"""PayPay-backed PaymentProvider.

Adapts the low-level PayPayClient to the provider interface. Distinguishes a
definitive failure from an UNKNOWN transport failure so the service layer can
avoid double-receiving money.
"""
from __future__ import annotations

import logging

from payments.base import AcceptOutcome, AcceptResult, PaymentProvider
from paypay.client import PayPayClient
from paypay.exceptions import (
    PayPayAlreadyAccepted,
    PayPayError,
    PayPayNetworkError,
)
from paypay.models import LinkStatus, PaymentInfo

logger = logging.getLogger("payments.paypay")


class PayPayPaymentProvider(PaymentProvider):
    name = "paypay"

    def __init__(self, client: PayPayClient) -> None:
        self._client = client

    async def is_ready(self) -> bool:
        return self._client.is_authenticated()

    async def inspect_payment(self, url: str) -> PaymentInfo:
        return await self._client.link_check(url)

    async def accept_payment(
        self, url: str, link_info: PaymentInfo | None = None
    ) -> AcceptResult:
        try:
            raw = await self._client.link_receive(url, link_info=link_info)
        except PayPayAlreadyAccepted:
            return AcceptResult(outcome=AcceptOutcome.ALREADY)
        except PayPayNetworkError:
            # State genuinely undetermined; caller must re-check, not fail.
            return AcceptResult(outcome=AcceptOutcome.UNKNOWN)
        except PayPayError:
            logger.warning("accept_payment failed", exc_info=False)
            return AcceptResult(outcome=AcceptOutcome.FAILED)

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
