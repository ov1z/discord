"""Data-access layer.

All SQL lives here; services call these repositories. Keeps concurrency-
sensitive operations (stock reservation, link-uniqueness) in one place.
"""
from __future__ import annotations

from datetime import datetime
from typing import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    Inventory,
    InventoryStatus,
    Order,
    OrderStatus,
    Payment,
    Product,
    User,
)


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self, telegram_user_id: int, username: str | None, first_name: str | None
    ) -> User:
        user = await self.session.scalar(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )
        if user is None:
            user = User(
                telegram_user_id=telegram_user_id,
                username=username,
                first_name=first_name,
            )
            self.session.add(user)
        else:
            user.username = username
            user.first_name = first_name
        await self.session.flush()
        return user

    async def all_telegram_ids(self) -> list[int]:
        """Every registered user's Telegram id (for broadcast)."""
        rows = await self.session.scalars(
            select(User.telegram_user_id).order_by(User.id)
        )
        return list(rows.all())

    async def count(self) -> int:
        return int(
            await self.session.scalar(select(func.count()).select_from(User)) or 0
        )


class ProductRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, name: str, price: int, description: str | None = None
    ) -> Product:
        product = Product(name=name, price=price, description=description, active=True)
        self.session.add(product)
        await self.session.flush()
        return product

    async def get(self, product_id: int) -> Product | None:
        return await self.session.get(Product, product_id)

    async def list_active(self) -> Sequence[Product]:
        return (
            await self.session.scalars(
                select(Product).where(Product.active.is_(True)).order_by(Product.id)
            )
        ).all()

    async def list_all(self) -> Sequence[Product]:
        return (
            await self.session.scalars(select(Product).order_by(Product.id))
        ).all()

    async def update_fields(self, product_id: int, **fields) -> Product | None:
        product = await self.get(product_id)
        if product is None:
            return None
        for key, value in fields.items():
            setattr(product, key, value)
        await self.session.flush()
        return product

    async def deactivate(self, product_id: int) -> bool:
        product = await self.get(product_id)
        if product is None:
            return False
        product.active = False
        await self.session.flush()
        return True


class InventoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_many(self, product_id: int, contents: Sequence[str]) -> int:
        items = [
            Inventory(
                product_id=product_id,
                content=c.strip(),
                status=InventoryStatus.AVAILABLE.value,
            )
            for c in contents
            if c.strip()
        ]
        self.session.add_all(items)
        await self.session.flush()
        return len(items)

    async def count_available(self, product_id: int) -> int:
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(Inventory)
                .where(
                    Inventory.product_id == product_id,
                    Inventory.status == InventoryStatus.AVAILABLE.value,
                )
            )
            or 0
        )

    async def counts_by_status(self, product_id: int) -> dict[str, int]:
        rows = await self.session.execute(
            select(Inventory.status, func.count())
            .where(Inventory.product_id == product_id)
            .group_by(Inventory.status)
        )
        return {status: count for status, count in rows.all()}

    async def list_for_product(
        self, product_id: int, limit: int = 50
    ) -> Sequence[Inventory]:
        return (
            await self.session.scalars(
                select(Inventory)
                .where(Inventory.product_id == product_id)
                .order_by(Inventory.id)
                .limit(limit)
            )
        ).all()

    async def reserve_one(self, product_id: int, order_id: int) -> Inventory | None:
        """Atomically move ONE available item to RESERVED for *order_id*.

        Idempotent: if this order already holds a reserved/sold item, return it
        instead of grabbing another (prevents double allocation on retries).

        The UPDATE ... WHERE id = (SELECT ... LIMIT 1) pattern ensures two
        concurrent buyers cannot grab the same row.
        """
        existing = await self.session.scalar(
            select(Inventory).where(
                Inventory.reserved_order_id == order_id,
                Inventory.status.in_(
                    [InventoryStatus.RESERVED.value, InventoryStatus.SOLD.value]
                ),
            )
        )
        if existing is not None:
            return existing

        candidate_id = await self.session.scalar(
            select(Inventory.id)
            .where(
                Inventory.product_id == product_id,
                Inventory.status == InventoryStatus.AVAILABLE.value,
            )
            .order_by(Inventory.id)
            .limit(1)
        )
        if candidate_id is None:
            return None

        result = await self.session.execute(
            update(Inventory)
            .where(
                Inventory.id == candidate_id,
                Inventory.status == InventoryStatus.AVAILABLE.value,
            )
            .values(
                status=InventoryStatus.RESERVED.value, reserved_order_id=order_id
            )
        )
        await self.session.flush()
        if result.rowcount != 1:
            return None
        return await self.session.get(Inventory, candidate_id)

    async def reserve_many(
        self, product_id: int, order_id: int, quantity: int
    ) -> list[Inventory] | None:
        """Atomically reserve *quantity* items for the order.

        Idempotent: items already reserved/sold for this order count toward the
        quantity. Returns the order's reserved items (len == quantity), or None
        if there is not enough stock (in which case NOTHING new is reserved).
        """
        existing = list(
            await self.session.scalars(
                select(Inventory).where(
                    Inventory.reserved_order_id == order_id,
                    Inventory.status.in_(
                        [InventoryStatus.RESERVED.value, InventoryStatus.SOLD.value]
                    ),
                ).order_by(Inventory.id)
            )
        )
        need = quantity - len(existing)
        if need <= 0:
            return existing[:quantity]

        candidate_ids = list(
            await self.session.scalars(
                select(Inventory.id)
                .where(
                    Inventory.product_id == product_id,
                    Inventory.status == InventoryStatus.AVAILABLE.value,
                )
                .order_by(Inventory.id)
                .limit(need)
            )
        )
        if len(candidate_ids) < need:
            return None

        result = await self.session.execute(
            update(Inventory)
            .where(
                Inventory.id.in_(candidate_ids),
                Inventory.status == InventoryStatus.AVAILABLE.value,
            )
            .values(
                status=InventoryStatus.RESERVED.value, reserved_order_id=order_id
            )
        )
        await self.session.flush()
        if result.rowcount != need:
            await self.session.rollback()
            return None
        return await self.list_reserved_for_order(order_id)

    async def list_reserved_for_order(self, order_id: int) -> list[Inventory]:
        return list(
            await self.session.scalars(
                select(Inventory).where(
                    Inventory.reserved_order_id == order_id
                ).order_by(Inventory.id)
            )
        )

    async def get_reserved_for_order(self, order_id: int) -> Inventory | None:
        return await self.session.scalar(
            select(Inventory).where(Inventory.reserved_order_id == order_id)
        )

    async def mark_many_sold(self, order_id: int, delivered_at: datetime) -> None:
        for item in await self.list_reserved_for_order(order_id):
            if item.status != InventoryStatus.SOLD.value:
                item.status = InventoryStatus.SOLD.value
                item.delivered_at = delivered_at
        await self.session.flush()

    async def mark_sold(self, inventory_id: int, delivered_at: datetime) -> None:
        item = await self.session.get(Inventory, inventory_id)
        if item is not None:
            item.status = InventoryStatus.SOLD.value
            item.delivered_at = delivered_at
            await self.session.flush()

    async def release(self, order_id: int) -> None:
        """Return all reserved-but-not-sold items to the pool (on cancel)."""
        items = await self.session.scalars(
            select(Inventory).where(
                Inventory.reserved_order_id == order_id,
                Inventory.status == InventoryStatus.RESERVED.value,
            )
        )
        for item in items:
            item.status = InventoryStatus.AVAILABLE.value
            item.reserved_order_id = None
        await self.session.flush()


class OrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        order_code: str,
        telegram_user_id: int,
        product_id: int,
        price: int,
        expires_at: datetime,
        quantity: int = 1,
        unit_price: int = 0,
    ) -> Order:
        order = Order(
            order_code=order_code,
            telegram_user_id=telegram_user_id,
            product_id=product_id,
            quantity=quantity,
            unit_price=unit_price,
            price=price,
            status=OrderStatus.WAITING_PAYMENT.value,
            expires_at=expires_at,
        )
        self.session.add(order)
        await self.session.flush()
        return order

    async def get(self, order_id: int) -> Order | None:
        return await self.session.get(Order, order_id)

    async def get_by_code(self, order_code: str) -> Order | None:
        return await self.session.scalar(
            select(Order).where(Order.order_code == order_code)
        )

    async def get_active_for_user_product(
        self, telegram_user_id: int, product_id: int
    ) -> Order | None:
        return await self.session.scalar(
            select(Order).where(
                Order.telegram_user_id == telegram_user_id,
                Order.product_id == product_id,
                Order.status.in_(
                    [
                        OrderStatus.WAITING_PAYMENT.value,
                        OrderStatus.CHECKING_PAYMENT.value,
                        OrderStatus.ACCEPTING_PAYMENT.value,
                        OrderStatus.PAID.value,
                        OrderStatus.DELIVERING.value,
                    ]
                ),
            )
        )

    async def find_by_link_id(self, paypay_link_id: str) -> Order | None:
        return await self.session.scalar(
            select(Order).where(Order.paypay_link_id == paypay_link_id)
        )

    async def list_recent(self, limit: int = 20) -> Sequence[Order]:
        return (
            await self.session.scalars(
                select(Order).order_by(Order.id.desc()).limit(limit)
            )
        ).all()

    async def list_for_user_by_status(
        self, telegram_user_id: int, statuses: Sequence[str]
    ) -> Sequence[Order]:
        return (
            await self.session.scalars(
                select(Order).where(
                    Order.telegram_user_id == telegram_user_id,
                    Order.status.in_(list(statuses)),
                )
            )
        ).all()

    async def list_by_status(self, statuses: Sequence[str]) -> Sequence[Order]:
        return (
            await self.session.scalars(
                select(Order).where(Order.status.in_(list(statuses)))
            )
        ).all()


class PaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        order_id: int,
        provider: str,
        amount: int,
        paypay_link_id: str | None,
        external_payment_id: str | None,
        status: str,
        raw_response: str | None = None,
    ) -> Payment:
        payment = Payment(
            order_id=order_id,
            provider=provider,
            amount=amount,
            paypay_link_id=paypay_link_id,
            external_payment_id=external_payment_id,
            status=status,
            raw_response=raw_response,
        )
        self.session.add(payment)
        await self.session.flush()
        return payment

    async def get_for_order(self, order_id: int) -> Payment | None:
        return await self.session.scalar(
            select(Payment).where(Payment.order_id == order_id)
        )

    async def find_by_external_id(
        self, provider: str, external_payment_id: str
    ) -> Payment | None:
        return await self.session.scalar(
            select(Payment).where(
                Payment.provider == provider,
                Payment.external_payment_id == external_payment_id,
            )
        )

    async def find_by_link_id(
        self, provider: str, paypay_link_id: str
    ) -> Payment | None:
        return await self.session.scalar(
            select(Payment).where(
                Payment.provider == provider,
                Payment.paypay_link_id == paypay_link_id,
            )
        )
