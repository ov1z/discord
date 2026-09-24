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
from paypay.models import LinkStatus, PaymentInfo, RequestLink, Transaction

logger = logging.getLogger("payments.paypay")


class PayPayPaymentProvider(PaymentProvider):
    name = "paypay"
    supports_requests = True

    def __init__(self, client: PayPayClient) -> None:
        self._client = client

    async def is_ready(self) -> bool:
        return self._client.is_authenticated()

    def looks_like_link(self, text: str) -> bool:
        return is_paypay_link(text or "")

    async def create_request(self, amount: int) -> RequestLink:
        link = await self._client.create_request_link(amount)
        logger.info("payment request issued: amount=%s code=%s", amount, link.code)
        return link

    async def recent_incoming(self, limit: int = 10) -> list[Transaction]:
        history = await self._client.payment_history(limit=limit)
        logger.info("history fetched: %s rows", len(history))
        return history

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
