"""Unit tests for mswap core error taxonomy."""

from __future__ import annotations

import pytest

from mswap.core.errors import (
    INTERNAL_ERROR_CODE,
    AgyNotFound,
    ApiError,
    CorruptState,
    LockTimeout,
    MswapError,
    NetworkError,
    NothingToDo,
    NotSignedIn,
    NoViableTarget,
    TokenDead,
    UnsafeOperation,
    UsageError,
    VaultError,
)


@pytest.mark.parametrize(
    ("exc_cls", "expected_code"),
    [
        (MswapError, 1),
        (NothingToDo, 2),
        (NoViableTarget, 3),
        (NotSignedIn, 4),
        (TokenDead, 4),
        (AgyNotFound, 5),
        (UnsafeOperation, 6),
        (LockTimeout, 7),
        (UsageError, 64),
        (ApiError, 1),
        (NetworkError, 1),
        (VaultError, 1),
        (CorruptState, 1),
    ],
)
def test_error_codes_and_kind(exc_cls: type[MswapError], expected_code: int) -> None:
    exc = exc_cls("Something went wrong", hint="Try doing X")
    assert exc.code == expected_code
    assert exc.kind == exc_cls.__name__
    assert exc.message == "Something went wrong"
    assert exc.hint == "Try doing X"
    assert str(exc) == "Something went wrong"


def test_internal_error_code() -> None:
    assert INTERNAL_ERROR_CODE == 70


def test_api_error_extra_attrs() -> None:
    err = ApiError("HTTP error", status=404, endpoint="v1internal:quota", hint="Check status")
    assert err.code == 1
    assert err.kind == "ApiError"
    assert err.status == 404
    assert err.endpoint == "v1internal:quota"
    assert err.hint == "Check status"


def test_error_without_hint() -> None:
    err = MswapError("Plain error")
    assert err.hint is None
    assert err.code == 1
    assert err.kind == "MswapError"
