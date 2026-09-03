from __future__ import annotations

import logging
from dataclasses import fields
from pathlib import Path

import pytest

from route_mapper.cli import main
from route_mapper.config import CrawlConfig
from route_mapper.crawler import Crawler, InvalidStartUrl
from route_mapper.models import CrawlResult, FetchOutcome, PageRecord


def _fake_result() -> CrawlResult:
    r = CrawlResult(start_url="https://example.com/", domain="example.com")
    r.finished_at = r.started_at + 1
    r.pages.append(
        PageRecord(
            url="https://example.com/",
            status=200,
            content_type="text/html",
            outcome=FetchOutcome.OK,
            depth=0,
        )
    )
    return r


def test_main_writes_output_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Crawler, "run", lambda self: _fake_result())
    out = tmp_path / "routes.json"
    code = main(["https://example.com", "-f", "json", "-o", str(out), "-q"])
    assert code == 0
    assert "https://example.com/" in out.read_text()


def test_main_bad_url_is_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: Crawler) -> CrawlResult:
        raise InvalidStartUrl("nope")

    monkeypatch.setattr(Crawler, "run", boom)
    assert main(["not-a-url", "-q"]) == 2


def test_main_rejects_bad_config() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["https://example.com", "-m", "0"])
    assert exc.value.code == 2


def test_main_emits_summary_through_logger(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(Crawler, "run", lambda self: _fake_result())
    with caplog.at_level(logging.DEBUG, logger="route_mapper"):
        code = main(["https://example.com", "-f", "txt", "-o", "/dev/null"])
    assert code == 0
    messages = [r.getMessage() for r in caplog.records]
    assert any("1 rutas" in m for m in messages)


def test_main_progress_is_silenced_by_quiet(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def _run(self: Crawler) -> CrawlResult:
        r = _fake_result()
        if self._on_page is not None:
            self._on_page(r.pages[0])
        return r

    monkeypatch.setattr(Crawler, "run", _run)
    with caplog.at_level(logging.DEBUG, logger="route_mapper"):
        code = main(["https://example.com", "-o", "/dev/null", "-q"])
    assert code == 0
    assert not any("[0001]" in r.getMessage() for r in caplog.records)


def test_progress_hook_logs_each_page(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def _run(self: Crawler) -> CrawlResult:
        r = _fake_result()
        assert self._on_page is not None
        self._on_page(r.pages[0])
        return r

    monkeypatch.setattr(Crawler, "run", _run)
    with caplog.at_level(logging.DEBUG, logger="route_mapper"):
        main(["https://example.com", "-o", "/dev/null"])
    assert any("[0001]" in r.getMessage() for r in caplog.records)


def test_safe_dump_masks_sensitive_headers() -> None:
    config = CrawlConfig(
        start_url="https://example.com",
        extra_headers={"Authorization": "Bearer secret", "Accept": "text/html"},
    )
    dump = config.safe_dump()
    assert dump["extra_headers"]["Authorization"] == "***"
    assert dump["extra_headers"]["Accept"] == "text/html"
    assert set(dump) == {f.name for f in fields(config)}
