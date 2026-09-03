"""Proxy, rotación de User-Agent y jitter de temporización."""

from __future__ import annotations

from pathlib import Path

import pytest

from route_mapper.config import CrawlConfig
from route_mapper.crawler import Crawler
from route_mapper.http_client import UrllibHttpClient
from route_mapper.scope import ScopeEngine
from test_crawler import AllowAllRobots, FakeHttpClient


def _scope(host: str = "example.com") -> ScopeEngine:
    return ScopeEngine(host, include_subdomains=False)


def test_proxy_handler_is_installed_in_opener() -> None:
    config = CrawlConfig(
        start_url="https://example.com", proxy="http://127.0.0.1:8080", retries=0
    )
    client = UrllibHttpClient(config, scope=_scope())

    proxies = client._opener.handlers  # type: ignore[attr-defined]
    assert any(type(h).__name__ == "ProxyHandler" for h in proxies)


def test_no_proxy_handler_by_default() -> None:
    config = CrawlConfig(start_url="https://example.com", retries=0)
    client = UrllibHttpClient(config, scope=_scope())
    assert not any(
        type(h).__name__ == "ProxyHandler"
        for h in client._opener.handlers  # type: ignore[attr-defined]
    )


def test_invalid_proxy_scheme_is_rejected() -> None:
    with pytest.raises(ValueError, match="proxy debe ser"):
        CrawlConfig(start_url="https://example.com", proxy="ftp://x:21")


def test_user_agent_rotates_across_requests() -> None:
    agents = tuple(f"UA-{i}" for i in range(8))
    config = CrawlConfig(start_url="https://example.com", user_agents=agents)

    seen = {config.request_headers()["User-Agent"] for _ in range(200)}

    assert seen == set(agents)


def test_rotation_never_overrides_explicit_user_agent_header() -> None:
    config = CrawlConfig(
        start_url="https://example.com",
        user_agents=("UA-1", "UA-2"),
        extra_headers={"User-Agent": "Pinned/1.0"},
    )
    assert config.request_headers()["User-Agent"] == "Pinned/1.0"


def test_request_headers_without_list_uses_default_agent() -> None:
    config = CrawlConfig(start_url="https://example.com")
    assert config.request_headers()["User-Agent"] == config.user_agent


def test_get_request_carries_a_rotated_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    config = CrawlConfig(
        start_url="https://example.com",
        user_agents=("Alpha/1", "Beta/2"),
        retries=0,
    )
    client = UrllibHttpClient(config, scope=_scope())
    monkeypatch.setattr(client._scope, "assert_ip_allowed", lambda host: None)

    agents: list[str] = []

    def _capture(request: object, timeout: object = None) -> object:
        agents.append(request.get_header("User-agent"))  # type: ignore[attr-defined]
        raise OSError("stop")

    monkeypatch.setattr(client._opener, "open", _capture)
    for _ in range(30):
        client.get("https://example.com/x")

    assert set(agents) <= {"Alpha/1", "Beta/2"}
    assert len(set(agents)) == 2


def test_empty_ua_entry_is_rejected() -> None:
    with pytest.raises(ValueError, match="user_agents"):
        CrawlConfig(start_url="https://example.com", user_agents=("ok", "  "))


def test_negative_jitter_is_rejected() -> None:
    with pytest.raises(ValueError, match="jitter"):
        CrawlConfig(start_url="https://example.com", jitter=-0.1)


def test_jitter_varies_effective_delay_within_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("route_mapper.crawler.time.sleep", sleeps.append)

    config = CrawlConfig(start_url="https://example.com", delay=1.0, jitter=0.4)
    crawler = Crawler(
        config, http_client=FakeHttpClient(), robots_policy=AllowAllRobots()
    )
    crawler.run()

    assert sleeps
    assert all(0.6 <= s <= 1.4 for s in sleeps)
    assert len(set(sleeps)) > 1  # de verdad varía, no es constante


def test_jitter_never_produces_negative_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("route_mapper.crawler.time.sleep", sleeps.append)

    config = CrawlConfig(start_url="https://example.com", delay=0.1, jitter=5.0)
    crawler = Crawler(
        config, http_client=FakeHttpClient(), robots_policy=AllowAllRobots()
    )
    crawler.run()

    assert all(s >= 0.0 for s in sleeps)


def test_safe_dump_redacts_proxy_credentials() -> None:
    config = CrawlConfig(
        start_url="https://example.com",
        proxy="http://alice:s3cr3t@127.0.0.1:8080",
    )
    assert config.safe_dump()["proxy"] == "http://***@127.0.0.1:8080"


def test_ua_file_is_loaded_and_trimmed(tmp_path: Path) -> None:
    from route_mapper.cli import _load_user_agents

    f = tmp_path / "ua.txt"
    f.write_text("# comentario\nMozilla/5.0 A\n\n  Mozilla/5.0 B  \n")
    assert _load_user_agents(f) == ("Mozilla/5.0 A", "Mozilla/5.0 B")


def test_empty_ua_file_raises(tmp_path: Path) -> None:
    from route_mapper.cli import _load_user_agents

    f = tmp_path / "ua.txt"
    f.write_text("# solo comentarios\n\n")
    with pytest.raises(ValueError, match="ningún User-Agent"):
        _load_user_agents(f)
