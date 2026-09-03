"""Punto de entrada de línea de comandos.

Responsabilidad única: parsear argumentos, construir la configuración, lanzar el
crawler y escribir el reporte. Toda la lógica vive en los módulos de librería.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from pathlib import Path

from route_mapper import __version__
from route_mapper.auth import AuthConfig, AuthConfigError, AuthenticationError
from route_mapper.config import CrawlConfig
from route_mapper.crawler import Crawler, InvalidStartUrl
from route_mapper.logging_setup import AUDIT, audit, configure_logging
from route_mapper.models import PageRecord
from route_mapper.reporters import available_formats, get_reporter

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_RUNTIME = 1

log = logging.getLogger("route_mapper.cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="route-mapper",
        description="Mapea las rutas internas de un sitio web.",
    )
    parser.add_argument("url", help="URL inicial, p. ej. https://example.com")
    parser.add_argument("-m", "--max-pages", type=int, default=500,
                        help="máximo de páginas a analizar (def. 500)")
    parser.add_argument("--max-depth", type=int, default=None,
                        help="profundidad máxima de enlaces (def. sin límite)")
    parser.add_argument("-d", "--delay", type=float, default=0.2,
                        help="pausa entre lotes en segundos (def. 0.2)")
    parser.add_argument("-t", "--timeout", type=float, default=10.0,
                        help="timeout por petición en segundos (def. 10)")
    parser.add_argument("-c", "--concurrency", type=int, default=1,
                        help="peticiones en paralelo (def. 1)")
    parser.add_argument("--retries", type=int, default=2,
                        help="reintentos ante fallos de red (def. 2)")
    parser.add_argument("--max-redirects", type=int, default=5,
                        help="redirecciones máximas por petición (def. 5)")
    parser.add_argument("--global-timeout", type=float, default=300.0,
                        help="tiempo máximo total del crawl en segundos (def. 300)")
    parser.add_argument("--max-links-per-page", type=int, default=1000,
                        help="enlaces máximos extraídos por página (def. 1000)")
    parser.add_argument("-H", "--header", action="append", default=None,
                        metavar="'Nombre: Valor'", dest="header",
                        help="cabecera HTTP personalizada; repetible "
                             "(p. ej. -H 'Authorization: Bearer <token>')")
    auth_group = parser.add_argument_group(
        "autenticación",
        "login previo contra un formulario o endpoint API; la sesión obtenida "
        "se reutiliza en todo el crawl",
    )
    auth_group.add_argument("--login-url", default=None,
                            help="URL del formulario o endpoint de login")
    auth_group.add_argument("--login-user", default=None,
                            help="nombre de usuario o correo")
    auth_group.add_argument("--login-pass", default=None,
                            help="contraseña (no se registra en logs ni reportes)")
    auth_group.add_argument("--user-field", default="username",
                            help="nombre del campo de usuario (def. 'username')")
    auth_group.add_argument("--pass-field", default="password",
                            help="nombre del campo de contraseña (def. 'password')")
    auth_group.add_argument("--auth-type", choices=("form", "json"), default="form",
                            help="tipo de autenticación (def. form)")
    auth_group.add_argument("--token-key", default=None,
                            help="clave del token en la respuesta JSON para "
                                 "inyectar 'Authorization: Bearer <token>'")

    evasion_group = parser.add_argument_group(
        "evasión / enrutado",
        "canalizar el tráfico por un proxy, rotar el User-Agent y difuminar el "
        "patrón temporal de las peticiones",
    )
    evasion_group.add_argument("--proxy", default=None, metavar="URL",
                               help="proxy http(s):// o socks5(h):// para todo "
                                    "el tráfico (p. ej. http://127.0.0.1:8080)")
    evasion_group.add_argument("--ua-file", type=Path, default=None, metavar="RUTA",
                               help="archivo con User-Agents (uno por línea) para "
                                    "rotación aleatoria por petición")
    evasion_group.add_argument("--jitter", type=float, default=0.0, metavar="SEG",
                               help="variación aleatoria ±SEG sobre la pausa "
                                    "entre lotes (def. 0)")

    disco_group = parser.add_argument_group("descubrimiento")
    disco_group.add_argument("--sitemap", action="store_true",
                             help="sembrar la cola con las URLs de /sitemap.xml")
    disco_group.add_argument("--parse-js", dest="parse_js", action="store_true",
                             default=True,
                             help="extraer endpoints de archivos .js (por defecto)")
    disco_group.add_argument("--no-parse-js", dest="parse_js", action="store_false",
                             help="desactivar la minería de endpoints en JavaScript")

    parser.add_argument("--include-subdomains", action="store_true",
                        help="seguir enlaces a subdominios del mismo dominio")
    parser.add_argument("--ignore-robots", action="store_true",
                        help="no respetar robots.txt")
    parser.add_argument("-f", "--format", choices=available_formats(), default="txt",
                        help="formato del reporte (def. txt)")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="archivo de salida (def. stdout)")
    parser.add_argument("-v", "--verbose", action="count", default=0,
                        help="aumenta el detalle de logs (-v, -vv)")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="no mostrar progreso por stderr")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def parse_headers(raw: list[str] | None) -> dict[str, str]:
    """Convierte ``["Nombre: Valor", ...]`` en un diccionario.

    Lanza :class:`ValueError` si alguna entrada carece del separador ``:``.
    """
    headers: dict[str, str] = {}
    for item in raw or []:
        name, sep, value = item.partition(":")
        if not sep or not name.strip():
            raise ValueError(
                f"cabecera mal formada: {item!r} "
                "(se espera el formato 'Nombre: Valor')"
            )
        headers[name.strip()] = value.strip()
    return headers


def _build_auth_config(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> AuthConfig | None:
    """Construye ``AuthConfig`` desde las banderas ``--login-*`` o devuelve ``None``.

    Sale con código 2 (error de uso) si la combinación de banderas es inválida.
    """
    if args.login_url is None:
        if args.login_user is not None or args.login_pass is not None:
            parser.error("--login-user/--login-pass requieren --login-url")
        return None
    if not args.login_user or not args.login_pass:
        parser.error("--login-url requiere también --login-user y --login-pass")
    try:
        return AuthConfig(
            login_url=args.login_url,
            username=args.login_user,
            password=args.login_pass,
            username_field=args.user_field,
            password_field=args.pass_field,
            auth_type=args.auth_type,
            token_json_key=args.token_key,
        )
    except AuthConfigError as exc:
        parser.error(str(exc))


def _load_user_agents(path: Path | None) -> tuple[str, ...]:
    """Lee un archivo de User-Agents (uno por línea, ignora vacías y ``#``).

    Lanza :class:`ValueError` si la ruta se indicó pero no contiene ninguno.
    """
    if path is None:
        return ()
    lines = path.read_text(encoding="utf-8").splitlines()
    agents = tuple(
        stripped
        for line in lines
        if (stripped := line.strip()) and not stripped.startswith("#")
    )
    if not agents:
        raise ValueError(f"--ua-file {path} no contiene ningún User-Agent")
    return agents


def _progress_logger() -> Callable[[PageRecord], None]:
    counter = {"n": 0}

    def _emit(record: PageRecord) -> None:
        counter["n"] += 1
        status = record.status if record.status is not None else record.outcome.value
        log.log(AUDIT, "[%04d] [%s] %s", counter["n"], status, record.url)

    return _emit


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose, quiet=args.quiet)

    auth_config = _build_auth_config(parser, args)

    try:
        extra_headers = parse_headers(args.header)
        user_agents = _load_user_agents(args.ua_file)
        config = CrawlConfig(
            start_url=args.url,
            max_pages=args.max_pages,
            max_depth=args.max_depth,
            delay=args.delay,
            timeout=args.timeout,
            concurrency=args.concurrency,
            retries=args.retries,
            max_redirects=args.max_redirects,
            respect_robots=not args.ignore_robots,
            include_subdomains=args.include_subdomains,
            max_links_per_page=args.max_links_per_page,
            global_timeout=args.global_timeout,
            extra_headers=extra_headers,
            proxy=args.proxy,
            user_agents=user_agents,
            jitter=args.jitter,
            sitemap=args.sitemap,
            parse_js=args.parse_js,
            auth=auth_config,
        )
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
        return EXIT_USAGE

    on_page = None if args.quiet else _progress_logger()

    try:
        result = Crawler(config, on_page=on_page).run()
    except InvalidStartUrl as exc:
        log.error("URL inicial inválida: %s", exc)
        return EXIT_USAGE
    except AuthenticationError as exc:
        log.error("autenticación fallida: %s", exc)
        return EXIT_RUNTIME
    except KeyboardInterrupt:
        log.error("interrumpido por el usuario")
        return EXIT_RUNTIME

    report = get_reporter(args.format).render(result)
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    else:
        sys.stdout.write(report)

    summary = result.summary()
    broken = summary["broken"]
    audit(
        log,
        "%s rutas | %s OK | %s con error | %ss",
        summary["total"],
        summary["ok"],
        broken,
        summary["duration_s"],
    )
    return EXIT_RUNTIME if broken else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
