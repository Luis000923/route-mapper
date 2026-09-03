"""Autenticación previa al crawl.

Permite que ``route-mapper`` obtenga una sesión válida contra un panel de login
(formulario HTML o endpoint JSON/API) antes de mapear la superficie autenticada
de la aplicación.

Dos modos:

* ``form``: ``POST`` ``application/x-www-form-urlencoded`` a la URL de login. La
  sesión se toma de las cabeceras ``Set-Cookie`` de la respuesta (y de toda la
  cadena de redirecciones).
* ``json``: ``POST`` ``application/json``. La sesión se toma de ``Set-Cookie`` y,
  si se indica ``token_json_key``, del token en el cuerpo JSON, que se inyecta
  como ``Authorization: Bearer <token>``.

Seguridad:

* La URL de login se valida con :class:`ScopeEngine` (scope + pre-flight DNS
  anti-SSRF) antes de enviar credenciales.
* La contraseña nunca se escribe en logs, mensajes de excepción ni reportes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from urllib.parse import urlencode

from route_mapper.http_client import AuthHttpClient, HttpAuthError
from route_mapper.scope import ScopeEngine, ScopeViolation, SsrfViolation

log = logging.getLogger(__name__)

_AUTH_TYPES = ("form", "json")


class AuthConfigError(ValueError):
    """La configuración de autenticación es incoherente o incompleta."""


class AuthenticationError(Exception):
    """El proceso de login falló (credenciales, HTTP 401/403, red, respuesta).

    El mensaje se construye sin incluir jamás la contraseña ni el cuerpo enviado.
    """


@dataclass(slots=True)
class AuthConfig:
    login_url: str
    username: str
    password: str
    username_field: str = "username"
    password_field: str = "password"  # noqa: S105 - nombre de campo, no un secreto
    auth_type: str = "form"
    #: Clave del token dentro del cuerpo JSON de la respuesta (solo ``json``).
    #: Si se indica, el token se inyecta como ``Authorization: Bearer <token>``.
    token_json_key: str | None = None

    def __post_init__(self) -> None:
        if self.auth_type not in _AUTH_TYPES:
            raise AuthConfigError(
                f"auth_type debe ser uno de {_AUTH_TYPES}, no {self.auth_type!r}"
            )
        if not self.login_url:
            raise AuthConfigError("login_url es obligatorio")
        if not self.username or not self.password:
            raise AuthConfigError("username y password son obligatorios")
        if self.token_json_key is not None and self.auth_type != "json":
            raise AuthConfigError("token_json_key solo aplica con auth_type='json'")

    def safe_dump(self) -> dict[str, object]:
        """Volcado sin secretos para metadatos de reporte."""
        return {
            "login_url": self.login_url,
            "username": "***",
            "password": "***",
            "username_field": self.username_field,
            "password_field": self.password_field,
            "auth_type": self.auth_type,
            "token_json_key": self.token_json_key,
        }


def _build_payload(auth_config: AuthConfig) -> tuple[bytes, str]:
    fields = {
        auth_config.username_field: auth_config.username,
        auth_config.password_field: auth_config.password,
    }
    if auth_config.auth_type == "json":
        return json.dumps(fields).encode("utf-8"), "application/json"
    return urlencode(fields).encode("utf-8"), "application/x-www-form-urlencoded"


def _cookie_header(set_cookie_values: tuple[str, ...]) -> str | None:
    """Colapsa varias cabeceras ``Set-Cookie`` en un único valor ``Cookie``."""
    jar: dict[str, str] = {}
    for raw in set_cookie_values:
        try:
            parsed = SimpleCookie(raw)
        except CookieError:
            continue
        for name, morsel in parsed.items():
            jar[name] = morsel.value
    if not jar:
        return None
    return "; ".join(f"{name}={value}" for name, value in jar.items())


def _extract_token(body: bytes, key: str) -> str:
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise AuthenticationError(
            "la respuesta de login no es JSON válido"
        ) from exc
    if not isinstance(data, dict) or key not in data:
        raise AuthenticationError(
            f"la respuesta de login no contiene la clave de token {key!r}"
        )
    token = data[key]
    if not isinstance(token, str) or not token:
        raise AuthenticationError(f"el token en {key!r} está vacío o no es texto")
    return token


def authenticate(
    auth_config: AuthConfig,
    http_client: AuthHttpClient,
    scope: ScopeEngine,
) -> dict[str, str]:
    """Ejecuta el login y devuelve las cabeceras de sesión a reutilizar.

    :raises AuthenticationError: si el login falla por cualquier motivo.
    """
    try:
        scope.validate_url(auth_config.login_url)
    except (ScopeViolation, SsrfViolation) as exc:
        raise AuthenticationError(
            f"la URL de login no es válida para este objetivo: {exc}"
        ) from None

    data, content_type = _build_payload(auth_config)

    log.info(
        "autenticando (%s) en %s como %s",
        auth_config.auth_type,
        auth_config.login_url,
        auth_config.username,
    )
    try:
        result = http_client.post(
            auth_config.login_url, data=data, content_type=content_type
        )
    except HttpAuthError as exc:
        raise AuthenticationError(str(exc)) from None

    if result.status in (401, 403):
        raise AuthenticationError(
            f"credenciales rechazadas por el servidor (HTTP {result.status})"
        )
    if result.status >= 400:
        raise AuthenticationError(f"el login devolvió HTTP {result.status}")

    headers: dict[str, str] = {}

    cookie = _cookie_header(result.set_cookie)
    if cookie is not None:
        headers["Cookie"] = cookie

    if auth_config.token_json_key is not None:
        token = _extract_token(result.body, auth_config.token_json_key)
        headers["Authorization"] = f"Bearer {token}"

    if not headers:
        raise AuthenticationError(
            "el login no devolvió ni cookie de sesión ni token; "
            "revisa --auth-type / --token-key"
        )

    log.info("autenticación correcta: %s", ", ".join(sorted(headers)))
    return headers
