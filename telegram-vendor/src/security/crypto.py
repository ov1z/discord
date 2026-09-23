"""Symmetric encryption helpers for PayPay session persistence.

Uses Fernet (AES-128-CBC + HMAC) keyed by ``SESSION_ENCRYPTION_KEY``.
Never store PayPay tokens on disk in plaintext.
"""
from __future__ import annotations

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
        except (ValueError, TypeError) as exc:  # invalid key format
            raise CryptoError("Invalid SESSION_ENCRYPTION_KEY format") from exc

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, token: bytes) -> str:
        try:
            return self._fernet.decrypt(token).decode("utf-8")
        except InvalidToken as exc:
            raise CryptoError("Failed to decrypt session (key mismatch?)") from exc
