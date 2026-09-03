"""Utilidades puras para normalizar y comparar URLs.

Todas las funciones de este módulo son deterministas y sin efectos secundarios,
lo que las hace triviales de testear de forma aislada.
"""

from __future__ import annotations

import re
from urllib.parse import (
    parse_qsl,
    quote,
    urldefrag,
    urlencode,
    urljoin,
    urlparse,
    urlunparse,
)

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_DEFAULT_PORTS = {"http": 80, "https": 443}

#: Caracteres estructurales que ``quote`` debe preservar en el ``path``.
_PATH_SAFE = "/:@!$&'()*+,;=~-._"
_PCT_RE = re.compile(r"%[0-9A-Fa-f]{2}")
#: Caracteres de control que habilitarían HTTP request/response splitting.
_FORBIDDEN = ("\n", "\r", "\x00")


def _encode_component(value: str, safe: str) -> str:
    """Percent-encodea Unicode preservando los ``%XX`` ya válidos (sin doblar)."""
    out: list[str] = []
    last = 0
    for match in _PCT_RE.finditer(value):
        out.append(quote(value[last : match.start()], safe=safe))
        out.append(match.group(0).upper())
        last = match.end()
    out.append(quote(value[last:], safe=safe))
    return "".join(out)


def _encode_host(host: str) -> str | None:
    """Convierte un host IDN a su forma Punycode ASCII; ``None`` si es inválido."""
    if host.isascii():
        return host
    try:
        return host.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return None


def normalize_url(url: str) -> str | None:
    """Normaliza una URL o devuelve ``None`` si no es http(s) navegable.

    - Elimina el fragmento (``#...``).
    - Pasa el esquema y el host a minúsculas.
    - Quita el puerto por defecto.
    - Elimina la barra final salvo en la raíz.
    - Ordena alfabéticamente los parámetros de la query y colapsa duplicados
      idénticos, de modo que ``?b=2&a=1`` y ``?a=1&b=2`` normalizan igual y no
      amplifican el espacio de escaneo.
    """
    if not url:
        return None

    if any(ch in url for ch in _FORBIDDEN):
        # Evita inyección de CRLF en cabeceras HTTP a partir de la URL.
        return None

    url, _ = urldefrag(url.strip())
    parsed = urlparse(url)

    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return None

    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        # Puerto fuera de rango (p. ej. ``http://host:999999/``): URL inservible.
        return None
    if not hostname:
        return None

    scheme = parsed.scheme.lower()
    host = _encode_host(hostname.lower())
    if host is None:
        return None

    netloc = host
    if port and port != _DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{port}"

    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    path = _encode_component(path, _PATH_SAFE)

    query = _normalize_query(parsed.query)

    return urlunparse((scheme, netloc, path, parsed.params, query, ""))


def _normalize_query(query: str) -> str:
    """Ordena los pares de la query y elimina duplicados exactos."""
    if not query:
        return ""
    pairs = parse_qsl(query, keep_blank_values=True)
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for pair in pairs:
        if pair in seen:
            continue
        seen.add(pair)
        unique.append(pair)
    unique.sort(key=lambda kv: (kv[0], kv[1]))
    return urlencode(unique)


def resolve_link(base_url: str, href: str) -> str | None:
    """Convierte un ``href`` relativo en URL absoluta normalizada."""
    try:
        absolute = urljoin(base_url, href)
    except ValueError:
        return None
    return normalize_url(absolute)


def is_valid_subdomain(host: str, root: str, *, include_subdomains: bool) -> bool:
    """Coincidencia estricta de host contra la semilla, sin adivinar sufijos.

    - Sin subdominios: sólo se acepta el host idéntico a ``root``.
    - Con subdominios: se acepta ``root`` y cualquier ``*.root`` (sufijo
      explícito). No se usa la Public Suffix List, por lo que ``a.co.uk`` y
      ``b.co.uk`` nunca se consideran del mismo ámbito.
    """
    host = host.lower().rstrip(".")
    root = root.lower().rstrip(".")
    if not host or not root:
        return False
    if host == root:
        return True
    if include_subdomains:
        return host.endswith("." + root)
    return False


def in_scope(url: str, root_hostname: str, *, include_subdomains: bool) -> bool:
    """Indica si ``url`` pertenece al ámbito del crawl (sólo política de dominio)."""
    try:
        host = urlparse(url).hostname
    except ValueError:
        return False
    if not host:
        return False
    return is_valid_subdomain(
        host, root_hostname, include_subdomains=include_subdomains
    )
