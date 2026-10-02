"""Unit tests for HTTP client abstractions and test doubles."""

from __future__ import annotations

import pytest

from mswap.core.errors import NetworkError
from mswap.util.http import _OPENER, FakeHttp, HttpResponse, UrllibHttp, json_response


def test_fake_http_queues_in_order_and_repeats_last() -> None:
    # Arrange
    http = FakeHttp()
    resp1 = HttpResponse(status=200, body=b"first")
    resp2 = HttpResponse(status=201, body=b"second")
    http.add("POST", "https://api.example.com/test", resp1, resp2)

    # Act & Assert
    # First response
    r1 = http.request("POST", "https://api.example.com/test")
    assert r1.status == 200
    assert r1.body == b"first"

    # Second response
    r2 = http.request("POST", "https://api.example.com/test")
    assert r2.status == 201
    assert r2.body == b"second"

    # Third response repeats the last
    r3 = http.request("POST", "https://api.example.com/test")
    assert r3.status == 201
    assert r3.body == b"second"


def test_fake_http_unexpected_request_raises_assertion_error() -> None:
    # Arrange
    http = FakeHttp()

    # Act & Assert
    with pytest.raises(
        AssertionError, match=r"unexpected request GET https://api\.example\.com/unknown"
    ):
        http.request("GET", "https://api.example.com/unknown")


def test_fake_http_records_headers_and_payloads() -> None:
    # Arrange
    http = FakeHttp()
    http.add("POST", "https://api.example.com/data", HttpResponse(status=200, body=b"ok"))

    # Act
    http.request(
        "POST",
        "https://api.example.com/data",
        headers={"Authorization": "Bearer fake", "User-Agent": "test-agent"},
        json_body={"key": "value"},
    )

    # Assert
    assert len(http.requests) == 1
    req = http.requests[0]
    assert req["method"] == "POST"
    assert req["url"] == "https://api.example.com/data"
    assert req["headers"]["Authorization"] == "Bearer fake"
    assert req["headers"]["User-Agent"] == "test-agent"
    assert req["json_body"] == {"key": "value"}
    assert req["form"] is None


def test_json_response_helper() -> None:
    # Arrange
    data = {"status": "success", "count": 42}

    # Act
    resp = json_response(data, status=202)

    # Assert
    assert resp.status == 202
    assert resp.headers["Content-Type"] == "application/json"
    assert resp.json() == data


def test_urllib_http_disabled_by_env_var() -> None:
    # Arrange
    # Autouse fixture sets MSWAP_NO_NETWORK="1"
    client = UrllibHttp()

    # Act & Assert
    with pytest.raises(NetworkError, match=r"network disabled \(MSWAP_NO_NETWORK=1\)"):
        client.request("GET", "https://api.example.com/live")


def test_urllib_http_success_and_json_body(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request
    from typing import Any

    monkeypatch.delenv("MSWAP_NO_NETWORK")
    client = UrllibHttp()
    captured_req: list[urllib.request.Request] = []

    class DummyResponse:
        def __init__(self) -> None:
            self.status = 200
            self.headers = {"Content-Type": "application/json"}

        def read(self) -> bytes:
            return b'{"ok": true}'

        def __enter__(self) -> DummyResponse:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

    def dummy_urlopen(req: urllib.request.Request, timeout: float = 20.0) -> DummyResponse:
        captured_req.append(req)
        return DummyResponse()

    monkeypatch.setattr(_OPENER, "open", dummy_urlopen)

    resp = client.request("POST", "https://api.example.com/item", json_body={"hello": "world"})
    assert resp.status == 200
    assert resp.json() == {"ok": True}
    assert len(captured_req) == 1
    assert captured_req[0].get_header("Content-type") == "application/json"
    assert captured_req[0].data == b'{"hello": "world"}'


def test_urllib_http_form_body(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request
    from typing import Any

    monkeypatch.delenv("MSWAP_NO_NETWORK")
    client = UrllibHttp()
    captured_req: list[urllib.request.Request] = []

    class DummyResponse:
        def __init__(self) -> None:
            self.status = 200
            self.headers: dict[str, str] = {}

        def read(self) -> bytes:
            return b"ok"

        def __enter__(self) -> DummyResponse:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

    def dummy_urlopen(req: urllib.request.Request, timeout: float = 20.0) -> DummyResponse:
        captured_req.append(req)
        return DummyResponse()

    monkeypatch.setattr(_OPENER, "open", dummy_urlopen)

    resp = client.request("POST", "https://api.example.com/form", form={"field": "val"})
    assert resp.status == 200
    assert len(captured_req) == 1
    assert captured_req[0].get_header("Content-type") == "application/x-www-form-urlencoded"
    assert captured_req[0].data == b"field=val"


def test_urllib_http_catches_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    import urllib.error
    import urllib.request
    from typing import Any

    monkeypatch.delenv("MSWAP_NO_NETWORK")
    client = UrllibHttp()

    def dummy_urlopen(req: urllib.request.Request, timeout: float = 20.0) -> Any:
        fp = io.BytesIO(b"bad request payload")
        raise urllib.error.HTTPError(
            req.full_url,
            400,
            "Bad Request",
            {"X-Error": "1"},
            fp,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(_OPENER, "open", dummy_urlopen)

    resp = client.request("GET", "https://api.example.com/bad")
    assert resp.status == 400
    assert resp.body == b"bad request payload"
    assert resp.headers.get("X-Error") == "1"


def test_urllib_http_raises_network_error_on_url_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error
    import urllib.request
    from typing import Any

    monkeypatch.delenv("MSWAP_NO_NETWORK")
    client = UrllibHttp()

    def dummy_urlopen(req: urllib.request.Request, timeout: float = 20.0) -> Any:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(_OPENER, "open", dummy_urlopen)

    with pytest.raises(NetworkError, match=r"network error: connection refused"):
        client.request("GET", "https://api.example.com/fail")


def test_urllib_http_raises_network_error_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request
    from typing import Any

    monkeypatch.delenv("MSWAP_NO_NETWORK")
    client = UrllibHttp()

    def dummy_urlopen(req: urllib.request.Request, timeout: float = 20.0) -> Any:
        raise TimeoutError("timed out")

    monkeypatch.setattr(_OPENER, "open", dummy_urlopen)

    with pytest.raises(NetworkError, match=r"network error: timed out"):
        client.request("GET", "https://api.example.com/timeout")


def test_urllib_http_refuses_plain_http_and_other_schemes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MSWAP_NO_NETWORK")
    client = UrllibHttp()
    for url in ("http://oauth2.example.com/token", "file:///etc/passwd", "ftp://example.com/x"):
        with pytest.raises(NetworkError, match="https only"):
            client.request("POST", url, form={"refresh_token": "x"})


def test_redirects_are_never_followed_so_credentials_stay_on_the_first_host() -> None:
    import http.server
    import threading
    import urllib.error
    import urllib.request

    hits: dict[str, list[str]] = {"target": [], "origin": []}

    class Target(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits["target"].append(self.headers.get("Authorization", ""))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            pass

    target = http.server.HTTPServer(("127.0.0.1", 0), Target)
    target_url = f"http://127.0.0.1:{target.server_address[1]}/stolen"

    class Origin(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits["origin"].append(self.headers.get("Authorization", ""))
            self.send_response(302)
            self.send_header("Location", target_url)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            pass

    origin = http.server.HTTPServer(("127.0.0.1", 0), Origin)
    threads = [threading.Thread(target=srv.serve_forever, daemon=True) for srv in (origin, target)]
    for thread in threads:
        thread.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{origin.server_address[1]}/start",
            headers={"Authorization": "Bearer ya29.FAKE"},
        )
        with pytest.raises(urllib.error.HTTPError) as info:
            _OPENER.open(req, timeout=5)
        assert info.value.code == 302
    finally:
        for srv in (origin, target):
            srv.shutdown()
            srv.server_close()
    assert hits["origin"] == ["Bearer ya29.FAKE"]
    assert hits["target"] == []
