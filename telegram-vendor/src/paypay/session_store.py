"""Encrypted persistence for the re-usable PayPay session.

Stores an encrypted JSON blob at ``PAYPAY_SESSION_PATH``. Only session
material (tokens, uuids, expiry) is stored — never phone / password / OTP.
Everything is encrypted with the Fernet key, so the file is useless on its own.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from paypay.models import PayPaySession
from security.crypto import Cryptor, CryptoError


class PayPaySessionStore:
    def __init__(self, path: str, cryptor: Cryptor) -> None:
        self._path = Path(path)
        self._cryptor = cryptor
        self._lock = asyncio.Lock()

    async def save(self, session: PayPaySession) -> None:
        payload = json.dumps(session.to_dict(), ensure_ascii=False)
        token = self._cryptor.encrypt(payload)
        async with self._lock:
            await asyncio.to_thread(self._atomic_write, token)

    def _atomic_write(self, token: bytes) -> None:
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_bytes(token)
        # 0600 so other users cannot read the encrypted blob.
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self._path)

    async def load(self) -> PayPaySession | None:
        if not self._path.exists():
            return None
        async with self._lock:
            token = await asyncio.to_thread(self._path.read_bytes)
        try:
            plaintext = self._cryptor.decrypt(token)
        except CryptoError:
            return None
        try:
            data = json.loads(plaintext)
        except json.JSONDecodeError:
            return None
        return PayPaySession.from_dict(data)

    async def clear(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._unlink)

    def _unlink(self) -> None:
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            pass

    def exists(self) -> bool:
        return self._path.exists()
