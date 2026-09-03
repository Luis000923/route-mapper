"""Modelos de dominio inmutables usados en todo el crawler."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import urlsplit


class FetchOutcome(str, Enum):
    """Resultado de alto nivel de una descarga."""

    OK = "ok"
    HTTP_ERROR = "http_error"
    CONNECTION_ERROR = "connection_error"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class FetchResponse:
    """Respuesta normalizada de la capa HTTP."""

    url: str
    outcome: FetchOutcome
    status: int | None = None
    content_type: str = ""
    charset: str | None = None
    body: bytes = b""
    elapsed: float = 0.0
    error: str | None = None
    #: Causa raíz legible (``DNS_RESOLUTION_ERROR``, ``TIMEOUT``, ``SSL_ERROR``,
    #: ``ENCODING_ERROR``...) cuando ``outcome`` es ``CONNECTION_ERROR``.
    error_detail: str | None = None

    @property
    def is_html(self) -> bool:
        return "text/html" in self.content_type.lower()

    @property
    def is_javascript(self) -> bool:
        """``True`` si la respuesta parece código JavaScript.

        Se basa en el ``Content-Type`` (``application/javascript``,
        ``text/javascript``...) o, en su defecto, en la extensión ``.js`` del
        path de la URL.
        """
        ctype = self.content_type.lower()
        if "javascript" in ctype or "ecmascript" in ctype:
            return True
        path = urlsplit(self.url).path.lower()
        return path.endswith(".js")


@dataclass(frozen=True, slots=True)
class HttpPostResult:
    """Resultado normalizado de un ``POST`` (usado por el flujo de autenticación).

    ``set_cookie`` acumula todas las cabeceras ``Set-Cookie`` vistas a lo largo de
    la cadena de redirecciones, no solo las de la respuesta final.
    """

    status: int
    body: bytes = b""
    set_cookie: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PageRecord:
    """Una ruta descubierta y su estado."""

    url: str
    status: int | None
    content_type: str
    outcome: FetchOutcome
    depth: int
    referrer: str | None = None
    elapsed: float = 0.0
    error: str | None = None
    #: Causa raíz específica del fallo de red (ver ``FetchResponse.error_detail``).
    error_detail: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "status": self.status,
            "content_type": self.content_type,
            "outcome": self.outcome.value,
            "depth": self.depth,
            "referrer": self.referrer,
            "elapsed_ms": round(self.elapsed * 1000, 1),
            "error": self.error,
            "error_detail": self.error_detail,
        }


@dataclass(slots=True)
class ExecutionMetadata:
    """Contexto reproducible de una ejecución del crawler.

    Guarda una copia estática de los parámetros usados, la versión de la
    herramienta y las marcas de tiempo en ISO 8601, de modo que otro analista
    pueda reconstruir bajo qué condiciones se obtuvo la topología.
    """

    tool_version: str
    started_at: str
    finished_at: str | None = None
    #: Volcado seguro (ya redaccionado) de ``CrawlConfig``.
    config: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_version": self.tool_version,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "config": dict(self.config),
        }


@dataclass(slots=True)
class CrawlResult:
    """Agregado devuelto al final de un crawl."""

    start_url: str
    domain: str
    pages: list[PageRecord] = field(default_factory=list)
    #: Enlaces dirigidos (origen -> destino) descubiertos dentro del ámbito.
    edges: set[tuple[str, str]] = field(default_factory=set)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    #: Metadatos de ejecución para reportes auditables (se puebla al finalizar).
    metadata: ExecutionMetadata | None = None

    @property
    def duration(self) -> float:
        return (self.finished_at or time.time()) - self.started_at

    @property
    def ok_pages(self) -> list[PageRecord]:
        return [p for p in self.pages if p.outcome is FetchOutcome.OK]

    @property
    def broken_pages(self) -> list[PageRecord]:
        return [
            p
            for p in self.pages
            if p.outcome in (FetchOutcome.HTTP_ERROR, FetchOutcome.CONNECTION_ERROR)
        ]

    def summary(self) -> dict[str, object]:
        return {
            "start_url": self.start_url,
            "domain": self.domain,
            "total": len(self.pages),
            "ok": len(self.ok_pages),
            "broken": len(self.broken_pages),
            "edges": len(self.edges),
            "duration_s": round(self.duration, 2),
        }
