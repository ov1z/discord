"""Order lifecycle: creation, code generation, expiry, status transitions."""
from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from database.models import Order, OrderStatus
from database.repository import (
    InventoryRepository,
    OrderRepository,
    ProductRepository,
)
from services import pricing

_CODE_ALPHABET = string.ascii_uppercase + string.digits
# Avoid ambiguous characters.
_CODE_ALPHABET = _CODE_ALPHABET.translate(str.maketrans("", "", "O0I1"))


def generate_order_code() -> str:
    body = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
    return f"ORD-{body}"


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass(slots=True)
class CreateOrderResult:
    order_id: int
    order_code: str
    product_name: str
    price: int            # total the buyer must pay
    expires_at: datetime
    quantity: int = 1
    unit_price: int = 0
    reused: bool = False  # an existing active order was returned
    out_of_stock: bool = False


class OrderService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        ttl_seconds: int = 600,
    ) -> None:
        self._sm = sessionmaker
        self._ttl = ttl_seconds

    async def create_order(
        self, telegram_user_id: int, product_id: int, quantity: int = 1
    ) -> CreateOrderResult | None:
        """Create a WAITING_PAYMENT order for *quantity* units.

        Price uses the product's bulk-discount tiers (total = qty * unit_price).
        Idempotent against double presses: an existing WAITING_PAYMENT order for
        the same product is updated to the new quantity and reused; an order
        that already progressed is returned as-is.
        Returns None if the product is missing/inactive.
        """
        quantity = max(1, quantity)
        async with self._sm() as session:
            prepo = ProductRepository(session)
            irepo = InventoryRepository(session)
            orepo = OrderRepository(session)
            product = await prepo.get(product_id)
            if product is None or not product.active:
                return None

            tiers = pricing.parse_tiers(product.price_tiers, product.price)
            unit_price = pricing.unit_price_for(quantity, tiers)
            total = quantity * unit_price

            available = await irepo.count_available(product_id)

            existing = await orepo.get_active_for_user_product(
                telegram_user_id, product_id
            )
            if existing is not None:
                # Only a not-yet-paid order can be re-priced safely.
                if existing.status == OrderStatus.WAITING_PAYMENT.value:
                    existing.quantity = quantity
                    existing.unit_price = unit_price
                    existing.price = total
                    await session.commit()
                exp = _aware(existing.expires_at) or datetime.now(timezone.utc)
                return CreateOrderResult(
                    order_id=existing.id,
                    order_code=existing.order_code,
                    product_name=product.name,
                    price=existing.price,
                    quantity=existing.quantity,
                    unit_price=existing.unit_price,
                    expires_at=exp,
                    reused=True,
                    out_of_stock=available < existing.quantity,
                )

            if available < quantity:
                return CreateOrderResult(
                    order_id=0, order_code="", product_name=product.name,
                    price=total, quantity=quantity, unit_price=unit_price,
                    expires_at=datetime.now(timezone.utc), out_of_stock=True,
                )

            for _ in range(5):
                code = generate_order_code()
                if await orepo.get_by_code(code) is None:
                    break
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=self._ttl)
            order = await orepo.create(
                order_code=code,
                telegram_user_id=telegram_user_id,
                product_id=product_id,
                price=total,
                expires_at=expires_at,
                quantity=quantity,
                unit_price=unit_price,
            )
            await session.commit()
            return CreateOrderResult(
                order_id=order.id,
                order_code=order.order_code,
                product_name=product.name,
                price=order.price,
                quantity=quantity,
                unit_price=unit_price,
                expires_at=expires_at,
            )

    async def get(self, order_id: int) -> Order | None:
        async with self._sm() as session:
            return await OrderRepository(session).get(order_id)

    async def get_by_code(self, order_code: str) -> Order | None:
        async with self._sm() as session:
            return await OrderRepository(session).get_by_code(order_code)

    async def list_recent(self, limit: int = 20) -> list[Order]:
        async with self._sm() as session:
            return list(await OrderRepository(session).list_recent(limit))

    async def list_needs_attention(self) -> list[Order]:
        """Orders the admin must act on: paid-but-undelivered or unsettled."""
        async with self._sm() as session:
            return list(
                await OrderRepository(session).list_by_status(
                    [
                        OrderStatus.PAID.value,
                        OrderStatus.DELIVERING.value,
                        OrderStatus.PAYMENT_UNKNOWN.value,
                    ]
                )
            )

    def is_expired(self, order: Order) -> bool:
        exp = _aware(order.expires_at)
        if exp is None:
            return False
        return datetime.now(timezone.utc) > exp

    async def mark_status(
        self, order_id: int, status: OrderStatus, **timestamps
    ) -> None:
        async with self._sm() as session:
            order = await OrderRepository(session).get(order_id)
            if order is not None:
                order.status = status.value
                for key, value in timestamps.items():
                    setattr(order, key, value)
                await session.commit()

    async def expire_if_due(self, order_id: int) -> bool:
        async with self._sm() as session:
            order = await OrderRepository(session).get(order_id)
            if order is None:
                return False
            if order.status == OrderStatus.WAITING_PAYMENT.value and self.is_expired(
                order
            ):
                order.status = OrderStatus.EXPIRED.value
                await session.commit()
                return True
            return False

    async def cancel(self, order_code: str) -> bool:
        async with self._sm() as session:
            order = await OrderRepository(session).get_by_code(order_code)
            if order is None:
                return False
            if order.status in (
                OrderStatus.DELIVERED.value,
                OrderStatus.PAID.value,
            ):
                return False
            order.status = OrderStatus.CANCELLED.value
            await session.commit()
            return True
