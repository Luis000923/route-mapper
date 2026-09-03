from __future__ import annotations

from route_mapper.parser import extract_links

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
