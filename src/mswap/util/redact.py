"""Secret and PII redaction for logs, errors, and traces.

Owns pattern-based masking of OAuth tokens, client secrets, and emails.
Must never log, write, or output unmasked secrets under any circumstance.
"""

from __future__ import annotations

import re

_JSON_SECRET_PATTERN = re.compile(
    r'"(access_token|refresh_token|id_token|client_secret)"\s*:\s*"[^"]*"'
)

# Python reprs (``{'refresh_token': 'abc'}`` in a traceback) and form bodies (``client_secret=abc``)
# carry the same keys without JSON's double quotes.
_REPR_SECRET_PATTERN = re.compile(
    r"'(access_token|refresh_token|id_token|client_secret)'\s*:\s*'[^']*'"
)
_FORM_SECRET_PATTERN = re.compile(
    r"\b(access_token|refresh_token|id_token|client_secret)=[^&\s'\"]+"
)

_TOKEN_PATTERNS = [
    re.compile(r"ya29\.[\w-]+"),
    re.compile(r"1//[\w-]+"),
    re.compile(r"GOCSPX-[\w-]+"),
    re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+"),
]

_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")


def redact(text: str) -> str:
    """Mask credentials, tokens, and client secrets in text with [REDACTED]."""
    text = _JSON_SECRET_PATTERN.sub(r'"\1": "[REDACTED]"', text)
    text = _REPR_SECRET_PATTERN.sub(r"'\1': '[REDACTED]'", text)
    text = _FORM_SECRET_PATTERN.sub(r"\1=[REDACTED]", text)
    for pattern in _TOKEN_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def redact_emails(text: str) -> str:
    """Mask email addresses in text with <email>."""
    return _EMAIL_PATTERN.sub("<email>", text)
