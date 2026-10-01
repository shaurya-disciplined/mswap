"""Unit tests for HTTP client abstractions and test doubles."""

from __future__ import annotations

import pytest

from mswap.core.errors import NetworkError
from mswap.util.http import FakeHttp, HttpResponse, UrllibHttp, json_response


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

    monkeypatch.setattr(urllib.request, "urlopen", dummy_urlopen)

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

    monkeypatch.setattr(urllib.request, "urlopen", dummy_urlopen)

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

    monkeypatch.setattr(urllib.request, "urlopen", dummy_urlopen)

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

    monkeypatch.setattr(urllib.request, "urlopen", dummy_urlopen)

    with pytest.raises(NetworkError, match=r"network error: connection refused"):
        client.request("GET", "https://api.example.com/fail")


def test_urllib_http_raises_network_error_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request
    from typing import Any

    monkeypatch.delenv("MSWAP_NO_NETWORK")
    client = UrllibHttp()

    def dummy_urlopen(req: urllib.request.Request, timeout: float = 20.0) -> Any:
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", dummy_urlopen)

    with pytest.raises(NetworkError, match=r"network error: timed out"):
        client.request("GET", "https://api.example.com/timeout")
