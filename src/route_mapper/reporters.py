"""Formatos de salida (patrón Strategy).

Cada reporter sabe serializar un ``CrawlResult`` a un formato. Añadir uno nuevo
(por ejemplo, sitemap XML) no requiere tocar el crawler ni la CLI.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Protocol

from route_mapper.html_report import HtmlReporter
from route_mapper.models import CrawlResult


class Reporter(Protocol):
    extension: str

    def render(self, result: CrawlResult) -> str: ...


class TextReporter:
    extension = "txt"

    def render(self, result: CrawlResult) -> str:
        lines = [f"{p.status or 'ERR':>4}  {p.url}" for p in result.pages]
        return "\n".join(lines) + "\n"


class JsonReporter:
    extension = "json"

    def render(self, result: CrawlResult) -> str:
        payload = {
            "metadata": result.metadata.as_dict() if result.metadata else None,
            "summary": result.summary(),
            "pages": [p.as_dict() for p in result.pages],
        }
        return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


class CsvReporter:
    extension = "csv"

    def render(self, result: CrawlResult) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["url", "status", "outcome", "content_type", "depth", "referrer"])
        for p in result.pages:
            writer.writerow(
                [p.url, p.status or "", p.outcome.value, p.content_type, p.depth, p.referrer or ""]
            )
        return buffer.getvalue()


_REGISTRY: dict[str, Reporter] = {
    "txt": TextReporter(),
    "json": JsonReporter(),
    "csv": CsvReporter(),
    "html": HtmlReporter(),
}


def available_formats() -> list[str]:
    return sorted(_REGISTRY)


def get_reporter(name: str) -> Reporter:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ValueError(f"formato desconocido: {name!r}") from None
