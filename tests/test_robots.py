from __future__ import annotations

import io
import urllib.error
from typing import Any

from route_mapper.robots import _MAX_ROBOTS_BYTES, RobotsPolicy
from route_mapper.scope import ScopeEngine


def _scope(mapping: dict[str, list[str]]) -> ScopeEngine:
    return ScopeEngine(
        "example.com",
        include_subdomains=True,
        resolver=lambda host: list(mapping.get(host, ["93.184.216.34"])),
    )


class _FakeResponse(io.BytesIO):
    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _FakeOpener:
    """Sustituye a ``build_opener`` para no tocar la red en tests."""

    def __init__(self, handler: Any) -> None:
        self.opened: list[str] = []
        self._handler = handler

    def open(self, request: Any, timeout: float | None = None) -> _FakeResponse:
        url = request.full_url
        self.opened.append(url)
        return self._handler(url)


def _policy(handler: Any, scope: ScopeEngine) -> tuple[RobotsPolicy, _FakeOpener]:
    policy = RobotsPolicy(user_agent="test", scope=scope)
    opener = _FakeOpener(handler)
    policy._opener = opener  # type: ignore[assignment]
    return policy, opener


def test_robots_ssrf_preflight_blocks_private_ip() -> None:
    def handler(url: str) -> _FakeResponse:
        raise AssertionError("no debe abrirse el socket")

    scope = _scope({"internal.example.com": ["169.254.169.254"]})
    policy, opener = _policy(handler, scope)

    assert policy.can_fetch("https://internal.example.com/secret") is True
    assert opener.opened == []


def test_robots_redirect_to_internal_ip_is_blocked() -> None:
    hop2 = "http://169.254.169.254/latest/meta-data/"

    def handler(url: str) -> _FakeResponse:
        if url == "https://example.com/robots.txt":
            raise urllib.error.HTTPError(
                url, 302, "Found", {"Location": hop2}, None  # type: ignore[arg-type]
            )
        raise AssertionError(f"segundo salto no debe ejecutarse: {url}")

    scope = _scope({"example.com": ["93.184.216.34"], "169.254.169.254": ["169.254.169.254"]})
    policy, opener = _policy(handler, scope)

    assert policy.can_fetch("https://example.com/anything") is True
    assert opener.opened == ["https://example.com/robots.txt"]


def test_robots_body_is_truncated() -> None:
    huge = b"# c\n" * (_MAX_ROBOTS_BYTES // 3)
    captured: dict[str, int] = {}

    class _Capturing(_FakeResponse):
        def read(self, size: int | None = -1, /) -> bytes:
            captured["amt"] = size if size is not None else -1
            return super().read(size)

    def handler(url: str) -> _FakeResponse:
        return _Capturing(huge)

    policy, _ = _policy(handler, _scope({}))
    assert policy.can_fetch("https://example.com/") is True
    assert captured["amt"] == _MAX_ROBOTS_BYTES + 1


def test_robots_normal_flow_public_host() -> None:
    def handler(url: str) -> _FakeResponse:
        return _FakeResponse(b"User-agent: *\nDisallow: /private\n")

    policy, opener = _policy(handler, _scope({}))
    assert policy.can_fetch("https://example.com/public") is True
    assert policy.can_fetch("https://example.com/private") is False
    assert opener.opened == ["https://example.com/robots.txt"]
