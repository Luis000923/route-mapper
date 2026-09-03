"""Cumplimiento de robots.txt.

Envuelve ``urllib.robotparser`` y falla de forma abierta (permite) si el fichero
no se puede recuperar, que es el comportamiento recomendado por el estándar.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request
from urllib import robotparser
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# Límite de tiempo para descargar robots.txt. Evita que un objetivo hostil
# cuelgue el escáner con tarpitting (respuestas lentas que nunca terminan).
_DEFAULT_ROBOTS_TIMEOUT = 10.0


class RobotsPolicy:
    def __init__(
        self,
        *,
        user_agent: str,
        enabled: bool = True,
        timeout: float | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._enabled = enabled
        self._timeout = timeout if timeout and timeout > 0 else _DEFAULT_ROBOTS_TIMEOUT
        self._parsers: dict[str, robotparser.RobotFileParser | None] = {}

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
            # No usamos ``parser.read()`` porque abre la URL sin timeout.
            request = urllib.request.Request(robots_url, method="GET")  # noqa: S310
            with urllib.request.urlopen(  # noqa: S310
                request, timeout=self._timeout
            ) as response:
                raw = response.read().decode("utf-8", errors="replace")
            parser.parse(raw.splitlines())
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
