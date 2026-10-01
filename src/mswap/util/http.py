"""HTTP client interface, urllib implementation, and test double."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from mswap.core.errors import NetworkError


@dataclass
class HttpResponse:
    """HTTP response container."""

    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class Http(Protocol):
    """Protocol for HTTP clients."""

    def request(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        form: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 20.0,
    ) -> HttpResponse: ...


class UrllibHttp:
    """Real HTTP client using urllib.request."""

    def request(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        form: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 20.0,
    ) -> HttpResponse:
        if os.environ.get("MSWAP_NO_NETWORK") == "1":
            raise NetworkError("network disabled (MSWAP_NO_NETWORK=1)")

        if not url.startswith(("http://", "https://")):
            raise NetworkError(f"unsupported URL scheme: {url}")

        req_headers = dict(headers) if headers else {}
        data: bytes | None = None

        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            if "Content-Type" not in req_headers:
                req_headers["Content-Type"] = "application/json"
        elif form is not None:
            data = urllib.parse.urlencode(form).encode("utf-8")
            if "Content-Type" not in req_headers:
                req_headers["Content-Type"] = "application/x-www-form-urlencoded"

        req = urllib.request.Request(  # noqa: S310 - URL schemes are validated above to http and https
            url, data=data, headers=req_headers, method=method.upper()
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - URL validated above
                resp_body = resp.read()
                resp_headers = {k: v for k, v in resp.headers.items()}
                return HttpResponse(status=resp.status, body=resp_body, headers=resp_headers)
        except urllib.error.HTTPError as err:
            err_body = err.read()
            err_headers = {k: v for k, v in err.headers.items()}
            return HttpResponse(status=err.code, body=err_body, headers=err_headers)
        except urllib.error.URLError as err:
            raise NetworkError(f"network error: {err.reason}") from err
        except (TimeoutError, OSError) as err:
            raise NetworkError(f"network error: {err}") from err


class FakeHttp:
    """In-memory fake HTTP client for testing."""

    def __init__(self) -> None:
        self._routes: dict[tuple[str, str], list[HttpResponse]] = {}
        self.requests: list[dict[str, Any]] = []

    def add(self, method: str, url: str, *responses: HttpResponse) -> None:
        key = (method.upper(), url)
        if key not in self._routes:
            self._routes[key] = []
        self._routes[key].extend(responses)

    def request(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        form: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 20.0,
    ) -> HttpResponse:
        _ = timeout
        record: dict[str, Any] = {
            "method": method.upper(),
            "url": url,
            "headers": headers if headers is not None else {},
            "json_body": json_body,
            "form": form,
        }
        self.requests.append(record)
        key = (method.upper(), url)
        queue = self._routes.get(key)
        if not queue:
            raise AssertionError(f"unexpected request {method} {url}")
        if len(queue) == 1:
            return queue[0]
        return queue.pop(0)


def json_response(
    obj: Any, status: int = 200, headers: dict[str, str] | None = None
) -> HttpResponse:
    """Helper to construct an HttpResponse from a JSON-serializable object."""
    body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    resp_headers = {"Content-Type": "application/json"}
    if headers:
        resp_headers.update(headers)
    return HttpResponse(status=status, body=body, headers=resp_headers)
