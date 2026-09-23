"""Inventory operations: bulk add, counts, atomic reservation."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from database.models import Inventory
from database.repository import InventoryRepository


class InventoryService:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def add_bulk(self, product_id: int, raw_text: str) -> int:
        """Add one inventory item per non-empty line."""
        lines = [ln for ln in raw_text.splitlines() if ln.strip()]
        async with self._sm() as session:
            added = await InventoryRepository(session).add_many(product_id, lines)
            await session.commit()
            return added

    async def count_available(self, product_id: int) -> int:
        async with self._sm() as session:
            return await InventoryRepository(session).count_available(product_id)

    async def counts_by_status(self, product_id: int) -> dict[str, int]:
        async with self._sm() as session:
            return await InventoryRepository(session).counts_by_status(product_id)

    async def list_for_product(self, product_id: int, limit: int = 50) -> list[Inventory]:
        async with self._sm() as session:
            return list(
                await InventoryRepository(session).list_for_product(product_id, limit)
            )

    async def reserve_for_order(self, product_id: int, order_id: int) -> Inventory | None:
        """Atomically reserve one item; returns it or None if out of stock."""
        async with self._sm() as session:
            item = await InventoryRepository(session).reserve_one(product_id, order_id)
            await session.commit()
            return item

    async def mark_delivered(self, inventory_id: int) -> None:
        async with self._sm() as session:
            await InventoryRepository(session).mark_sold(
                inventory_id, datetime.now(timezone.utc)
            )
            await session.commit()

    async def release(self, order_id: int) -> None:
        async with self._sm() as session:
            await InventoryRepository(session).release(order_id)
            await session.commit()
