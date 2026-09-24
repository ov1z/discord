"""Pulling codes out of pasted messages (SMS body, chat text around a link)."""
from __future__ import annotations

from paypay.auth import extract_otl_id, extract_verification_code

SMS = (
    "[PayPay]ログイン承認時はURLをタップ "
    "https://www.paypay.ne.jp/portal/oauth2/l?id=G0v8gV9gchCK845"
)


def test_otl_id_from_full_sms_body() -> None:
    assert extract_otl_id(SMS) == "G0v8gV9gchCK845"


def test_otl_id_from_bare_url() -> None:
    url = "https://www.paypay.ne.jp/portal/oauth2/l?id=G0v8gV9gchCK845"
    assert extract_otl_id(url) == "G0v8gV9gchCK845"


def test_otl_id_from_bare_id() -> None:
    assert extract_otl_id("G0v8gV9gchCK845") == "G0v8gV9gchCK845"
    assert extract_otl_id("  G0v8gV9gchCK845\n") == "G0v8gV9gchCK845"


def test_otl_id_over_multiple_lines() -> None:
    text = (
        "認証してください\n"
        "https://www.paypay.ne.jp/portal/oauth2/l?id=Ab3-_xYz99\n"
        "PayPay"
    )
    assert extract_otl_id(text) == "Ab3-_xYz99"


def test_otl_id_keeps_numeric_sms_code() -> None:
    assert extract_otl_id("1234") == "1234"


def test_otl_id_empty() -> None:
    assert extract_otl_id("") == ""
    assert extract_otl_id(None) == ""


def test_money_link_code_ignores_surrounding_text() -> None:
    text = "500円送りました https://pay.paypay.ne.jp/AbCd1234 確認お願いします"
    assert extract_verification_code(text) == "AbCd1234"


def test_money_link_code_variants() -> None:
    assert extract_verification_code("https://pay.paypay.ne.jp/AbCd1234") == "AbCd1234"
    assert extract_verification_code("pay.paypay.ne.jp/AbCd1234") == "AbCd1234"
    assert extract_verification_code("https://pay.paypay.ne.jp/AbCd1234?q=1") == "AbCd1234"
    assert extract_verification_code("AbCd1234") == "AbCd1234"


def test_money_link_and_otl_do_not_collide() -> None:
    """The OTL url must not be mangled by the money-link stripper."""
    otl = "https://www.paypay.ne.jp/portal/oauth2/l?id=G0v8gV9gchCK845"
    assert extract_verification_code(otl) != "G0v8gV9gchCK845"
    assert extract_otl_id(otl) == "G0v8gV9gchCK845"
