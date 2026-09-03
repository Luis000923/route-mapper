"""Cumplimiento de robots.txt.

Envuelve ``urllib.robotparser`` y falla de forma abierta (permite) si el fichero
no se puede recuperar, que es el comportamiento recomendado por el estándar.
"""

from __future__ import annotations

import logging
import urllib.error
from http.client import HTTPMessage
from urllib import robotparser
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from route_mapper.scope import ScopeEngine, SsrfViolation

log = logging.getLogger(__name__)

# Límite de tiempo para descargar robots.txt. Evita que un objetivo hostil
# cuelgue el escáner con tarpitting (respuestas lentas que nunca terminan).
_DEFAULT_ROBOTS_TIMEOUT = 10.0

# Tope de bytes al leer robots.txt: evita un DoS por agotamiento de memoria si
# el servidor remoto responde con un fichero gigante.
_MAX_ROBOTS_BYTES = 512 * 1024  # 512 KiB

# Tope de saltos al descargar robots.txt. Cada Location se revalida contra el
# ScopeEngine antes de seguirla (defensa anti-SSRF vía redirect).
_MAX_ROBOTS_REDIRECTS = 3


class _NoRedirectHandler(HTTPRedirectHandler):
    """Neutraliza el seguimiento automático: todo ``3xx`` sale como ``HTTPError``."""

    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        return None


class RobotsPolicy:
    def __init__(
        self,
        *,
        user_agent: str,
        enabled: bool = True,
        timeout: float | None = None,
        scope: ScopeEngine | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._enabled = enabled
        self._timeout = timeout if timeout and timeout > 0 else _DEFAULT_ROBOTS_TIMEOUT
        self._scope = scope
        self._opener = build_opener(_NoRedirectHandler)
        self._parsers: dict[str, robotparser.RobotFileParser | None] = {}

    def _assert_ip_allowed(self, host: str) -> None:
        if self._scope is not None:
            self._scope.assert_ip_allowed(host)

    def _fetch_robots(self, robots_url: str) -> bytes:
        """Descarga ``robots.txt`` sin seguir redirects de forma automática.

        Cada ``Location`` de un ``3xx`` se revalida contra el ``ScopeEngine``
        (pre-flight DNS/IP) antes de seguirla. Lanza en caso de error de red,
        HTTP 4xx/5xx, redirect no permitido o exceso de saltos.
        """
        current = robots_url
        for _hop in range(_MAX_ROBOTS_REDIRECTS + 1):
            self._assert_ip_allowed(urlparse(current).hostname or "")
            request = Request(current, method="GET")  # noqa: S310
            try:
                with self._opener.open(request, timeout=self._timeout) as response:
                    return bytes(response.read(_MAX_ROBOTS_BYTES + 1))
            except urllib.error.HTTPError as exc:
                if not (300 <= exc.code < 400):
                    raise
                location = exc.headers.get("Location") if exc.headers else None
                if not location:
                    raise
                current = urljoin(current, location)
        raise urllib.error.URLError(
            f"demasiados redirects para robots.txt (>{_MAX_ROBOTS_REDIRECTS})"
        )

    def _parser_for(self, url: str) -> robotparser.RobotFileParser | None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._parsers:
            return self._parsers[origin]

        robots_url = f"{origin}/robots.txt"
        parser = robotparser.RobotFileParser()
        parser.set_url(robots_url)
        result: robotparser.RobotFileParser | None = parser

        try:
            # No usamos ``parser.read()``: no tiene timeout y sigue redirects
            # sin revalidar el destino contra el ScopeEngine.
            raw_bytes = self._fetch_robots(robots_url)
            if len(raw_bytes) > _MAX_ROBOTS_BYTES:
                log.warning(
                    "robots.txt de %s excede %d bytes: truncado",
                    origin,
                    _MAX_ROBOTS_BYTES,
                )
                raw_bytes = raw_bytes[:_MAX_ROBOTS_BYTES]
            raw = raw_bytes.decode("utf-8", errors="replace")
            parser.parse(raw.splitlines())
        except SsrfViolation as exc:
            # Host (o destino de un redirect) resuelve a IP no pública: se aborta
            # la descarga. Fail-open seguro: no se contacta la IP interna.
            log.warning(
                "robots.txt de %s abortado por política SSRF/scope: %s", origin, exc
            )
            result = None
        except urllib.error.HTTPError as exc:
            # 4xx/5xx: sin robots.txt utilizable; el parser vacío ya es fail-open.
            log.warning("robots.txt de %s devolvió HTTP %s", origin, exc.code)
            parser.parse([])
        except Exception as exc:
            log.warning("no se pudo leer robots.txt de %s: %s", origin, exc)
            result = None
        self._parsers[origin] = result
        return result

    def can_fetch(self, url: str) -> bool:
        if not self._enabled:
            return True
        parser = self._parser_for(url)
        if parser is None:
            return True
        return parser.can_fetch(self._user_agent, url)

    def crawl_delay(self, url: str) -> float | None:
        if not self._enabled:
            return None
        parser = self._parser_for(url)
        if parser is None:
            return None
        try:
            value = parser.crawl_delay(self._user_agent)
        except Exception:
            return None
        return float(value) if value is not None else None
