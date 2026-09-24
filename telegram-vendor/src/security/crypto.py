"""Symmetric encryption helpers for PayPay session persistence.

Uses Fernet (AES-128-CBC + HMAC) keyed by ``SESSION_ENCRYPTION_KEY``.
Never store PayPay tokens on disk in plaintext.
"""
from __future__ import annotations

import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken


class CryptoError(Exception):
    """Raised when encryption/decryption fails (bad key or corrupt data)."""


def generate_key() -> str:
    """Generate a new urlsafe base64 Fernet key (for setup / docs)."""
    return Fernet.generate_key().decode("utf-8")


class Cryptor:
    """Thin wrapper around Fernet with str<->str helpers."""

    def __init__(self, key: str) -> None:
        if not key:
            raise CryptoError(
                "SESSION_ENCRYPTION_KEY is empty. Generate one with: "
                'python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )
        try:
            self._fernet = Fernet(key.encode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise CryptoError("Invalid SESSION_ENCRYPTION_KEY format") from exc
        self._key = key.encode("utf-8")

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def digest(self, value: str) -> str:
        """Stable, non-reversible id for *value*, keyed by the session key.

        Lets data be indexed by a secret (a phone number) without the secret
        ever being written down.
        """
        return hmac.new(self._key, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def decrypt(self, token: bytes) -> str:
        try:
            return self._fernet.decrypt(token).decode("utf-8")
        except InvalidToken as exc:
            raise CryptoError("Failed to decrypt session (key mismatch?)") from exc
