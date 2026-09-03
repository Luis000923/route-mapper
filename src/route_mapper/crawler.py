"""Motor de crawling.

Orquesta las demás capas (HTTP, parser, robots) y no sabe nada de la CLI ni de
los formatos de salida. Emite eventos a través de un callback opcional para que
la presentación quede desacoplada.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from urllib.parse import urlparse

from route_mapper.config import CrawlConfig
from route_mapper.http_client import HttpClient, UrllibHttpClient
from route_mapper.models import (
    CrawlResult,
    ExecutionMetadata,
    FetchOutcome,
    FetchResponse,
    PageRecord,
)
from route_mapper.parser import extract_links
from route_mapper.robots import RobotsPolicy
from route_mapper.scope import ScopeEngine
from route_mapper.url_utils import in_scope, normalize_url

log = logging.getLogger(__name__)

ProgressHook = Callable[[PageRecord], None]


class InvalidStartUrl(ValueError):
    """La URL inicial no es una URL http(s) válida."""


class Crawler:
    def __init__(
        self,
        config: CrawlConfig,
        *,
        http_client: HttpClient | None = None,
        robots_policy: RobotsPolicy | None = None,
        on_page: ProgressHook | None = None,
    ) -> None:
        self._config = config
        root_host = urlparse(normalize_url(config.start_url) or config.start_url).hostname or ""
        self._scope = ScopeEngine(
            root_host, include_subdomains=config.include_subdomains
        )
        self._http = http_client or UrllibHttpClient(config, scope=self._scope)
        self._robots = robots_policy or RobotsPolicy(
            user_agent=config.user_agent,
            enabled=config.respect_robots,
            timeout=config.timeout,
        )
        self._on_page = on_page

    def run(self) -> CrawlResult:
        start = normalize_url(self._config.start_url)
        if start is None:
            raise InvalidStartUrl(self._config.start_url)

        root_host = urlparse(start).hostname or ""
        result = CrawlResult(start_url=start, domain=root_host)
        result.metadata = ExecutionMetadata(
            tool_version=_tool_version(),
            started_at=_iso(result.started_at),
            config=self._config.safe_dump(),
        )

        # frontier: (url, depth, referrer)
        frontier: deque[tuple[str, int, str | None]] = deque([(start, 0, None)])
        seen: set[str] = {start}

        start_time = time.monotonic()
        timed_out = False

        with ThreadPoolExecutor(max_workers=self._config.concurrency) as pool:
            while frontier and len(result.pages) < self._config.max_pages:
                if time.monotonic() - start_time > self._config.global_timeout:
                    timed_out = True
                    break
                batch = self._take_batch(frontier, result)
                futures: dict[Future[FetchResponse], tuple[str, int, str | None]] = {}

                for url, depth, referrer in batch:
                    if not self._robots.can_fetch(url):
                        result.pages.append(
                            self._record(url, depth, referrer, _skipped("robots.txt"))
                        )
                        continue
                    futures[pool.submit(self._http.get, url)] = (url, depth, referrer)

                # `as_completed` con timeout convierte el límite global en un
                # hard-stop real para la recolección: ni un lote con peticiones
                # colgadas puede prolongar el escaneo más allá del presupuesto.
                remaining = self._config.global_timeout - (
                    time.monotonic() - start_time
                )
                try:
                    completed = list(as_completed(futures, timeout=max(0.0, remaining)))
                except FuturesTimeoutError:
                    completed = [f for f in futures if f.done()]
                    timed_out = True

                for future in completed:
                    url, depth, referrer = futures[future]
                    try:
                        response = future.result()
                    except Exception as exc:  # barrera de contención de fallos
                        # Un worker que revienta (línea de estado HTTP corrupta,
                        # puerto inválido, etc.) solo invalida SU URL: se anota
                        # como rota y el lote continúa (propiedad fail-safe).
                        log.warning("worker falló para %s: %s", url, exc)
                        response = FetchResponse(
                            url=url,
                            outcome=FetchOutcome.CONNECTION_ERROR,
                            error=f"{type(exc).__name__}: {exc}",
                            error_detail="WORKER_CRASH",
                        )
                    record = self._record(url, depth, referrer, response)
                    result.pages.append(record)
                    if self._on_page:
                        self._on_page(record)

                    self._enqueue_children(response, depth, root_host, seen, frontier, result)

                if timed_out:
                    break

                if self._config.delay:
                    time.sleep(self._config.delay)

        if timed_out:
            log.warning(
                "global_timeout (%.1fs) alcanzado: se devuelven resultados parciales",
                self._config.global_timeout,
            )

        result.finished_at = time.time()
        if result.metadata is not None:
            result.metadata.finished_at = _iso(result.finished_at)
        log.info("crawl terminado: %s", result.summary())
        return result

    # -- helpers ---------------------------------------------------------------

    def _take_batch(
        self,
        frontier: deque[tuple[str, int, str | None]],
        result: CrawlResult,
    ) -> list[tuple[str, int, str | None]]:
        room = self._config.max_pages - len(result.pages)
        size = max(1, min(self._config.concurrency, room, len(frontier)))
        return [frontier.popleft() for _ in range(size)]

    def _record(
        self,
        url: str,
        depth: int,
        referrer: str | None,
        response: FetchResponse,
    ) -> PageRecord:
        return PageRecord(
            url=url,
            status=response.status,
            content_type=response.content_type,
            outcome=response.outcome,
            depth=depth,
            referrer=referrer,
            elapsed=response.elapsed,
            error=response.error,
            error_detail=response.error_detail,
        )

    def _enqueue_children(
        self,
        response: FetchResponse,
        depth: int,
        root_host: str,
        seen: set[str],
        frontier: deque[tuple[str, int, str | None]],
        result: CrawlResult,
    ) -> None:
        if response.outcome is not FetchOutcome.OK or not response.is_html:
            return

        at_max_depth = (
            self._config.max_depth is not None and depth >= self._config.max_depth
        )

        # Cota por página: una sola página maliciosa no puede inyectar cientos de
        # miles de URLs en las colas internas.
        children = extract_links(
            response.url, response.body, encoding=response.charset
        )[
            : self._config.max_links_per_page
        ]

        limit = self._config.max_queued_urls
        for link in children:
            if not in_scope(
                link, root_host, include_subdomains=self._config.include_subdomains
            ):
                continue
            # El arco se registra siempre, aunque el destino ya se conociera:
            # así el mapa refleja todas las conexiones reales del contenido.
            if link != response.url:
                result.edges.add((response.url, link))
            if link in seen or at_max_depth:
                continue
            # Cota de memoria global: si las colas llegan al límite dejamos de
            # encolar para esta página (protección anti-OOM ante URLs infinitas).
            if len(seen) >= limit or len(frontier) >= limit:
                break
            seen.add(link)
            frontier.append((link, depth + 1, response.url))


def _iso(timestamp: float) -> str:
    """Convierte un epoch a ISO 8601 en UTC (con offset explícito)."""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _tool_version() -> str:
    # Import diferido: ``route_mapper.__init__`` importa este módulo.
    from route_mapper import __version__

    return __version__


def _skipped(reason: str) -> FetchResponse:
    return FetchResponse(url="", outcome=FetchOutcome.SKIPPED, error=reason)
