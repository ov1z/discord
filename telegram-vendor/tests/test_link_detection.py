"""Deciding whether a buyer's message is worth sending to the provider."""
from __future__ import annotations

from payments.mock import MockPaymentProvider
from payments.paypay import PayPayPaymentProvider


def test_mock_accepts_its_own_links() -> None:
    p = MockPaymentProvider()
    assert p.looks_like_link("https://example.local/pay/500/TEST001")
    assert p.looks_like_link("500円です https://example.local/pay/500/TEST001")


def test_mock_rejects_chatter() -> None:
    p = MockPaymentProvider()
    assert not p.looks_like_link("こんにちは")
    assert not p.looks_like_link("/start")
    assert not p.looks_like_link("")


def test_paypay_accepts_money_links_with_text_around() -> None:
    p = PayPayPaymentProvider(client=None)
    assert p.looks_like_link("https://pay.paypay.ne.jp/AbCd1234")
    assert p.looks_like_link("送りました https://pay.paypay.ne.jp/AbCd1234 確認して")


def test_paypay_rejects_chatter_and_other_links() -> None:
    p = PayPayPaymentProvider(client=None)
    assert not p.looks_like_link("まだ払ってません")
    assert not p.looks_like_link("https://example.com/pay/500")
    assert not p.looks_like_link("")
