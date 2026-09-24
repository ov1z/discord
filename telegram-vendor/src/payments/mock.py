"""In-memory mock PaymentProvider for dev & tests.

Link format:  https://example.local/pay/<amount>/<link_id>
e.g.          https://example.local/pay/500/TEST001   -> amount=500, READY

Supports the full purchase flow without touching the network, and lets tests
simulate timeouts, already-received links, and post-accept uncertainty.
"""
from __future__ import annotations

import re

from payments.base import AcceptOutcome, AcceptResult, PaymentProvider
from paypay.exceptions import (
    PayPayAlreadyAccepted,
    PayPayInvalidLink,
    PayPayNetworkError,
    PayPayTemporaryHold,
)
from paypay.models import LinkStatus, PaymentInfo, RequestLink, Transaction

_LINK_RE = re.compile(r"https?://example\.local/pay/(\d+)/([A-Za-z0-9_-]+)")


class MockPaymentProvider(PaymentProvider):
    name = "mock"
    supports_requests = True

    def __init__(self) -> None:
        self._received: set[str] = set()
        self.fail_inspect: set[str] = set()
        self.timeout_on_accept: set[str] = set()
        self.timeout_after_accept: set[str] = set()
        self.hold_on_accept: set[str] = set()
        self.fail_accept: set[str] = set()
        self.ready: bool = True
        self._request_seq = 0
        self._history: list[Transaction] = []

    def _parse(self, url: str) -> tuple[int, str]:
        m = _LINK_RE.match(url.strip())
        if not m:
            raise PayPayInvalidLink("not a mock pay link")
        return int(m.group(1)), m.group(2)

    async def is_ready(self) -> bool:
        return self.ready

    def looks_like_link(self, text: str) -> bool:
        return bool(_LINK_RE.search(text or ""))

    async def create_request(self, amount: int) -> RequestLink:
        self._request_seq += 1
        code = f"REQ{self._request_seq:04d}"
        return RequestLink(
            link=f"https://example.local/request/{amount}/{code}",
            code=code,
            amount=amount,
            session_id=code,
        )

    async def recent_incoming(self, limit: int = 10) -> list[Transaction]:
        return list(self._history[:limit])

    def add_incoming(
        self,
        transaction_id: str,
        amount: int,
        *,
        incoming: bool = True,
        status: str = "COMPLETED",
        completed: bool | None = None,
        created_at=None,
    ) -> Transaction:
        """Test hook: put a transaction at the top of the history."""
        if completed is not None:
            status = "COMPLETED" if completed else "PENDING"
        tx = Transaction(
            transaction_id=transaction_id,
            amount=amount,
            incoming=incoming,
            status=status,
            order_type="P2P_CODE_RECEPTION" if incoming else "P2PSEND",
            created_at=created_at,
        )
        self._history.insert(0, tx)
        return tx

    async def inspect_payment(self, url: str) -> PaymentInfo:
        amount, link_id = self._parse(url)
        if link_id in self.fail_inspect:
            raise PayPayInvalidLink("inspect failed (test hook)")
        status = LinkStatus.SUCCESS if link_id in self._received else LinkStatus.PENDING
        return PaymentInfo(
            link_id=link_id,
            amount=amount,
            status=status,
            can_accept=status == LinkStatus.PENDING,
            payment_id=f"mockpay-{link_id}",
            sender_name="Mock Sender",
            sender_external_id="mock-ext-id",
            has_password=False,
            raw={"amount": amount, "link_id": link_id, "status": status.value},
        )

    async def accept_payment(
        self, url: str, link_info: PaymentInfo | None = None
    ) -> AcceptResult:
        amount, link_id = self._parse(url)

        if link_id in self.timeout_on_accept:
            raise PayPayNetworkError("timeout during accept (test hook)")

        if link_id in self.hold_on_accept:
            raise PayPayTemporaryHold("temporary hold (test hook)")

        if link_id in self.fail_accept:
            return AcceptResult(outcome=AcceptOutcome.FAILED, message="declined")

        if link_id in self._received:
            raise PayPayAlreadyAccepted("already received")

        self._received.add(link_id)

        if link_id in self.timeout_after_accept:
            raise PayPayNetworkError("timeout after accept (test hook)")

        return AcceptResult(
            outcome=AcceptOutcome.ACCEPTED,
            payment_id=f"mockpay-{link_id}",
            raw={"amount": amount, "link_id": link_id, "status": "SUCCESS"},
        )

    async def get_payment_status(self, url: str) -> PaymentInfo:
        return await self.inspect_payment(url)

    def mark_received(self, link_id: str) -> None:
        self._received.add(link_id)
