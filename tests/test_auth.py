from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from route_mapper.auth import AuthConfig, AuthenticationError, authenticate
from route_mapper.cli import build_parser
from route_mapper.config import CrawlConfig
from route_mapper.http_client import UrllibHttpClient
from route_mapper.scope import ScopeEngine


def _scope() -> ScopeEngine:
    # 'localhost' se trata como público para poder alcanzar el servidor de test.
    return ScopeEngine(
        "localhost", include_subdomains=False, resolver=lambda host: ["93.184.216.34"]
    )


def _client() -> UrllibHttpClient:
    config = CrawlConfig(start_url="http://localhost", delay=0, retries=0)
    return UrllibHttpClient(config, scope=_scope())


class _LoginHandler(BaseHTTPRequestHandler):
    mode: ClassVar[str] = "form"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")

        if type(self).mode == "bad":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"nope")
            return

        if type(self).mode == "json":
            payload = json.loads(raw)
            assert payload == {"username": "admin", "password": "s3cret"}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"token": "secret_token_123"}')
            return

        # form
        assert raw == "username=admin&password=s3cret"
        self.send_response(200)
        self.send_header("Set-Cookie", "sessionid=abc123; Path=/; HttpOnly")
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def login_server() -> object:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LoginHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join()


def _url(server: object, path: str = "/login") -> str:
    return f"http://localhost:{server.server_port}{path}"  # type: ignore[attr-defined]


def test_form_login_extracts_session_cookie(login_server: object) -> None:
    _LoginHandler.mode = "form"
    cfg = AuthConfig(
        login_url=_url(login_server),
        username="admin",
        password="s3cret",
    )
    headers = authenticate(cfg, _client(), _scope())
    assert headers == {"Cookie": "sessionid=abc123"}


def test_json_login_yields_bearer_token(login_server: object) -> None:
    _LoginHandler.mode = "json"
    cfg = AuthConfig(
        login_url=_url(login_server),
        username="admin",
        password="s3cret",
        auth_type="json",
        token_json_key="token",
    )
    headers = authenticate(cfg, _client(), _scope())
    assert headers == {"Authorization": "Bearer secret_token_123"}


def test_bad_credentials_raise_clean_error(login_server: object) -> None:
    _LoginHandler.mode = "bad"
    cfg = AuthConfig(
        login_url=_url(login_server),
        username="admin",
        password="s3cret",
    )
    with pytest.raises(AuthenticationError) as exc:
        authenticate(cfg, _client(), _scope())
    assert "401" in str(exc.value)
    assert "s3cret" not in str(exc.value)


def test_login_url_out_of_scope_is_rejected_before_request() -> None:
    cfg = AuthConfig(
        login_url="http://evil.example/login",
        username="admin",
        password="s3cret",
    )
    with pytest.raises(AuthenticationError):
        authenticate(cfg, _client(), _scope())


def test_crawler_injects_session_into_extra_headers(login_server: object) -> None:
    from route_mapper.crawler import Crawler
    from route_mapper.robots import RobotsPolicy

    _LoginHandler.mode = "form"
    auth = AuthConfig(
        login_url=_url(login_server), username="admin", password="s3cret"
    )
    config = CrawlConfig(
        start_url="http://localhost", delay=0, max_pages=1, auth=auth
    )
    crawler = Crawler(
        config,
        http_client=_client(),
        robots_policy=RobotsPolicy(user_agent="t", enabled=False),
    )
    crawler._scope = _scope()
    crawler._run_authentication()
    assert config.extra_headers["Cookie"] == "sessionid=abc123"


def test_cli_parses_auth_flags() -> None:
    args = build_parser().parse_args([
        "https://example.com",
        "--login-url", "https://example.com/login",
        "--login-user", "admin",
        "--login-pass", "hunter2",
        "--auth-type", "json",
        "--token-key", "access_token",
    ])
    assert args.login_url == "https://example.com/login"
    assert args.login_user == "admin"
    assert args.login_pass == "hunter2"
    assert args.auth_type == "json"
    assert args.token_key == "access_token"


def test_cli_login_url_requires_credentials() -> None:
    from route_mapper.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["https://example.com", "--login-url", "https://example.com/login"])
    assert exc.value.code == 2


def test_auth_config_redacts_secrets_in_dump() -> None:
    cfg = AuthConfig(
        login_url="https://example.com/login", username="admin", password="s3cret"
    )
    dump = cfg.safe_dump()
    assert dump["username"] == "***"
    assert dump["password"] == "***"
    assert "s3cret" not in json.dumps(dump)
