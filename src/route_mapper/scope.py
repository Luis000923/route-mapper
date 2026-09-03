"""Motor de scope y filtro SSRF.

Dos responsabilidades, sin dependencias externas (sólo ``ipaddress`` y
``socket`` de la stdlib):

* **Scope estricto:** una URL sólo entra en el crawl si su host coincide
  exactamente con la semilla o, con subdominios habilitados, es un sufijo
  explícito de ella (``*.app.example.com``). No se adivina el TLD ni se usa la
  Public Suffix List.
* **Filtro SSRF (pre-flight DNS):** antes de cualquier conexión se resuelve el
  hostname y se comprueba que **todas** las IPs devueltas sean públicas. Se
  acepta el riesgo de TOCTOU / DNS rebinding que impone ``urllib``.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlparse

from route_mapper.url_utils import is_valid_subdomain

#: Un resolver traduce un hostname a la lista de IPs (texto) a las que apunta.
Resolver = Callable[[str], list[str]]

#: Carrier-Grade NAT (RFC 6598). ``ipaddress`` no lo marca como ``is_private``
#: hasta Python 3.13, así que lo comprobamos explícitamente para todas las
#: versiones soportadas.
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


class ScopeViolation(Exception):
    """La URL de destino queda fuera del ámbito autorizado del crawl."""


class SsrfViolation(Exception):
    """El host resuelve a una IP no pública (loopback, privada, link-local...)."""


def _default_resolver(host: str) -> list[str]:
    """Resolver real basado en ``socket.getaddrinfo`` (IPv4 e IPv6)."""
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return [str(info[4][0]) for info in infos]


def is_blocked_ip(ip: str) -> bool:
    """``True`` si ``ip`` pertenece a un rango que nunca debe contactarse.

    Cubre loopback (``127.0.0.0/8``, ``::1``), redes privadas (``10/8``,
    ``172.16/12``, ``192.168/16``), link-local (``169.254/16``, ``fe80::/10``),
    multicast, CGNAT (``100.64.0.0/10``), no especificadas (``0.0.0.0``) y
    reservadas. Cualquier
    representación que ``ipaddress`` no sepa parsear se deniega por defecto.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    if addr.version == 4 and addr in _CGNAT_NETWORK:
        return True
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


class ScopeEngine:
    """Dictamina si una URL está permitida por políticas de dominio y de IP."""

    def __init__(
        self,
        root_host: str,
        *,
        include_subdomains: bool,
        resolver: Resolver | None = None,
    ) -> None:
        self._root = root_host.lower().rstrip(".")
        self._include_subdomains = include_subdomains
        self._resolver: Resolver = resolver or _default_resolver

    @classmethod
    def from_config(
        cls,
        root_host: str,
        *,
        include_subdomains: bool,
        resolver: Resolver | None = None,
    ) -> ScopeEngine:
        return cls(
            root_host,
            include_subdomains=include_subdomains,
            resolver=resolver,
        )

    def host_in_scope(self, host: str) -> bool:
        return is_valid_subdomain(
            host, self._root, include_subdomains=self._include_subdomains
        )

    def assert_ip_allowed(self, host: str) -> list[str]:
        """Resuelve ``host`` y verifica que todas sus IPs sean públicas.

        Devuelve la lista de IPs resueltas o lanza :class:`SsrfViolation`.
        """
        try:
            ips = self._resolver(host)
        except OSError as exc:
            raise SsrfViolation(f"no se pudo resolver {host!r}: {exc}") from exc
        if not ips:
            raise SsrfViolation(f"{host!r} no resolvió a ninguna IP")
        for ip in ips:
            if is_blocked_ip(ip):
                raise SsrfViolation(f"{host!r} resuelve a una IP no pública ({ip})")
        return ips

    def validate_url(self, url: str) -> list[str]:
        """Valida scope + SSRF de ``url``. Lanza excepción si algo falla."""
        host = urlparse(url).hostname
        if not host:
            raise ScopeViolation(f"URL sin host: {url!r}")
        if not self.host_in_scope(host):
            raise ScopeViolation(f"{host!r} fuera del ámbito de {self._root!r}")
        return self.assert_ip_allowed(host)
