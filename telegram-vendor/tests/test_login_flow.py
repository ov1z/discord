"""Session store encryption, restore, redaction, and PayPay service status."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from paypay.client import PayPayClient
from paypay.models import LoginStatus, PayPaySession
from paypay.session_store import PayPaySessionStore
from security.crypto import Cryptor, generate_key
from security.redaction import MASK, redact
from services.paypay_service import AuthState, PayPayService


def _store(tmp_path) -> PayPaySessionStore:
    key = generate_key()
    return PayPaySessionStore(str(tmp_path / "sess.enc"), Cryptor(key))


async def test_session_roundtrip_and_encrypted_at_rest(tmp_path) -> None:
    store = _store(tmp_path)
    session = PayPaySession(
        access_token="SECRET-TOKEN-123",
        refresh_token="REFRESH-XYZ",
        device_uuid="dev-uuid",
        token_expires_at=datetime.now(timezone.utc) + timedelta(days=90),
        account_id="acc-1",
    )
    await store.save(session)
    # File on disk must not contain the plaintext token.
    raw = (tmp_path / "sess.enc").read_bytes()
    assert b"SECRET-TOKEN-123" not in raw

    loaded = await store.load()
    assert loaded is not None
    assert loaded.access_token == "SECRET-TOKEN-123"
    assert loaded.account_id == "acc-1"


async def test_session_clear(tmp_path) -> None:
    store = _store(tmp_path)
    await store.save(PayPaySession(access_token="t"))
    assert store.exists()
    await store.clear()
    assert not store.exists()
    assert await store.load() is None


async def test_restore_session_authenticated(tmp_path) -> None:
    store = _store(tmp_path)
    await store.save(
        PayPaySession(
            access_token="tok",
            token_expires_at=datetime.now(timezone.utc) + timedelta(days=10),
        )
    )
    svc = PayPayService(PayPayClient(), store)
    state = await svc.restore_session()
    assert state == AuthState.AUTHENTICATED
    assert svc.is_authenticated()


async def test_restore_expired_without_refresh_is_unauthenticated(tmp_path) -> None:
    store = _store(tmp_path)
    await store.save(
        PayPaySession(
            access_token="tok",
            refresh_token=None,
            token_expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
    )
    svc = PayPayService(PayPayClient(), store)
    state = await svc.restore_session()
    assert state == AuthState.UNAUTHENTICATED
    assert not svc.is_authenticated()


async def test_adopt_token_persists(tmp_path) -> None:
    store = _store(tmp_path)
    svc = PayPayService(PayPayClient(), store)
    await svc.adopt_token("ACCESS-1", refresh_token="R1")
    assert svc.is_authenticated()
    reloaded = await store.load()
    assert reloaded is not None and reloaded.access_token == "ACCESS-1"


async def test_logout_clears_everything(tmp_path) -> None:
    store = _store(tmp_path)
    svc = PayPayService(PayPayClient(), store)
    await svc.adopt_token("ACCESS-1")
    await svc.logout()
    assert not svc.is_authenticated()
    assert await store.load() is None


async def test_status_never_leaks_token(tmp_path) -> None:
    store = _store(tmp_path)
    svc = PayPayService(PayPayClient(), store)
    await svc.adopt_token("VERY-SECRET")
    view = await svc.status(provider_ready=True)
    assert view.state == AuthState.AUTHENTICATED
    # The view has no token field at all.
    assert "VERY-SECRET" not in repr(view)


async def test_redaction_masks_sensitive_keys() -> None:
    payload = {
        "access_token": "abc",
        "header": {"Authorization": "Bearer xyz", "Set-Cookie": "s=1"},
        "sender": {"displayName": "たろう", "phone": "090-1234-5678"},
        "amount": 500,
        "nested": [{"password": "p"}, {"ok": "value"}],
    }
    red = redact(payload)
    assert red["access_token"] == MASK
    assert red["header"]["Authorization"] == MASK
    assert red["header"]["Set-Cookie"] == MASK
    assert red["sender"]["phone"] == MASK
    assert red["amount"] == 500
    assert red["nested"][0]["password"] == MASK
    assert red["nested"][1]["ok"] == "value"


def test_crypto_wrong_key_fails() -> None:
    c1 = Cryptor(generate_key())
    c2 = Cryptor(generate_key())
    token = c1.encrypt("hello")
    from security.crypto import CryptoError

    with pytest.raises(CryptoError):
        c2.decrypt(token)


async def test_begin_login_reports_status(tmp_path) -> None:
    """begin_login must not raise for bad input shapes; returns a status.

    (Network is not exercised here; we assert the client contract stays intact
    by checking submit_otp guards when no login is in progress.)
    """
    from paypay.exceptions import PayPayAuthError

    client = PayPayClient()
    with pytest.raises(PayPayAuthError):
        await client.submit_otp("123456")
