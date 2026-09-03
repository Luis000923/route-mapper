from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar
from urllib.error import URLError

import pytest

from route_mapper.config import CrawlConfig
from route_mapper.http_client import UrllibHttpClient
from route_mapper.models import FetchOutcome
from route_mapper.scope import ScopeEngine


def _scope(host: str = "example.com") -> ScopeEngine:
    return ScopeEngine(host, include_subdomains=False)


@pytest.mark.parametrize("target", ["127.0.0.1", "169.254.169.254", "10.0.0.1"])
def test_client_rejects_internal_ip_before_connection(target: str) -> None:
    config = CrawlConfig(start_url="https://example.com", delay=0, retries=0)
    client = UrllibHttpClient(config, scope=_scope())

    resp = client.get(f"http://{target}/")

    assert resp.outcome is FetchOutcome.CONNECTION_ERROR
    assert resp.error is not None
    assert "SSRF" in resp.error


def test_dns_failure_records_specific_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = CrawlConfig(start_url="https://example.com", delay=0, retries=0)
    client = UrllibHttpClient(config, scope=_scope())

    monkeypatch.setattr(client._scope, "assert_ip_allowed", lambda host: None)

    def _boom(*args: object, **kwargs: object) -> object:
        raise URLError(socket.gaierror(-2, "Name or service not known"))

    monkeypatch.setattr(client._opener, "open", _boom)

    resp = client.get("https://example.com/pagina")

    assert resp.outcome is FetchOutcome.CONNECTION_ERROR
    assert resp.error_detail == "DNS_RESOLUTION_ERROR"


def test_non_ascii_path_is_percent_encoded(monkeypatch: pytest.MonkeyPatch) -> None:
    config = CrawlConfig(start_url="https://example.com", delay=0, retries=0)
    client = UrllibHttpClient(config, scope=_scope())
    monkeypatch.setattr(client._scope, "assert_ip_allowed", lambda host: None)

    seen: list[str] = []

    def _capture(request: object, timeout: object = None) -> object:
        seen.append(request.full_url)  # type: ignore[attr-defined]
        raise URLError("stop here")

    monkeypatch.setattr(client._opener, "open", _capture)

    client.get("https://example.com/recursos-didácticos")

    assert seen == ["https://example.com/recursos-did%C3%A1cticos"]


def test_scope_is_a_required_argument() -> None:
    config = CrawlConfig(start_url="https://example.com", delay=0, retries=0)
    with pytest.raises(TypeError):
        UrllibHttpClient(config)  # type: ignore[call-arg]


class _RedirectHandler(BaseHTTPRequestHandler):
    seen: ClassVar[list[str]] = []
    port: ClassVar[int] = 0

    def do_GET(self) -> None:
        type(self).seen.append(self.path)
        if self.path == "/":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{type(self).port}/secret")
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"SECRET")

    def log_message(self, *args: object) -> None:
        pass


def test_client_does_not_follow_redirect_to_internal_ip() -> None:
    _RedirectHandler.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
    port = server.server_port
    _RedirectHandler.port = port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # 'localhost' se considera público para poder alcanzar el servidor de
        # pruebas; el destino literal 127.0.0.1 del redirect sigue siendo interno.
        scope = ScopeEngine(
            "localhost",
            include_subdomains=True,
            resolver=lambda host: {"localhost": ["93.184.216.34"], "127.0.0.1": ["127.0.0.1"]}[
                host
            ],
        )
        config = CrawlConfig(start_url="http://localhost", delay=0, retries=0)
        client = UrllibHttpClient(config, scope=scope)

        resp = client.get(f"http://localhost:{port}/")

        assert resp.outcome is FetchOutcome.CONNECTION_ERROR
        assert resp.error is not None and "redirect bloqueado" in resp.error
        assert _RedirectHandler.seen == ["/"]  # /secret nunca se solicitó
    finally:
        server.shutdown()
        thread.join()
