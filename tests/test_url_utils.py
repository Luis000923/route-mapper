from __future__ import annotations

import pytest

from route_mapper.url_utils import in_scope, normalize_url, resolve_link


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://Example.com", "https://example.com/"),
        ("https://example.com/path/", "https://example.com/path"),
        ("https://example.com/a#section", "https://example.com/a"),
        ("https://example.com:443/a", "https://example.com/a"),
        ("http://example.com:8080/a", "http://example.com:8080/a"),
        ("ftp://example.com/a", None),
        ("javascript:void(0)", None),
        ("", None),
    ],
)
def test_normalize_url(raw: str, expected: str | None) -> None:
    assert normalize_url(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://example.com/recursos-didácticos",
            "https://example.com/recursos-did%C3%A1cticos",
        ),
        (
            "https://example.com/misión-y-visión",
            "https://example.com/misi%C3%B3n-y-visi%C3%B3n",
        ),
        # No se re-codifica un %XX ya presente (idempotencia).
        (
            "https://example.com/recursos-did%C3%A1cticos",
            "https://example.com/recursos-did%C3%A1cticos",
        ),
        # Host IDN -> Punycode.
        ("https://mañana.example/", "https://xn--maana-pta.example/"),
        # CRLF en la URL: se rechaza (anti header-splitting).
        ("https://example.com/a%0d%0aX:1", "https://example.com/a%0D%0AX:1"),
        ("https://example.com/a\r\nX:1", None),
    ],
)
def test_normalize_url_percent_encodes_non_ascii(raw: str, expected: str | None) -> None:
    assert normalize_url(raw) == expected


def test_normalize_url_sorts_query_params() -> None:
    a = normalize_url("https://example.com/p?b=2&a=1")
    b = normalize_url("https://example.com/p?a=1&b=2")
    assert a == b == "https://example.com/p?a=1&b=2"


def test_normalize_url_collapses_identical_duplicate_params() -> None:
    assert (
        normalize_url("https://example.com/p?a=1&a=1&b=2")
        == "https://example.com/p?a=1&b=2"
    )
    # Duplicados con valores distintos se conservan (orden estable).
    assert (
        normalize_url("https://example.com/p?a=2&a=1")
        == "https://example.com/p?a=1&a=2"
    )


def test_resolve_link_relative() -> None:
    assert resolve_link("https://example.com/dir/page", "../other") == "https://example.com/other"


def test_in_scope_same_host() -> None:
    assert in_scope("https://example.com/x", "example.com", include_subdomains=False)


def test_in_scope_subdomain_toggle() -> None:
    url = "https://blog.example.com/x"
    assert not in_scope(url, "example.com", include_subdomains=False)
    assert in_scope(url, "example.com", include_subdomains=True)
