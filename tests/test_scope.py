from __future__ import annotations

from collections.abc import Callable

import pytest

from route_mapper.scope import ScopeEngine, ScopeViolation, SsrfViolation, is_blocked_ip
from route_mapper.url_utils import is_valid_subdomain


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "0.0.0.0",  # noqa: S104 - se prueba que se rechaza
        "10.0.0.1",
        "169.254.169.254",
        "192.168.1.1",
        "172.16.5.4",
        "100.64.0.1",  # CGNAT (RFC 6598)
        "100.127.255.254",  # CGNAT, extremo del rango
        "::1",
        "fe80::1",
        "224.0.0.1",
        "no-es-una-ip",
    ],
)
def test_is_blocked_ip_rejects_non_public(ip: str) -> None:
    assert is_blocked_ip(ip)


@pytest.mark.parametrize("ip", ["93.184.216.34", "8.8.8.8", "1.1.1.1"])
def test_is_blocked_ip_allows_public(ip: str) -> None:
    assert not is_blocked_ip(ip)


def test_is_blocked_ip_rejects_cgnat_all_python_versions() -> None:
    # RFC 6598: no lo marca is_private hasta Python 3.13; se comprueba aparte.
    assert is_blocked_ip("100.64.0.1")
    assert not is_blocked_ip("100.63.255.255")  # justo fuera del rango
    assert not is_blocked_ip("100.128.0.0")  # justo fuera del rango


@pytest.mark.parametrize(
    ("host", "root", "subs", "expected"),
    [
        ("example.com", "example.com", False, True),
        ("app.example.com", "app.example.com", True, True),
        ("api.app.example.com", "app.example.com", True, True),
        ("app.example.com", "example.com", False, False),
        ("blog.example.com", "example.com", True, True),
        # saltos laterales que una heurística de PSL fusionaría
        ("b.co.uk", "a.co.uk", True, False),
        ("evil-example.com", "example.com", True, False),
        ("example.com.evil.com", "example.com", True, False),
        ("example.com", "app.example.com", True, False),
    ],
)
def test_is_valid_subdomain_strict(host: str, root: str, subs: bool, expected: bool) -> None:
    assert is_valid_subdomain(host, root, include_subdomains=subs) is expected


def _resolver(mapping: dict[str, list[str]]) -> Callable[[str], list[str]]:
    def resolve(host: str) -> list[str]:
        return mapping[host]

    return resolve


def test_engine_rejects_internal_ip_before_connect() -> None:
    engine = ScopeEngine(
        "example.com",
        include_subdomains=False,
        resolver=_resolver({"example.com": ["127.0.0.1"]}),
    )
    with pytest.raises(SsrfViolation):
        engine.assert_ip_allowed("example.com")


def test_engine_in_scope_host_but_internal_ip_is_ssrf() -> None:
    engine = ScopeEngine(
        "example.com",
        include_subdomains=True,
        resolver=_resolver({"evil.example.com": ["169.254.169.254"]}),
    )
    with pytest.raises(SsrfViolation):
        engine.validate_url("http://evil.example.com/latest/meta-data")


def test_engine_public_ip_out_of_scope_is_scope_violation() -> None:
    engine = ScopeEngine(
        "example.com",
        include_subdomains=False,
        resolver=_resolver({"other.com": ["93.184.216.34"]}),
    )
    with pytest.raises(ScopeViolation):
        engine.validate_url("http://other.com/")


def test_engine_allows_public_in_scope() -> None:
    engine = ScopeEngine(
        "example.com",
        include_subdomains=False,
        resolver=_resolver({"example.com": ["93.184.216.34"]}),
    )
    assert engine.validate_url("https://example.com/x") == ["93.184.216.34"]
