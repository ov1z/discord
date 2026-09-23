"""Purchase orchestration: link check -> amount match -> accept -> verify.

This is the safety-critical core. It guarantees:
  * exact amount match before any accept
  * a link can back only one order (DB UNIQUE + pre-check)
  * accept is only attempted when every precondition holds
  * transport failures become PAYMENT_UNKNOWN (never a silent double-receive)
  * PAID -> delivery is separated so it can be retried after a crash

Idempotency: a per-order asyncio lock serializes concurrent triggers for the
same order, and every step re-reads DB state so replays are no-ops.
"""
from __future__ import annotations

import asyncio
import enum
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from database.models import OrderStatus, PaymentStatus
from database.repository import (
    InventoryRepository,
    OrderRepository,
    PaymentRepository,
)
from payments.base import AcceptOutcome, PaymentProvider
from paypay.exceptions import PayPayError, PaymentAmountMismatch
from paypay.models import LinkStatus, PaymentInfo
from security.redaction import redact

logger = logging.getLogger("services.payment")


class PurchaseOutcome(str, enum.Enum):
    DELIVERED = "DELIVERED"
    PAID_NOT_DELIVERED = "PAID_NOT_DELIVERED"  # money in, delivery pending/failed
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    INVALID_LINK = "INVALID_LINK"
    LINK_ALREADY_USED = "LINK_ALREADY_USED"
    NOT_ACCEPTABLE = "NOT_ACCEPTABLE"
    ORDER_EXPIRED = "ORDER_EXPIRED"
    ORDER_NOT_WAITING = "ORDER_NOT_WAITING"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    PROVIDER_NOT_READY = "PROVIDER_NOT_READY"
    PAYMENT_UNKNOWN = "PAYMENT_UNKNOWN"
    FAILED = "FAILED"


@dataclass(slots=True)
class PurchaseResult:
    outcome: PurchaseOutcome
    order_code: str
    expected_amount: int
    actual_amount: int | None = None
    delivered_content: str | None = None
    payment_id: str | None = None


class PaymentService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        provider: PaymentProvider,
    ) -> None:
        self._sm = sessionmaker
        self._provider = provider
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def set_provider(self, provider: PaymentProvider) -> None:
        self._provider = provider

    @staticmethod
    def _aware(dt: datetime | None) -> datetime | None:
        if dt is None:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    async def process_payment_link(self, order_id: int, url: str) -> PurchaseResult:
        """Full pipeline for a submitted PayPay link. Safe to call repeatedly."""
        lock = self._locks[order_id]
        async with lock:
            return await self._process(order_id, url)

    async def _process(self, order_id: int, url: str) -> PurchaseResult:
        # ---- load order & guards ----
        async with self._sm() as session:
            order = await OrderRepository(session).get(order_id)
            if order is None:
                raise ValueError(f"order {order_id} not found")
            order_code = order.order_code
            expected = order.price
            status = order.status
            expires_at = self._aware(order.expires_at)

        if status != OrderStatus.WAITING_PAYMENT.value:
            # Already progressed (double submit). Report current state safely.
            if status in (OrderStatus.DELIVERED.value,):
                return PurchaseResult(
                    PurchaseOutcome.DELIVERED, order_code, expected
                )
            if status in (OrderStatus.PAID.value, OrderStatus.DELIVERING.value):
                return PurchaseResult(
                    PurchaseOutcome.PAID_NOT_DELIVERED, order_code, expected
                )
            if status == OrderStatus.PAYMENT_UNKNOWN.value:
                return PurchaseResult(
                    PurchaseOutcome.PAYMENT_UNKNOWN, order_code, expected
                )
            return PurchaseResult(
                PurchaseOutcome.ORDER_NOT_WAITING, order_code, expected
            )

        if expires_at and datetime.now(timezone.utc) > expires_at:
            await self._set_status(order_id, OrderStatus.EXPIRED)
            return PurchaseResult(PurchaseOutcome.ORDER_EXPIRED, order_code, expected)

        if not await self._provider.is_ready():
            return PurchaseResult(
                PurchaseOutcome.PROVIDER_NOT_READY, order_code, expected
            )

        # ---- inspect link ----
        try:
            info = await self._provider.inspect_payment(url)
        except PayPayError:
            return PurchaseResult(PurchaseOutcome.INVALID_LINK, order_code, expected)

        # ---- double-use guard (pre-check; DB UNIQUE is the hard guarantee) ----
        if await self._link_already_used(info.link_id, order_id):
            return PurchaseResult(
                PurchaseOutcome.LINK_ALREADY_USED, order_code, expected
            )

        # ---- amount match (exact) ----
        if info.amount != expected:
            return PurchaseResult(
                PurchaseOutcome.AMOUNT_MISMATCH,
                order_code,
                expected,
                actual_amount=info.amount,
            )

        if info.status != LinkStatus.PENDING or not info.can_accept:
            return PurchaseResult(
                PurchaseOutcome.NOT_ACCEPTABLE, order_code, expected
            )

        # ---- claim the link on this order (enforces uniqueness) ----
        if not await self._claim_link(order_id, url, info):
            return PurchaseResult(
                PurchaseOutcome.LINK_ALREADY_USED, order_code, expected
            )

        # ---- accept ----
        await self._set_status(order_id, OrderStatus.ACCEPTING_PAYMENT)
        try:
            result = await self._provider.accept_payment(url, link_info=info)
        except PaymentAmountMismatch:
            return PurchaseResult(
                PurchaseOutcome.AMOUNT_MISMATCH, order_code, expected, info.amount
            )
        except PayPayError:
            # Truly unknown whether it went through -> UNKNOWN, not FAILED.
            await self._mark_unknown(order_id, info)
            return PurchaseResult(
                PurchaseOutcome.PAYMENT_UNKNOWN, order_code, expected
            )

        if result.outcome == AcceptOutcome.UNKNOWN:
            await self._mark_unknown(order_id, info, result.raw)
            return PurchaseResult(
                PurchaseOutcome.PAYMENT_UNKNOWN, order_code, expected
            )
        if result.outcome == AcceptOutcome.FAILED:
            await self._set_status(order_id, OrderStatus.FAILED)
            await self._update_payment(order_id, PaymentStatus.FAILED, info, result.raw)
            return PurchaseResult(PurchaseOutcome.FAILED, order_code, expected)

        # ACCEPTED or ALREADY -> confirm authoritative state.
        confirmed = await self._confirm_received(url)
        if confirmed is None:
            await self._mark_unknown(order_id, info, result.raw)
            return PurchaseResult(
                PurchaseOutcome.PAYMENT_UNKNOWN, order_code, expected
            )
        if not confirmed:
            await self._mark_unknown(order_id, info, result.raw)
            return PurchaseResult(
                PurchaseOutcome.PAYMENT_UNKNOWN, order_code, expected
            )

        # Confirmed received -> PAID.
        payment_id = result.payment_id or info.payment_id
        await self._set_status(
            order_id,
            OrderStatus.PAID,
            paid_at=datetime.now(timezone.utc),
            external_payment_id=payment_id,
        )
        await self._update_payment(
            order_id, PaymentStatus.COMPLETED, info, result.raw, external_id=payment_id
        )

        # ---- deliver ----
        return await self._deliver(order_id, order_code, expected, payment_id)

    # --------------------------------------------------------------- delivery
    async def deliver_order(self, order_id: int) -> PurchaseResult:
        """Public entry to (re)deliver a PAID/DELIVERING order."""
        lock = self._locks[order_id]
        async with lock:
            async with self._sm() as session:
                order = await OrderRepository(session).get(order_id)
                if order is None:
                    raise ValueError("order not found")
                return await self._deliver(
                    order.id, order.order_code, order.price, order.external_payment_id
                )

    async def _deliver(
        self, order_id: int, order_code: str, expected: int, payment_id: str | None
    ) -> PurchaseResult:
        async with self._sm() as session:
            order = await OrderRepository(session).get(order_id)
            if order is None:
                raise ValueError("order not found")
            if order.status == OrderStatus.DELIVERED.value:
                item = await InventoryRepository(session).get_reserved_for_order(
                    order_id
                )
                return PurchaseResult(
                    PurchaseOutcome.DELIVERED,
                    order_code,
                    expected,
                    delivered_content=item.content if item else None,
                    payment_id=payment_id,
                )
            if order.status not in (
                OrderStatus.PAID.value,
                OrderStatus.DELIVERING.value,
            ):
                return PurchaseResult(
                    PurchaseOutcome.PAID_NOT_DELIVERED, order_code, expected
                )

        # Reserve stock atomically (idempotent per order).
        async with self._sm() as session:
            irepo = InventoryRepository(session)
            order = await OrderRepository(session).get(order_id)
            assert order is not None
            item = await irepo.reserve_one(order.product_id, order_id)
            if item is None:
                # PAID but no stock: keep money, let admin re-deliver later.
                order.status = OrderStatus.DELIVERING.value
                await session.commit()
                return PurchaseResult(
                    PurchaseOutcome.OUT_OF_STOCK, order_code, expected,
                    payment_id=payment_id,
                )
            order.status = OrderStatus.DELIVERING.value
            content = item.content
            inventory_id = item.id
            await session.commit()

        # Mark delivered (the actual Telegram send is done by the caller; if it
        # fails, the order stays DELIVERING and stock stays RESERVED for retry).
        return PurchaseResult(
            PurchaseOutcome.PAID_NOT_DELIVERED,  # provisional until caller confirms
            order_code,
            expected,
            delivered_content=content,
            payment_id=payment_id,
        )

    async def confirm_delivered(self, order_id: int) -> None:
        """Call AFTER the content was successfully sent to the buyer."""
        async with self._sm() as session:
            irepo = InventoryRepository(session)
            orepo = OrderRepository(session)
            order = await orepo.get(order_id)
            if order is None:
                return
            item = await irepo.get_reserved_for_order(order_id)
            now = datetime.now(timezone.utc)
            if item is not None and item.status != "SOLD":
                await irepo.mark_sold(item.id, now)
            order.status = OrderStatus.DELIVERED.value
            order.delivered_at = now
            await session.commit()

    # --------------------------------------------------------------- helpers
    async def _confirm_received(self, url: str) -> bool | None:
        """True=received, False=not received, None=undetermined."""
        try:
            info = await self._provider.get_payment_status(url)
        except PayPayError:
            return None
        return info.status == LinkStatus.SUCCESS

    async def _link_already_used(self, link_id: str, this_order_id: int) -> bool:
        async with self._sm() as session:
            other = await OrderRepository(session).find_by_link_id(link_id)
            if other is not None and other.id != this_order_id:
                return True
            pay = await PaymentRepository(session).find_by_link_id(
                self._provider.name, link_id
            )
            if pay is not None and pay.order_id != this_order_id:
                return True
            return False

    async def _claim_link(self, order_id: int, url: str, info: PaymentInfo) -> bool:
        """Persist link_id on the order + create a payment row. UNIQUE-guarded."""
        async with self._sm() as session:
            orepo = OrderRepository(session)
            prepo = PaymentRepository(session)
            order = await orepo.get(order_id)
            if order is None:
                return False
            order.status = OrderStatus.CHECKING_PAYMENT.value
            order.paypay_link = url
            order.paypay_link_id = info.link_id
            if await prepo.get_for_order(order_id) is None:
                await prepo.create(
                    order_id=order_id,
                    provider=self._provider.name,
                    amount=info.amount,
                    paypay_link_id=info.link_id,
                    external_payment_id=info.payment_id,
                    status=PaymentStatus.INSPECTED.value,
                    raw_response=json.dumps(redact(info.raw), ensure_ascii=False),
                )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return False
            return True

    async def _set_status(self, order_id: int, status: OrderStatus, **fields) -> None:
        async with self._sm() as session:
            order = await OrderRepository(session).get(order_id)
            if order is not None:
                order.status = status.value
                for k, v in fields.items():
                    setattr(order, k, v)
                await session.commit()

    async def _mark_unknown(
        self, order_id: int, info: PaymentInfo, raw: dict | None = None
    ) -> None:
        await self._set_status(order_id, OrderStatus.PAYMENT_UNKNOWN)
        await self._update_payment(order_id, PaymentStatus.UNKNOWN, info, raw)

    async def _update_payment(
        self,
        order_id: int,
        status: PaymentStatus,
        info: PaymentInfo,
        raw: dict | None = None,
        external_id: str | None = None,
    ) -> None:
        async with self._sm() as session:
            prepo = PaymentRepository(session)
            payment = await prepo.get_for_order(order_id)
            if payment is None:
                await prepo.create(
                    order_id=order_id,
                    provider=self._provider.name,
                    amount=info.amount,
                    paypay_link_id=info.link_id,
                    external_payment_id=external_id or info.payment_id,
                    status=status.value,
                    raw_response=json.dumps(
                        redact(raw or info.raw), ensure_ascii=False
                    ),
                )
            else:
                payment.status = status.value
                if external_id:
                    payment.external_payment_id = external_id
                if raw is not None:
                    payment.raw_response = json.dumps(
                        redact(raw), ensure_ascii=False
                    )
            await session.commit()
