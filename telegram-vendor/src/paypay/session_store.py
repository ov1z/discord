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
from uuid import uuid4

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


class DeviceStore:
    """Remembers the device/client UUID pair used for each PayPay account.

    PayPay treats an unseen ``Device-UUID`` as a brand-new handset: generating
    a fresh pair on every login forces 2FA each time and looks like automated
    access to their anti-fraud checks. Reusing the pair keeps the account tied
    to one "device", exactly as a real phone would be.

    The phone number is never written to disk: entries are keyed by a keyed
    digest of it, and the file is encrypted on top of that.
    """

    def __init__(self, path: str, cryptor: Cryptor) -> None:
        self._path = Path(path)
        self._cryptor = cryptor
        self._lock = asyncio.Lock()

    async def get_or_create(self, phone: str) -> tuple[str, str]:
        """Return ``(device_uuid, client_uuid)`` for *phone*, creating once."""
        key = self._cryptor.digest(phone)
        async with self._lock:
            data = await asyncio.to_thread(self._read)
            entry = data.get(key)
            if not entry or not entry.get("device_uuid"):
                entry = {"device_uuid": str(uuid4()), "client_uuid": str(uuid4())}
                data[key] = entry
                await asyncio.to_thread(self._write, data)
        return entry["device_uuid"], entry["client_uuid"]

    def _read(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            plaintext = self._cryptor.decrypt(self._path.read_bytes())
        except (CryptoError, OSError):
            return {}
        try:
            data = json.loads(plaintext)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict) -> None:
        token = self._cryptor.encrypt(json.dumps(data, ensure_ascii=False))
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_bytes(token)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self._path)
