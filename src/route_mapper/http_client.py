"""Capa HTTP con reintentos, backoff y endurecimiento anti-SSRF.

Define un ``Protocol`` para que el crawler dependa de una abstracción y no de
``urllib``; en tests se inyecta un cliente falso.

Endurecimiento (Fase 3):

* **Pre-flight DNS:** antes de cada ``open`` se resuelve el hostname y se
  rechaza si apunta a una IP no pública (loopback, privada, link-local...).
* **Redirects manuales:** el ``OpenerDirector`` no sigue redirecciones
  automáticamente. Cada ``Location`` de un ``3xx`` se revalida contra el mismo
  motor de scope + SSRF antes de seguirla; si falla, la cadena se corta y la
  URL se marca como rota.
"""

from __future__ import annotations

import http.client
import logging
import socket
import ssl
import time
from http.client import HTTPMessage
from typing import Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import (
    BaseHandler,
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from route_mapper.config import CrawlConfig
from route_mapper.models import FetchOutcome, FetchResponse, HttpPostResult
from route_mapper.scope import ScopeEngine, ScopeViolation, SsrfViolation
from route_mapper.url_utils import normalize_url, resolve_link

log = logging.getLogger(__name__)

_MAX_BODY_BYTES = 5 * 1024 * 1024  # 5 MiB: evita descargar binarios enormes.


def _classify_network_error(exc: BaseException) -> str:
    """Traduce una excepción de red a una causa raíz legible para el analista."""
    if isinstance(exc, ssl.SSLError):
        return "SSL_ERROR"
    if isinstance(exc, socket.gaierror):
        return "DNS_RESOLUTION_ERROR"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "TIMEOUT"
    if isinstance(exc, ConnectionRefusedError):
        return "CONNECTION_REFUSED"
    if isinstance(exc, ConnectionResetError):
        return "CONNECTION_RESET"
    if isinstance(exc, (UnicodeEncodeError, UnicodeDecodeError, UnicodeError)):
        return "ENCODING_ERROR"
    if isinstance(exc, http.client.HTTPException):
        return "MALFORMED_RESPONSE"
    return "CONNECTION_ERROR"


class HttpClient(Protocol):
    def get(self, url: str) -> FetchResponse: ...


@runtime_checkable
class AuthHttpClient(Protocol):
    """Subconjunto de la capa HTTP que necesita el flujo de autenticación."""

    def post(
        self, url: str, *, data: bytes, content_type: str
    ) -> HttpPostResult: ...


class HttpAuthError(Exception):
    """Fallo de red o de protocolo durante una petición ``POST`` de login.

    El mensaje nunca incluye el cuerpo de la petición (que lleva credenciales).
    """


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


class UrllibHttpClient:
    """Implementación por defecto basada en la stdlib."""

    def __init__(self, config: CrawlConfig, *, scope: ScopeEngine) -> None:
        self._config = config
        self._scope = scope
        handlers: list[BaseHandler | type[BaseHandler]] = [_NoRedirectHandler]
        if config.proxy:
            # ``ProxyHandler`` canaliza http/https por el proxy indicado. La
            # revalidación anti-SSRF sobre el host destino (``assert_ip_allowed``)
            # se mantiene: nunca se delega la política de scope al proxy.
            handlers.append(
                ProxyHandler({"http": config.proxy, "https": config.proxy})
            )
        self._opener = build_opener(*handlers)

    def get(self, url: str) -> FetchResponse:
        redirects_left = self._config.max_redirects
        current = url

        while True:
            response, redirect_to = self._fetch(current, origin=url)
            if response is not None:
                return response
            if redirect_to is None:  # defensivo: no debería ocurrir
                return FetchResponse(
                    url=url,
                    outcome=FetchOutcome.CONNECTION_ERROR,
                    error="respuesta vacía sin destino de redirect",
                    error_detail="REDIRECT_ERROR",
                )

            if redirects_left <= 0:
                return FetchResponse(
                    url=url,
                    outcome=FetchOutcome.CONNECTION_ERROR,
                    error=f"demasiados redirects (>{self._config.max_redirects})",
                    error_detail="TOO_MANY_REDIRECTS",
                )
            try:
                self._scope.validate_url(redirect_to)
            except (ScopeViolation, SsrfViolation) as exc:
                return FetchResponse(
                    url=url,
                    outcome=FetchOutcome.CONNECTION_ERROR,
                    error=f"redirect bloqueado -> {redirect_to}: {exc}",
                    error_detail="REDIRECT_BLOCKED",
                )
            log.debug("redirect %s -> %s", current, redirect_to)
            redirects_left -= 1
            current = redirect_to

    def post(
        self, url: str, *, data: bytes, content_type: str
    ) -> HttpPostResult:
        """Envía un ``POST`` siguiendo redirecciones de forma segura.

        Cada salto ``3xx`` se revalida contra el motor de scope + SSRF y las
        cookies (``Set-Cookie``) de todos los saltos se acumulan. Nunca se
        reintenta ni se registra ``data`` (contiene credenciales).
        """
        cookies: list[str] = []
        current = normalize_url(url) or url
        method = "POST"
        payload: bytes | None = data
        headers = {**self._config.request_headers(), "Content-Type": content_type}
        redirects_left = self._config.max_redirects

        while True:
            host = urlparse(current).hostname or ""
            try:
                self._scope.assert_ip_allowed(host)
                request = Request(  # noqa: S310 - esquema validado en normalize_url
                    current, data=payload, headers=headers, method=method
                )
                with self._opener.open(
                    request, timeout=self._config.timeout
                ) as response:
                    body = response.read(_MAX_BODY_BYTES + 1)[:_MAX_BODY_BYTES]
                    cookies.extend(response.headers.get_all("Set-Cookie") or [])
                    return HttpPostResult(
                        status=response.status,
                        body=body,
                        set_cookie=tuple(cookies),
                    )
            except HTTPError as exc:
                cookies.extend(exc.headers.get_all("Set-Cookie") or [] if exc.headers else [])
                if not (300 <= exc.code < 400):
                    return HttpPostResult(
                        status=exc.code,
                        body=exc.read(_MAX_BODY_BYTES + 1)[:_MAX_BODY_BYTES],
                        set_cookie=tuple(cookies),
                    )
                location = exc.headers.get("Location") if exc.headers else None
                target = resolve_link(current, location) if location else None
                if target is None:
                    raise HttpAuthError(
                        f"redirect {exc.code} de login a Location no navegable"
                    ) from None
                if redirects_left <= 0:
                    raise HttpAuthError(
                        f"demasiados redirects de login (>{self._config.max_redirects})"
                    ) from None
                try:
                    self._scope.validate_url(target)
                except (ScopeViolation, SsrfViolation) as scope_exc:
                    raise HttpAuthError(
                        f"redirect de login bloqueado -> {target}: {scope_exc}"
                    ) from None
                redirects_left -= 1
                # Tras un 3xx la petición pasa a ser un GET sin cuerpo.
                current = target
                method = "GET"
                payload = None
                headers = {
                    k: v for k, v in headers.items() if k.lower() != "content-type"
                }
            except SsrfViolation as exc:
                raise HttpAuthError(f"SSRF bloqueado en login: {exc}") from None
            except (URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
                root = getattr(exc, "reason", None)
                probe = root if isinstance(root, BaseException) else exc
                detail = _classify_network_error(probe)
                raise HttpAuthError(f"fallo de red en login: {detail}") from None

    # -- internos ------------------------------------------------------------

    def _fetch(
        self, url: str, *, origin: str
    ) -> tuple[FetchResponse | None, str | None]:
        """Una petición sin seguir redirects.

        Devuelve ``(FetchResponse, None)`` para una respuesta final, o
        ``(None, destino)`` si hubo un ``3xx`` con ``Location`` navegable.
        """
        attempts = self._config.retries + 1
        last_error: str | None = None
        last_detail: str | None = None
        host = urlparse(url).hostname or ""
        # Percent-encoding / IDN: ``urllib`` necesita bytes ASCII, no Unicode crudo.
        request_url = normalize_url(url) or url

        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            try:
                # Pre-flight DNS: corta antes de abrir el socket.
                self._scope.assert_ip_allowed(host)

                request = Request(  # noqa: S310 - esquema validado en normalize_url
                    request_url,
                    headers=self._config.request_headers(),
                    method="GET",
                )
                with self._opener.open(request, timeout=self._config.timeout) as response:
                    body = response.read(_MAX_BODY_BYTES + 1)
                    if len(body) > _MAX_BODY_BYTES:
                        body = body[:_MAX_BODY_BYTES]
                    return (
                        FetchResponse(
                            url=url,
                            outcome=FetchOutcome.OK,
                            status=response.status,
                            content_type=response.headers.get("Content-Type", ""),
                            charset=response.headers.get_content_charset(),
                            body=body,
                            elapsed=time.monotonic() - started,
                        ),
                        None,
                    )
            except SsrfViolation as exc:
                # IP interna: no tiene sentido reintentar.
                return (
                    FetchResponse(
                        url=origin,
                        outcome=FetchOutcome.CONNECTION_ERROR,
                        elapsed=time.monotonic() - started,
                        error=f"SSRF bloqueado: {exc}",
                        error_detail="SSRF_BLOCKED",
                    ),
                    None,
                )
            except HTTPError as exc:
                if 300 <= exc.code < 400:
                    location = exc.headers.get("Location") if exc.headers else None
                    target = resolve_link(url, location) if location else None
                    if target is None:
                        return (
                            FetchResponse(
                                url=origin,
                                outcome=FetchOutcome.CONNECTION_ERROR,
                                status=exc.code,
                                elapsed=time.monotonic() - started,
                                error=f"redirect {exc.code} a Location no navegable",
                            ),
                            None,
                        )
                    return (None, target)
                # 4xx/5xx: se registra pero no se reintenta salvo 429/503.
                if exc.code not in (429, 503) or attempt == attempts:
                    return (
                        FetchResponse(
                            url=url,
                            outcome=FetchOutcome.HTTP_ERROR,
                            status=exc.code,
                            content_type=exc.headers.get("Content-Type", "")
                            if exc.headers
                            else "",
                            elapsed=time.monotonic() - started,
                            error=f"HTTP {exc.code}",
                        ),
                        None,
                    )
                last_error = f"HTTP {exc.code}"
            except (UnicodeEncodeError, UnicodeDecodeError) as exc:
                last_error = str(exc) or type(exc).__name__
                last_detail = "ENCODING_ERROR"
            except (
                URLError,
                TimeoutError,
                OSError,
                http.client.HTTPException,
                UnicodeError,
            ) as exc:
                # Incluye respuestas HTTP corruptas (línea de estado malformada,
                # cabeceras ilegales) y errores de codificación de la URL/host.
                # ``URLError`` envuelve la excepción real en ``.reason``.
                root = getattr(exc, "reason", None)
                probe = root if isinstance(root, BaseException) else exc
                last_detail = _classify_network_error(probe)
                last_error = str(root or exc) or type(exc).__name__

            if attempt < attempts:
                backoff = self._config.retry_backoff * (2 ** (attempt - 1))
                log.debug("reintento %d/%d para %s en %.2fs", attempt, attempts, url, backoff)
                time.sleep(backoff)

        return (
            FetchResponse(
                url=origin,
                outcome=FetchOutcome.CONNECTION_ERROR,
                error=last_error,
                error_detail=last_detail,
            ),
            None,
        )
