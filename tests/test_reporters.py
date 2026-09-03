from __future__ import annotations

import json

import pytest

from route_mapper.models import (
    CrawlResult,
    ExecutionMetadata,
    FetchOutcome,
    PageRecord,
)
from route_mapper.reporters import available_formats, get_reporter


def _metadata() -> ExecutionMetadata:
    return ExecutionMetadata(
        tool_version="9.9.9",
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:05+00:00",
        config={"max_pages": 123, "timeout": 10.0, "max_depth": 2},
    )


def _result() -> CrawlResult:
    r = CrawlResult(start_url="https://example.com/", domain="example.com")
    r.pages.append(
        PageRecord(
            url="https://example.com/",
            status=200,
            content_type="text/html",
            outcome=FetchOutcome.OK,
            depth=0,
        )
    )
    r.pages.append(
        PageRecord(
            url="https://example.com/missing",
            status=404,
            content_type="",
            outcome=FetchOutcome.HTTP_ERROR,
            depth=1,
            referrer="https://example.com/",
        )
    )
    return r


def test_available_formats() -> None:
    assert set(available_formats()) == {"txt", "json", "csv", "html"}


def test_text_reporter() -> None:
    out = get_reporter("txt").render(_result())
    assert "200  https://example.com/" in out
    assert " 404  https://example.com/missing" in out


def test_json_reporter_roundtrip() -> None:
    payload = json.loads(get_reporter("json").render(_result()))
    assert payload["summary"]["total"] == 2
    assert payload["summary"]["broken"] == 1
    assert payload["pages"][1]["status"] == 404


def test_json_reporter_includes_execution_metadata() -> None:
    r = _result()
    r.metadata = _metadata()
    payload = json.loads(get_reporter("json").render(r))
    assert "metadata" in payload
    assert payload["metadata"]["tool_version"] == "9.9.9"
    assert payload["metadata"]["started_at"] == "2026-01-01T00:00:00+00:00"
    # Los límites impuestos quedan auditables en el reporte.
    assert payload["metadata"]["config"]["max_pages"] == 123
    assert payload["metadata"]["config"]["timeout"] == 10.0


def test_json_reporter_metadata_is_null_without_execution_context() -> None:
    payload = json.loads(get_reporter("json").render(_result()))
    assert payload["metadata"] is None


def test_html_reporter_shows_scan_parameters() -> None:
    r = _result()
    r.edges.add(("https://example.com/", "https://example.com/missing"))
    r.metadata = _metadata()
    out = get_reporter("html").render(r)
    assert "Parámetros del escaneo" in out
    assert "max_pages" in out and "123" in out
    assert "9.9.9" in out


def test_csv_reporter_has_header_and_rows() -> None:
    out = get_reporter("csv").render(_result()).splitlines()
    assert out[0].startswith("url,status,outcome")
    assert len(out) == 3


def test_html_reporter_is_self_contained() -> None:
    r = _result()
    r.edges.add(("https://example.com/", "https://example.com/missing"))
    out = get_reporter("html").render(r)
    assert out.startswith("<!doctype html>")
    assert "<script src=" not in out and "cdn" not in out.lower()
    assert '"nodes"' in out and '"links"' in out
    assert "example.com/missing" in out


def test_html_reporter_escapes_script_breakout_payload() -> None:
    payload = "</script><script>alert(1)</script>"
    r = CrawlResult(start_url="https://example.com/", domain="example.com")
    r.pages.append(
        PageRecord(
            url=f"https://example.com/{payload}",
            status=200,
            content_type="text/html",
            outcome=FetchOutcome.OK,
            depth=0,
        )
    )
    out = get_reporter("html").render(r)

    # El payload no puede aparecer literal: rompería <script type="application/json">.
    assert payload not in out
    assert "<script>alert(1)</script>" not in out
    # Los caracteres de control HTML del JSON quedan escapados como \uXXXX.
    assert "\\u003c/script\\u003e" in out


def test_html_reporter_renders_node_url_without_innerhtml() -> None:
    from route_mapper.html_report import _SCRIPT

    # Defensa en profundidad: la URL de un nodo nunca se interpola en innerHTML.
    assert "tip.innerHTML" not in _SCRIPT
    assert "urlLine.textContent = n.url" in _SCRIPT
    # No queda ninguna asignación `innerHTML` que contenga `n.url`.
    for line in _SCRIPT.splitlines():
        if "innerHTML" in line:
            assert "n.url" not in line


def test_unknown_format() -> None:
    with pytest.raises(ValueError):
        get_reporter("xml")
