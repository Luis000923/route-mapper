"""Configuración centralizada del crawler.

Un único objeto de configuración validado se pasa a través de todas las capas,
lo que evita listas de parámetros largas y facilita añadir opciones nuevas.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from route_mapper.auth import AuthConfig

DEFAULT_USER_AGENT = (
    "route-mapper/1.0 (+https://github.com/Luis000923/route-mapper)"
)

#: Subcadenas que marcan un campo (o clave dsole cabecera) como potencialmente
#: sensible. El volcado para reportes las enmascara en lugar de serializarlas.
_SENSITIVE_HINTS = (
    "authorization",
    "token",
    "secret",
    "password",
    "passwd",
    "cookie",
    "api-key",
    "api_key",
    "apikey",
    "credential",
)

_REDACTED = "***"


def _redact_userinfo(url: str) -> str:
    """Sustituye cualquier ``user:pass@`` de una URL por ``***@``."""
    scheme, sep, rest = url.partition("://")
    if not sep or "@" not in rest:
        return url
    _, _, host = rest.rpartition("@")
    return f"{scheme}://{_REDACTED}@{host}"


def _is_sensitive(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _SENSITIVE_HINTS)


@dataclass(slots=True)
class CrawlConfig:
    start_url: str
    max_pages: int = 500
    max_depth: int | None = None
    #: Pausa mínima de cortesía, en segundos, que el crawler respeta entre lotes
    #: de peticiones (un "lote" es hasta ``concurrency`` URLs procesadas en
    #: paralelo). Con ``concurrency > 1`` la pausa se aplica una vez por lote, no
    #: por petición, de modo que el rate efectivo puede llegar a
    #: ``concurrency / delay`` peticiones por segundo. En cada lote se toma el
    #: máximo entre este valor y el ``Crawl-delay`` declarado en ``robots.txt``
    #: (salvo que ``respect_robots`` esté desactivado).
    delay: float = 0.2
    timeout: float = 10.0
    concurrency: int = 1
    retries: int = 2
    retry_backoff: float = 0.5
    max_redirects: int = 5
    respect_robots: bool = True
    user_agent: str = DEFAULT_USER_AGENT
    allowed_content_types: tuple[str, ...] = ("text/html",)
    include_subdomains: bool = False
    max_queued_urls: int = 10000
    max_links_per_page: int = 1000
    global_timeout: float = 300.0
    extra_headers: dict[str, str] = field(default_factory=dict)
    #: Cadena de conexión a un proxy HTTP(S) (p. ej. ``http://127.0.0.1:8080``).
    #: Todo el tráfico del crawl se canaliza por él; la validación anti-SSRF
    #: sobre el host destino se mantiene intacta.
    proxy: str | None = None
    #: Lista de User-Agents para rotación aleatoria por petición. Si está vacía
    #: se usa siempre ``user_agent``.
    user_agents: tuple[str, ...] = ()
    #: Variación aleatoria en segundos que se suma/resta a la pausa entre lotes
    #: (``delay ± uniform(0, jitter)``) para difuminar el patrón de tráfico.
    jitter: float = 0.0
    #: Si es ``True`` el crawler intenta sembrar la cola con las URLs de
    #: ``/sitemap.xml`` antes de empezar.
    sitemap: bool = False
    #: Si es ``True`` se extraen endpoints de las respuestas JavaScript y se
    #: añaden a la frontera de crawling.
    parse_js: bool = True
    #: Autenticación previa opcional. Si está presente, el crawler ejecuta el
    #: login antes de la URL semilla e inyecta la sesión en ``extra_headers``.
    auth: AuthConfig | None = None

    def __post_init__(self) -> None:
        if self.max_pages <= 0:
            raise ValueError("max_pages debe ser > 0")
        if self.max_depth is not None and self.max_depth < 0:
            raise ValueError("max_depth no puede ser negativo")
        if self.delay < 0:
            raise ValueError("delay no puede ser negativo")
        if self.timeout <= 0:
            raise ValueError("timeout debe ser > 0")
        if self.concurrency < 1:
            raise ValueError("concurrency debe ser >= 1")
        if self.retries < 0:
            raise ValueError("retries no puede ser negativo")
        if self.max_redirects < 0:
            raise ValueError("max_redirects no puede ser negativo")
        if self.max_queued_urls <= 0:
            raise ValueError("max_queued_urls debe ser > 0")
        if self.max_links_per_page <= 0:
            raise ValueError("max_links_per_page debe ser > 0")
        if self.global_timeout <= 0:
            raise ValueError("global_timeout debe ser > 0")
        if self.jitter < 0:
            raise ValueError("jitter no puede ser negativo")
        if self.proxy is not None:
            scheme = self.proxy.split("://", 1)[0].lower() if "://" in self.proxy else ""
            if scheme not in ("http", "https", "socks5", "socks5h"):
                raise ValueError(
                    "proxy debe ser una URL http(s):// o socks5(h)://"
                )
        if any(not ua.strip() for ua in self.user_agents):
            raise ValueError("user_agents no puede contener entradas vacías")

    def headers(self) -> dict[str, str]:
        return {"User-Agent": self.user_agent, **self.extra_headers}

    def request_headers(self) -> dict[str, str]:
        """Cabeceras para una petición concreta, con User-Agent rotado.

        Si se configuró ``user_agents`` se elige uno al azar por llamada; en
        caso contrario es equivalente a :meth:`headers`. Una cabecera
        ``User-Agent`` explícita en ``extra_headers`` siempre tiene prioridad.
        """
        headers = self.headers()
        if self.user_agents and not any(
            k.lower() == "user-agent" for k in self.extra_headers
        ):
            headers["User-Agent"] = random.choice(self.user_agents)  # noqa: S311 - ofuscación, no cripto
        return headers

    def safe_dump(self) -> dict[str, Any]:
        """Volcado serializable de la configuración para los metadatos de un reporte.

        Se limita estrictamente a los campos declarados en ``CrawlConfig`` (nunca
        variables de entorno ni estado del sistema) y enmascara cualquier campo o
        cabecera cuyo nombre sugiera un valor sensible (p. ej. cabeceras de
        autorización que puedan añadirse en el futuro).
        """
        dump: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if _is_sensitive(f.name):
                dump[f.name] = _REDACTED
            elif f.name == "proxy" and isinstance(value, str):
                # Nunca serializamos user:pass@ embebidos en la URL del proxy.
                dump[f.name] = _redact_userinfo(value)
            elif hasattr(value, "safe_dump"):
                dump[f.name] = value.safe_dump()
            elif isinstance(value, dict):
                dump[f.name] = {
                    k: (_REDACTED if _is_sensitive(str(k)) else v)
                    for k, v in value.items()
                }
            elif isinstance(value, tuple):
                dump[f.name] = list(value)
            else:
                dump[f.name] = value
        return dump
