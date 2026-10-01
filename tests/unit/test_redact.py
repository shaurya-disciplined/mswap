"""Unit tests for secret and PII redaction."""

from __future__ import annotations

from mswap.util.redact import redact, redact_emails
from tests.conftest import make_blob


def test_redact_individual_patterns() -> None:
    # Google OAuth access token prefix
    assert redact("Failed with ya29.a0AfH6SM12345 token") == "Failed with [REDACTED] token"

    # Google OAuth refresh token prefix
    assert redact("Refresh token 1//04abc-xyz is dead") == "Refresh token [REDACTED] is dead"

    # Google OAuth client secret prefix
    assert redact("Client secret GOCSPX-abc123_xyz rejected") == "Client secret [REDACTED] rejected"

    # JWT / ID token (three base64 segments)
    assert redact("ID token eyJhbGciOi.eyJzdWIiOi.signature123-abc") == "ID token [REDACTED]"


def test_text_without_secrets_unchanged() -> None:
    plain = "This is a normal error message with no tokens or credentials."
    assert redact(plain) == plain


def test_redact_json_blob_string() -> None:
    blob_str = make_blob(1).decode("utf-8")
    redacted = redact(blob_str)
    assert "ya29." not in redacted
    assert "1//" not in redacted
    assert '"access_token": "[REDACTED]"' in redacted
    assert '"refresh_token": "[REDACTED]"' in redacted
    assert "[REDACTED]" in redacted


def test_redact_json_keys() -> None:
    json_snippet = (
        '{"access_token": "custom-val", "refresh_token": "custom-ref", '
        '"id_token": "custom-id", "client_secret": "custom-sec"}'
    )
    redacted = redact(json_snippet)
    assert '"access_token": "[REDACTED]"' in redacted
    assert '"refresh_token": "[REDACTED]"' in redacted
    assert '"id_token": "[REDACTED]"' in redacted
    assert '"client_secret": "[REDACTED]"' in redacted
    assert "custom-val" not in redacted
    assert "custom-ref" not in redacted
    assert "custom-id" not in redacted
    assert "custom-sec" not in redacted


def test_redact_emails() -> None:
    sample_email = "a@b" + ".com"
    assert redact_emails(f"Send mail to {sample_email} please") == "Send mail to <email> please"
    assert redact_emails("User alice.smith+work@example.com logged in") == "User <email> logged in"
    assert redact_emails("No email here") == "No email here"
