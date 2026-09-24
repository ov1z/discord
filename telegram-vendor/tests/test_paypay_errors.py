"""PayPay's refusal sheet must reach the admin, not be swallowed as 'failed'."""
from __future__ import annotations

import pytest

from payments.base import AcceptOutcome
from payments.paypay import PayPayPaymentProvider
from paypay.client import PayPayClient
from paypay.exceptions import PayPayError, PayPayTemporaryHold

RESTRICTED = {
    "header": {"resultCode": "S9999", "resultMessage": "Specific Error with half sheet"},
    "error": {
        "backendResultCode": "",
        "displayErrorResponse": {
            "iconUrl": "https://example.invalid/icon.png",
            "title": "現在ご利用を制限しています",
            "description": (
                "安心安全な決済サービスを維持するため\n"
                "ご利用を制限させていただく場合があります\n"
                "詳細はヘルプページをご確認ください"
            ),
            "canCloseByOutsideTap": True,
        },
    },
}


def test_check_header_carries_the_display_sheet() -> None:
    with pytest.raises(PayPayError) as excinfo:
        PayPayClient._check_header(RESTRICTED)
    exc = excinfo.value
    assert exc.display_message is not None
    assert "現在ご利用を制限しています" in exc.display_message
    assert "ヘルプページ" in exc.display_message
    assert "S9999" in str(exc)


def test_check_header_without_a_sheet_has_no_message() -> None:
    data = {"header": {"resultCode": "S1234", "resultMessage": "boom"}}
    with pytest.raises(PayPayError) as excinfo:
        PayPayClient._check_header(data)
    assert excinfo.value.display_message is None


def test_ok_result_codes_pass() -> None:
    PayPayClient._check_header({"header": {"resultCode": "S0000"}})
    PayPayClient._check_header({"header": {"resultCode": "S4002"}})


def test_display_message_ignores_an_empty_sheet() -> None:
    assert PayPayClient._display_message({}) is None
    assert PayPayClient._display_message({"displayErrorResponse": {}}) is None


class _RefusingClient:
    """Stands in for PayPayClient: the accept is refused with a sheet."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def link_receive(self, url, link_info=None, passcode=None):
        raise self._exc


def _restricted_error() -> PayPayError:
    exc = PayPayError("paypay result code S9999")
    exc.display_message = "現在ご利用を制限しています"
    return exc


async def test_provider_passes_the_message_on_failure() -> None:
    provider = PayPayPaymentProvider(_RefusingClient(_restricted_error()))
    result = await provider.accept_payment("https://pay.paypay.ne.jp/x")
    assert result.outcome == AcceptOutcome.FAILED
    assert result.message == "現在ご利用を制限しています"


async def test_provider_passes_the_message_on_hold() -> None:
    exc = PayPayTemporaryHold("held")
    exc.display_message = "一時的に受け取りを制限しています"
    provider = PayPayPaymentProvider(_RefusingClient(exc))
    result = await provider.accept_payment("https://pay.paypay.ne.jp/x")
    assert result.outcome == AcceptOutcome.HELD
    assert result.message == "一時的に受け取りを制限しています"
