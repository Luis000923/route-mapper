"""Configuración centralizada del crawler.

Un único objeto de configuración validado se pasa a través de todas las capas,
lo que evita listas de parámetros largas y facilita añadir opciones nuevas.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

DEFAULT_USER_AGENT = (
    "route-mapper/1.0 (+https://github.com/Luis000923/route-mapper)"
)

#: Subcadenas que marcan un campo (o clave de cabecera) como potencialmente
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


def _is_sensitive(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _SENSITIVE_HINTS)


@dataclass(slots=True)
class CrawlConfig:
    start_url: str
    max_pages: int = 500
    max_depth: int | None = None
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

    def headers(self) -> dict[str, str]:
        return {"User-Agent": self.user_agent, **self.extra_headers}

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
