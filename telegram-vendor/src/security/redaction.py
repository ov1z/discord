"""Secret redaction for logs and stored raw API responses.

Anything sensitive (tokens, cookies, credentials, OTP, phone) must be masked
BEFORE it reaches a log line, an exception message, or ``payments.raw_response``.
"""
from __future__ import annotations

import re
from typing import Any

SENSITIVE_KEYS: tuple[str, ...] = (
    "authorization",
    "access_token",
    "accesstoken",
    "refresh_token",
    "refreshtoken",
    "token",
    "cookie",
    "set-cookie",
    "password",
    "passcode",
    "otp",
    "code_verifier",
    "codeverifier",
    "code_challenge",
    "phone",
    "username",
    "device-uuid",
    "device_uuid",
    "client-uuid",
    "client_uuid",
    "secret",
    "photourl",
    "photo_url",
    "icon",
)

MASK = "***REDACTED***"

_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE)
_JP_PHONE_RE = re.compile(r"\b0\d{1,4}-?\d{1,4}-?\d{3,4}\b")


def _is_sensitive_key(key: str) -> bool:
    low = key.lower()
    return any(s in low for s in SENSITIVE_KEYS)


def redact(value: Any, _depth: int = 0) -> Any:
    """Return a deep copy of *value* with sensitive fields masked.

    Handles dicts, lists/tuples and scalars. Safe against deep nesting.
    """
    if _depth > 20:
        return MASK
    if isinstance(value, dict):
        return {
            k: (MASK if _is_sensitive_key(str(k)) else redact(v, _depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v, _depth + 1) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_text(text: str) -> str:
    """Mask secrets embedded in a free-form string."""
    text = _BEARER_RE.sub(r"\1" + MASK, text)
    text = _JP_PHONE_RE.sub(MASK, text)
    return text
