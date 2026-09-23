"""Product catalogue operations (admin)."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from database.models import Product
from database.repository import InventoryRepository, ProductRepository


from services import pricing


@dataclass(slots=True)
class ProductView:
    id: int
    name: str
    price: int
    description: str | None
    active: bool
    available_stock: int
    notes: str | None = None
    price_tiers: str | None = None

    @property
    def tiers(self) -> list["pricing.Tier"]:
        return pricing.parse_tiers(self.price_tiers, self.price)

    def unit_price(self, quantity: int) -> int:
        return pricing.unit_price_for(quantity, self.tiers)

    def total(self, quantity: int) -> int:
        return pricing.total_for(quantity, self.tiers)


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

    async def set_notes(self, product_id: int, notes: str) -> bool:
        """Set the per-product note shown to buyers after delivery."""
        async with self._sm() as session:
            updated = await ProductRepository(session).update_fields(
                product_id, notes=notes
            )
            await session.commit()
            return updated is not None

    async def set_tiers(self, product_id: int, tiers_json: str) -> bool:
        """Set the bulk-discount price tiers (stored JSON)."""
        async with self._sm() as session:
            updated = await ProductRepository(session).update_fields(
                product_id, price_tiers=tiers_json
            )
            await session.commit()
            return updated is not None

    async def get_view(self, product_id: int) -> ProductView | None:
        async with self._sm() as session:
            p = await ProductRepository(session).get(product_id)
            if p is None:
                return None
            stock = await InventoryRepository(session).count_available(p.id)
            return ProductView(
                p.id, p.name, p.price, p.description, p.active, stock,
                p.notes, p.price_tiers,
            )

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
                    ProductView(p.id, p.name, p.price, p.description, p.active, stock, p.notes, p.price_tiers)
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
                    ProductView(p.id, p.name, p.price, p.description, p.active, stock, p.notes, p.price_tiers)
                )
            return views
