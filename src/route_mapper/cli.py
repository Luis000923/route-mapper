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

    try:
        config = CrawlConfig(
            start_url=args.url,
            max_pages=args.max_pages,
            max_depth=args.max_depth,
            delay=args.delay,
            timeout=args.timeout,
            concurrency=args.concurrency,
            retries=args.retries,
            respect_robots=not args.ignore_robots,
            include_subdomains=args.include_subdomains,
        )
    except ValueError as exc:
        parser.error(str(exc))
        return EXIT_USAGE

    on_page = None if args.quiet else _progress_logger()

    try:
        result = Crawler(config, on_page=on_page).run()
    except InvalidStartUrl as exc:
        log.error("URL inicial inválida: %s", exc)
        return EXIT_USAGE
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
