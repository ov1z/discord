"""Product catalogue operations (admin)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from database.models import Product
from database.repository import InventoryRepository, ProductRepository


@dataclass(slots=True)
class ProductView:
    id: int
    name: str
    price: int
    description: str | None
    active: bool
    available_stock: int


class ProductService:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def add(self, name: str, price: int, description: str | None = None) -> int:
        async with self._sm() as session:
            product = await ProductRepository(session).create(name, price, description)
            await session.commit()
            return product.id

    async def edit(self, product_id: int, **fields) -> bool:
        async with self._sm() as session:
            updated = await ProductRepository(session).update_fields(
                product_id, **fields
            )
            await session.commit()
            return updated is not None

    async def delete(self, product_id: int) -> bool:
        async with self._sm() as session:
            ok = await ProductRepository(session).deactivate(product_id)
            await session.commit()
            return ok

    async def get(self, product_id: int) -> Product | None:
        async with self._sm() as session:
            return await ProductRepository(session).get(product_id)

    async def list_for_shop(self) -> list[ProductView]:
        async with self._sm() as session:
            prepo = ProductRepository(session)
            irepo = InventoryRepository(session)
            views: list[ProductView] = []
            for p in await prepo.list_active():
                stock = await irepo.count_available(p.id)
                views.append(
                    ProductView(p.id, p.name, p.price, p.description, p.active, stock)
                )
            return views

    async def list_all(self) -> list[ProductView]:
        async with self._sm() as session:
            prepo = ProductRepository(session)
            irepo = InventoryRepository(session)
            views: list[ProductView] = []
            for p in await prepo.list_all():
                stock = await irepo.count_available(p.id)
                views.append(
                    ProductView(p.id, p.name, p.price, p.description, p.active, stock)
                )
            return views
