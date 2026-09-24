"""PAYPAY_PROXY: parsing, masking, and that only PayPay clients use it."""
from __future__ import annotations

import pytest

from paypay import auth
from paypay.client import PayPayClient
from security.redaction import MASK, redact


def _proxy_hosts(client) -> set[bytes]:
    """Hosts of the proxies an httpx client is mounted on."""
    hosts = set()
    for transport in client._mounts.values():
        url = getattr(getattr(transport, "_pool", None), "_proxy_url", None)
        if url is not None:
            hosts.add(url.host)
    return hosts


@pytest.fixture(autouse=True)
def _reset_proxy():
    auth.set_proxy(None)
    yield
    auth.set_proxy(None)


def test_http_proxy_with_credentials_for_playwright() -> None:
    opt = auth.playwright_proxy("http://user:p%40ss@jp.example.net:8080")
    assert opt == {
        "server": "http://jp.example.net:8080",
        "username": "user",
        "password": "p@ss",
    }


def test_proxy_without_credentials() -> None:
    assert auth.playwright_proxy("http://1.2.3.4:3128") == {
        "server": "http://1.2.3.4:3128"
    }


def test_authenticated_socks5_is_rejected() -> None:
    with pytest.raises(ValueError):
        auth.set_proxy("socks5://user:pass@host:1080")


def test_invalid_proxy_is_rejected() -> None:
    with pytest.raises(ValueError):
        auth.set_proxy("not-a-url")
    with pytest.raises(ValueError):
        auth.set_proxy("http://host-without-port")


def test_display_hides_credentials() -> None:
    auth.set_proxy("http://user:secret@jp.example.net:8080")
    shown = auth.proxy_display()
    assert shown == "http://jp.example.net:8080"
    assert "secret" not in shown and "user" not in shown


def test_empty_means_direct() -> None:
    auth.set_proxy("")
    assert auth.get_proxy() is None
    assert auth.proxy_display() is None


def test_sync_login_client_uses_proxy() -> None:
    auth.set_proxy("http://user:pass@jp.example.net:8080")
    with auth._client() as c:
        assert b"jp.example.net" in _proxy_hosts(c)
    auth.set_proxy(None)
    with auth._client() as c:
        assert b"jp.example.net" not in _proxy_hosts(c)


async def test_async_api_client_uses_proxy() -> None:
    auth.set_proxy("http://user:pass@jp.example.net:8080")
    client = PayPayClient()
    try:
        assert b"jp.example.net" in _proxy_hosts(client.http)
    finally:
        await client.close()


def test_proxy_url_is_redacted() -> None:
    red = redact({"paypay_proxy": "http://user:pass@host:1", "amount": 1})
    assert red["paypay_proxy"] == MASK
    assert red["amount"] == 1
