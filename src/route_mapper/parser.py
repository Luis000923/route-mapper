"""Extracción de enlaces del HTML.

Se apoya en ``html.parser`` de la stdlib para no añadir dependencias. La lógica
está encapsulada para poder sustituirla por lxml/BeautifulSoup en el futuro sin
tocar el crawler.
"""

from __future__ import annotations

import contextlib
import re
from html.parser import HTMLParser
from urllib.parse import urljoin
from xml.etree.ElementTree import ParseError
from xml.etree.ElementTree import fromstring as _xml_fromstring

from route_mapper.url_utils import resolve_link

# Atributos que contienen URLs navegables o referencias de superficie por
# etiqueta. Se mantiene un enfoque puramente pasivo: nunca se ejecuta ni se
# interpreta el contenido en línea de scripts u hojas de estilo.
_URL_ATTRS: dict[str, str] = {
    "a": "href",
    "area": "href",
    "iframe": "src",
    "link": "href",
    "script": "src",
    "form": "action",
}

# Tipos de enlace que no queremos seguir aunque sean del mismo dominio.
_SKIP_REL = {"nofollow"}

_SKIP_PREFIXES = ("javascript:", "mailto:", "tel:", "data:")


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []
        self._meta_refresh: str | None = None
        #: Valor crudo de ``<base href>`` (el primero encontrado, como los
        #: navegadores). Se resuelve contra la URL real de la página.
        self.base_href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}

        if tag == "base" and self.base_href is None:
            href = attr_map.get("href", "").strip()
            if href:
                self.base_href = href
            return

        if tag in _URL_ATTRS:
            rel = set(attr_map.get("rel", "").lower().split())
            if rel & _SKIP_REL:
                return
            value = attr_map.get(_URL_ATTRS[tag], "").strip()
            if value and not value.lower().startswith(_SKIP_PREFIXES):
                self.hrefs.append(value)

        elif tag == "meta" and attr_map.get("http-equiv", "").lower() == "refresh":
            content = attr_map.get("content", "")
            if "url=" in content.lower():
                self._meta_refresh = content.split("=", 1)[1].strip().strip("'\"")

    @property
    def meta_refresh(self) -> str | None:
        return self._meta_refresh


def extract_links(
    page_url: str, html: bytes, *, encoding: str | None = None
) -> list[str]:
    """Devuelve las URLs absolutas y normalizadas encontradas en ``html``.

    ``encoding`` es el charset detectado en la cabecera ``Content-Type``; si no
    se conoce o es inválido se cae a UTF-8 con ``errors="replace"``.

    Si la página declara ``<base href>``, los enlaces relativos se resuelven
    contra esa base (resuelta a su vez contra ``page_url``), tal como haría un
    navegador. La base nunca exime del ``ScopeEngine``: las URLs resultantes
    siguen pasando por la normalización estricta y el filtro de scope aguas
    arriba.
    """
    for candidate in (encoding, "utf-8"):
        if not candidate:
            continue
        try:
            text = html.decode(candidate, errors="replace")
            break
        except LookupError:
            continue
    else:  # pragma: no cover - "utf-8" siempre es válido
        text = html.decode("utf-8", errors="replace")

    collector = _LinkCollector()
    # HTML roto no debe abortar el crawl.
    with contextlib.suppress(Exception):
        collector.feed(text)

    base_url = page_url
    if collector.base_href:
        with contextlib.suppress(Exception):
            base_url = urljoin(page_url, collector.base_href)

    raw = list(collector.hrefs)
    if collector.meta_refresh:
        raw.append(collector.meta_refresh)

    seen: set[str] = set()
    links: list[str] = []
    for href in raw:
        try:
            resolved = resolve_link(base_url, href)
        except ValueError:
            # Puerto fuera de rango u otra URL inválida: se descarta ese enlace,
            # nunca aborta el parser.
            continue
        if resolved and resolved not in seen:
            seen.add(resolved)
            links.append(resolved)
    return links


# --- Minería de endpoints en JavaScript ------------------------------------

#: Rutas absolutas ("/algo/...") entre comillas simples, dobles o backticks.
#: Deliberadamente conservador: exige que empiece por una sola barra y que el
#: primer carácter tras ella sea alfanumérico, descartando expresiones como
#: "//cdn" (protocol-relative) o operadores de división.
_JS_ENDPOINT_RE = re.compile(r"""['"`](/[A-Za-z0-9][A-Za-z0-9_./~-]*)['"`]""")

#: Extensiones de recurso estático que no aportan como endpoint de API.
_JS_NOISE_SUFFIXES = (
    ".js", ".css", ".map", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".woff", ".woff2", ".ttf", ".ico", ".webp",
)


def extract_js_endpoints(content: str) -> set[str]:
    """Extrae rutas absolutas embebidas como literales de cadena en código JS.

    Puramente léxico (regex): nunca se ejecuta ni se interpreta el JavaScript.
    Devuelve un conjunto de paths relativos a la raíz (``/api/v1/users``) que el
    llamador debe resolver contra la URL del recurso y validar contra el scope.
    """
    endpoints: set[str] = set()
    for match in _JS_ENDPOINT_RE.finditer(content):
        path = match.group(1)
        if path.lower().endswith(_JS_NOISE_SUFFIXES):
            continue
        endpoints.add(path)
    return endpoints


# --- Parsing de Sitemap XML ----------------------------------------------------


#: Un Sitemap legítimo (protocolo sitemaps.org) nunca lleva DTD ni entidades.
#: Su presencia indica un intento de XXE / "billion laughs": se rechaza el doc.
_XML_DTD_RE = re.compile(r"<!DOCTYPE|<!ENTITY", re.IGNORECASE)


def parse_sitemap(xml_content: str) -> list[str]:
    """Devuelve las URLs declaradas en un Sitemap XML (``<loc>``).

    Soporta tanto ``<urlset>`` como ``<sitemapindex>`` y es agnóstico al espacio
    de nombres. El parseo está endurecido contra XXE / XML bomb: cualquier DTD o
    declaración de entidad hace que se devuelva una lista vacía (ElementTree, por
    su parte, tampoco resuelve entidades externas). XML malformado también
    devuelve ``[]`` en lugar de propagar.
    """
    if _XML_DTD_RE.search(xml_content):
        return []
    try:
        root = _xml_fromstring(xml_content)  # noqa: S314 - DTD/entidades ya rechazadas
    except (ParseError, ValueError):
        return []

    locs: list[str] = []
    seen: set[str] = set()
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]  # descarta "{namespace}"
        if tag != "loc" or not element.text:
            continue
        url = element.text.strip()
        if url and url not in seen:
            seen.add(url)
            locs.append(url)
    return locs
