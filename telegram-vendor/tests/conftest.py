"""Shared pytest fixtures: isolated DB + wired services per test."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Make src/ importable.
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from database.models import Base  # noqa: E402
from payments.mock import MockPaymentProvider  # noqa: E402
from services.inventory_service import InventoryService  # noqa: E402
from services.order_service import OrderService  # noqa: E402
from services.payment_service import PaymentService  # noqa: E402
from services.product_service import ProductService  # noqa: E402


@dataclass
class Shop:
    sm: async_sessionmaker
    products: ProductService
    inventory: InventoryService
    orders: OrderService
    payments: PaymentService
    provider: MockPaymentProvider


@pytest_asyncio.fixture
async def shop(tmp_path) -> Shop:
    db_path = tmp_path / "test.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    provider = MockPaymentProvider()
    shop = Shop(
        sm=sm,
        products=ProductService(sm),
        inventory=InventoryService(sm),
        orders=OrderService(sm, ttl_seconds=600),
        payments=PaymentService(sm, provider),
        provider=provider,
    )
    yield shop
    await engine.dispose()


async def make_product_with_stock(
    shop: Shop, name: str = "商品A", price: int = 500, stock: int = 3
) -> int:
    pid = await shop.products.add(name, price, "desc")
    if stock:
        await shop.inventory.add_bulk(
            pid, "\n".join(f"CODE-{name}-{i}" for i in range(stock))
        )
    return pid


def mock_link(amount: int, link_id: str) -> str:
    return f"https://example.local/pay/{amount}/{link_id}"
