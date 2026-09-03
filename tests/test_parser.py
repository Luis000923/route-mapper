from __future__ import annotations

from route_mapper.parser import (
    extract_js_endpoints,
    extract_links,
    parse_sitemap,
)

HTML = b"""
<html><body>
  <a href="/a">a</a>
  <a href="a">dup relativa</a>
  <a href="https://other.com/x">externa</a>
  <a href="/b" rel="nofollow">no seguir</a>
  <a href="mailto:x@example.com">correo</a>
  <area href="/c">
</body></html>
"""


def test_extract_links_basic() -> None:
    links = extract_links("https://example.com/", HTML)
    assert "https://example.com/a" in links
    assert "https://example.com/c" in links
    assert "https://other.com/x" in links


def test_extract_links_dedupes_and_skips() -> None:
    links = extract_links("https://example.com/", HTML)
    assert links.count("https://example.com/a") == 1
    assert "https://example.com/b" not in links
    assert all(not link.startswith("mailto:") for link in links)


def test_extract_links_handles_broken_html() -> None:
    assert extract_links("https://example.com/", b"<a href='/x'>unclosed") == [
        "https://example.com/x"
    ]


def test_base_href_changes_relative_resolution() -> None:
    html = b"""
    <html><head><base href="https://example.com/sub/"></head><body>
      <a href="page.html">rel</a>
    </body></html>
    """
    links = extract_links("https://example.com/other/index.html", html)
    assert links == ["https://example.com/sub/page.html"]


def test_extract_links_expands_surface_tags() -> None:
    html = b"""
    <html><head>
      <link rel="stylesheet" href="/style.css">
      <script src="/app.js"></script>
    </head><body>
      <form action="/submit"></form>
    </body></html>
    """
    links = extract_links("https://example.com/", html)
    assert "https://example.com/style.css" in links
    assert "https://example.com/app.js" in links
    assert "https://example.com/submit" in links


def test_extract_links_decodes_declared_charset() -> None:
    # "ñ" en ISO-8859-1 es el byte 0xF1, inválido como UTF-8.
    html = "<a href='/ñ'>latin1</a>".encode("iso-8859-1")
    links = extract_links("https://example.com/", html, encoding="iso-8859-1")
    # Se decodifica como "ñ" (U+00F1) y luego se percent-encodea para la red.
    assert links == ["https://example.com/%C3%B1"]

    # Decodificado como UTF-8 el mismo byte produce mojibake (U+FFFD).
    mojibake = extract_links("https://example.com/", html, encoding="utf-8")
    assert mojibake == ["https://example.com/%EF%BF%BD"]


def test_base_href_still_subject_to_normalization() -> None:
    html = b"""
    <base href="https://EXAMPLE.com:443/x/">
    <a href="./y?b=2&a=1">q</a>
    """
    links = extract_links("https://example.com/", html)
    assert links == ["https://example.com/x/y?a=1&b=2"]


# --- JS endpoint miner -------------------------------------------------------

JS = """
const API = "/api/v2/users";
fetch('/dashboard').then(r => r.json());
const admin = `/admin/settings`;
let x = a / b / c;                 // división, no una ruta
import logo from "/static/logo.png";   // recurso estático: se ignora
const proto = "//cdn.example.com/x";   // protocol-relative: se ignora
const rel = "relative/path";           // sin barra inicial: se ignora
"""


def test_extract_js_endpoints_finds_absolute_paths() -> None:
    endpoints = extract_js_endpoints(JS)
    assert "/api/v2/users" in endpoints
    assert "/dashboard" in endpoints
    assert "/admin/settings" in endpoints


def test_extract_js_endpoints_filters_noise() -> None:
    endpoints = extract_js_endpoints(JS)
    assert "/static/logo.png" not in endpoints
    assert not any(e.startswith("//") for e in endpoints)
    assert "relative/path" not in endpoints


def test_extract_js_endpoints_empty_on_plain_code() -> None:
    assert extract_js_endpoints("const x = 1 + 2;") == set()


# --- Sitemap parser --------------------------------------------------------

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/</loc></url>
  <url><loc>https://example.com/hidden/page</loc><priority>0.5</priority></url>
  <url><loc>  https://example.com/spaces  </loc></url>
</urlset>
"""


def test_parse_sitemap_extracts_loc_urls() -> None:
    urls = parse_sitemap(SITEMAP)
    assert urls == [
        "https://example.com/",
        "https://example.com/hidden/page",
        "https://example.com/spaces",
    ]


def test_parse_sitemap_handles_sitemap_index() -> None:
    index = """<?xml version="1.0"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://example.com/sitemap-1.xml</loc></sitemap>
    </sitemapindex>
    """
    assert parse_sitemap(index) == ["https://example.com/sitemap-1.xml"]


def test_parse_sitemap_rejects_xxe_and_entity_bomb() -> None:
    xxe = """<?xml version="1.0"?>
    <!DOCTYPE foo [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>
    <urlset><url><loc>&xxe;</loc></url></urlset>
    """
    assert parse_sitemap(xxe) == []

    lol = """<?xml version="1.0"?>
    <!DOCTYPE lolz [
      <!ENTITY a "aaaaaaaaaa">
      <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
    ]>
    <urlset><url><loc>&b;</loc></url></urlset>
    """
    assert parse_sitemap(lol) == []


def test_parse_sitemap_malformed_returns_empty() -> None:
    assert parse_sitemap("<urlset><url><loc>oops") == []
