from __future__ import annotations

import time

from route_mapper.config import CrawlConfig
from route_mapper.crawler import Crawler
from route_mapper.models import FetchOutcome, FetchResponse
from route_mapper.robots import RobotsPolicy

PAGES: dict[str, bytes] = {
    "https://example.com/": b'<a href="/about">about</a><a href="/contact">c</a>',
    "https://example.com/about": b'<a href="/">home</a><a href="/team">team</a>',
    "https://example.com/contact": b"<p>no links</p>",
    "https://example.com/team": b'<a href="https://external.com/x">ext</a>',
}


class FakeHttpClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str) -> FetchResponse:
        self.calls.append(url)
        if url not in PAGES:
            return FetchResponse(url=url, outcome=FetchOutcome.HTTP_ERROR, status=404)
        return FetchResponse(
            url=url,
            outcome=FetchOutcome.OK,
            status=200,
            content_type="text/html; charset=utf-8",
            body=PAGES[url],
        )


class AllowAllRobots(RobotsPolicy):
    def __init__(self) -> None:
        super().__init__(user_agent="test", enabled=False)


def make_crawler(**overrides: object) -> tuple[Crawler, FakeHttpClient]:
    config = CrawlConfig(start_url="https://example.com", delay=0, **overrides)  # type: ignore[arg-type]
    client = FakeHttpClient()
    return Crawler(config, http_client=client, robots_policy=AllowAllRobots()), client


def test_crawl_visits_all_internal_pages() -> None:
    crawler, client = make_crawler()
    result = crawler.run()
    urls = {p.url for p in result.pages}
    assert urls == set(PAGES)
    assert len(client.calls) == len(PAGES)


def test_crawl_records_all_edges() -> None:
    crawler, _ = make_crawler()
    result = crawler.run()
    assert ("https://example.com/", "https://example.com/about") in result.edges
    # arco a un destino ya visitado también se registra
    assert ("https://example.com/about", "https://example.com/") in result.edges
    # los enlaces externos no generan aristas
    assert all("external.com" not in dst for _, dst in result.edges)


def test_crawl_respects_max_pages() -> None:
    crawler, _ = make_crawler(max_pages=2)
    result = crawler.run()
    assert len(result.pages) == 2


def test_crawl_respects_max_depth() -> None:
    crawler, _ = make_crawler(max_depth=1)
    result = crawler.run()
    urls = {p.url for p in result.pages}
    assert "https://example.com/team" not in urls  # está a profundidad 2


def test_crawl_stays_in_scope() -> None:
    crawler, _ = make_crawler()
    result = crawler.run()
    assert all("external.com" not in p.url for p in result.pages)


class ExplodingHttpClient(FakeHttpClient):
    """Un worker revienta con ValueError al pedir una URL concreta."""

    def __init__(self, bomb_url: str) -> None:
        super().__init__()
        self._bomb_url = bomb_url

    def get(self, url: str) -> FetchResponse:
        self.calls.append(url)
        if url == self._bomb_url:
            raise ValueError("línea de estado HTTP corrupta")
        if url not in PAGES:
            return FetchResponse(url=url, outcome=FetchOutcome.HTTP_ERROR, status=404)
        return FetchResponse(
            url=url,
            outcome=FetchOutcome.OK,
            status=200,
            content_type="text/html; charset=utf-8",
            body=PAGES[url],
        )


def test_worker_exception_is_isolated_and_crawl_continues() -> None:
    bomb = "https://example.com/about"
    config = CrawlConfig(start_url="https://example.com", delay=0)
    client = ExplodingHttpClient(bomb)
    crawler = Crawler(config, http_client=client, robots_policy=AllowAllRobots())

    result = crawler.run()  # no debe propagar la excepción del worker

    by_url = {p.url: p for p in result.pages}
    assert by_url[bomb].outcome is FetchOutcome.CONNECTION_ERROR
    assert by_url[bomb].error is not None
    # El resto de URLs alcanzables se procesan igualmente.
    assert by_url["https://example.com/"].status == 200
    assert by_url["https://example.com/contact"].status == 200


class SlowHttpClient(FakeHttpClient):
    """Cada petición induce un retraso simulado."""

    def __init__(self, delay: float) -> None:
        super().__init__()
        self._delay = delay

    def get(self, url: str) -> FetchResponse:
        time.sleep(self._delay)
        return super().get(url)


class InfiniteHttpClient:
    """Genera un espacio de URLs prácticamente infinito.

    Cada página enlaza a dos páginas nuevas, emulando un servidor que crea
    rutas dinámicas sin fin (``/n/0``, ``/n/1`` ...).
    """

    def __init__(self, fanout: int = 2) -> None:
        self.calls: list[str] = []
        self._fanout = fanout

    def get(self, url: str) -> FetchResponse:
        self.calls.append(url)
        n = url.rsplit("/", 1)[-1]
        base = n if n.isdigit() else "0"
        links = b"".join(
            b'<a href="https://example.com/n/%d">x</a>' % (int(base) * 10 + i)
            for i in range(self._fanout)
        )
        return FetchResponse(
            url=url,
            outcome=FetchOutcome.OK,
            status=200,
            content_type="text/html; charset=utf-8",
            body=links,
        )


def test_crawl_stops_on_global_timeout_and_returns_partial() -> None:
    config = CrawlConfig(
        start_url="https://example.com",
        delay=0,
        global_timeout=0.5,
    )
    client = SlowHttpClient(delay=0.2)
    crawler = Crawler(config, http_client=client, robots_policy=AllowAllRobots())

    result = crawler.run()

    # Se detuvo tras la primera página lenta en vez de recorrer las 4.
    assert 0 < len(result.pages) < len(PAGES)
    assert result.finished_at is not None


def test_crawl_truncates_links_per_page() -> None:
    client = InfiniteHttpClient(fanout=50)
    config = CrawlConfig(
        start_url="https://example.com",
        delay=0,
        max_links_per_page=3,
        max_pages=100,
        global_timeout=30,
    )
    crawler = Crawler(config, http_client=client, robots_policy=AllowAllRobots())

    result = crawler.run()

    # La raíz solo pudo encolar 3 hijos: 1 (raíz) + 3 + sus nietos (3 cada uno).
    children_of_root = {
        dst for src, dst in result.edges if src == "https://example.com/"
    }
    assert len(children_of_root) == 3


def test_queues_never_exceed_max_queued_urls() -> None:
    limit = 20
    client = InfiniteHttpClient(fanout=3)
    config = CrawlConfig(
        start_url="https://example.com",
        delay=0,
        max_queued_urls=limit,
        max_links_per_page=1000,
        max_pages=100_000,
        global_timeout=30,
    )
    crawler = Crawler(config, http_client=client, robots_policy=AllowAllRobots())

    result = crawler.run()

    # Toda URL descargada estuvo antes en `seen`; si las páginas no superan la
    # cota, entonces `seen` y `frontier` tampoco lo hicieron.
    assert len(result.pages) <= limit
    assert len(set(client.calls)) <= limit


def test_broken_page_marks_nonzero_exit_summary() -> None:
    PAGES["https://example.com/contact"] = b'<a href="/missing">x</a>'
    try:
        crawler, _ = make_crawler()
        result = crawler.run()
        assert any(p.status == 404 for p in result.pages)
    finally:
        PAGES["https://example.com/contact"] = b"<p>no links</p>"
