"""Unit tests verifying test environment isolation and safety net."""

from __future__ import annotations

import json
import os

from tests.conftest import make_blob


def test_environment_isolation() -> None:
    # Assert
    assert os.environ["MSWAP_LIVE_TARGET"] == "mswaptest:live"
    assert os.environ["MSWAP_NO_NETWORK"] == "1"
    assert os.environ["MSWAP_VAULT_PREFIX"] == "mswaptest:"
    assert os.environ["MSWAP_VAULT"] == "memory"
    assert "home" in os.environ["MSWAP_HOME"]
    assert "agy-state" in os.environ["MSWAP_AGY_STATE"]


def test_make_blob_structure() -> None:
    # Act
    blob_bytes = make_blob(1)
    data = json.loads(blob_bytes.decode("utf-8"))

    # Assert
    assert data["token"]["access_token"] == "ya29.FAKE-access-1"
    assert data["token"]["refresh_token"] == "1//FAKE-refresh-1"
    assert data["token"]["token_type"] == "Bearer"
    assert data["auth_method"] == "consumer"
