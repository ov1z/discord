"""PayPay-backed PaymentProvider.

Adapts the low-level PayPayClient to the provider interface. Distinguishes a
definitive failure from an UNKNOWN transport failure so the service layer can
avoid double-receiving money.
"""
from __future__ import annotations

import logging

from payments.base import AcceptOutcome, AcceptResult, PaymentProvider
from paypay.client import PayPayClient, is_paypay_link
from paypay.exceptions import (
    PayPayAlreadyAccepted,
    PayPayError,
    PayPayNetworkError,
    PayPayTemporaryHold,
)
from paypay.models import LinkStatus, PaymentInfo

logger = logging.getLogger("payments.paypay")


class PayPayPaymentProvider(PaymentProvider):
    name = "paypay"

    def __init__(self, client: PayPayClient) -> None:
        self._client = client

    async def is_ready(self) -> bool:
        return self._client.is_authenticated()

    def looks_like_link(self, text: str) -> bool:
        return is_paypay_link(text or "")

    async def inspect_payment(self, url: str) -> PaymentInfo:
        info = await self._client.link_check(url)
        logger.info(
            "link inspected: amount=%s status=%s acceptable=%s",
            info.amount, info.status.value, info.can_accept,
        )
        return info

    async def accept_payment(
        self, url: str, link_info: PaymentInfo | None = None
    ) -> AcceptResult:
        try:
            raw = await self._client.link_receive(url, link_info=link_info)
        except PayPayAlreadyAccepted:
            return AcceptResult(outcome=AcceptOutcome.ALREADY)
        except PayPayTemporaryHold as exc:
            return AcceptResult(
                outcome=AcceptOutcome.HELD, message=exc.display_message
            )
        except PayPayNetworkError:
            return AcceptResult(outcome=AcceptOutcome.UNKNOWN)
        except PayPayError as exc:
            logger.warning("accept_payment failed: %s", exc)
            return AcceptResult(
                outcome=AcceptOutcome.FAILED, message=exc.display_message
            )

        payment_id = None
        try:
            payment_id = raw.get("payload", {}).get("orderId")
        except AttributeError:
            pass
        return AcceptResult(
            outcome=AcceptOutcome.ACCEPTED, payment_id=payment_id, raw=raw
        )

    async def get_payment_status(self, url: str) -> PaymentInfo:
        return await self._client.link_check(url)

    async def close(self) -> None:
        await self._client.close()
