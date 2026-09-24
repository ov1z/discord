"""Order creation, code format/uniqueness, double-press idempotency, expiry."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from conftest import Shop, make_product_with_stock
from database.models import OrderStatus
from database.repository import OrderRepository
from services.order_service import generate_order_code


def test_order_code_format() -> None:
    code = generate_order_code()
    assert re.fullmatch(r"ORD-[A-Z2-9]{6}", code)


async def test_create_order(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500)
    result = await shop.orders.create_order(42, pid)
    assert result is not None
    assert result.price == 500
    assert result.order_code.startswith("ORD-")


async def test_double_press_reuses_order(shop: Shop) -> None:
    pid = await make_product_with_stock(shop)
    first = await shop.orders.create_order(42, pid)
    second = await shop.orders.create_order(42, pid)
    assert first and second
    assert first.order_id == second.order_id
    assert second.reused is True


async def test_create_order_missing_product(shop: Shop) -> None:
    assert await shop.orders.create_order(1, 9999) is None


async def test_expiry(shop: Shop) -> None:
    pid = await make_product_with_stock(shop)
    result = await shop.orders.create_order(7, pid)
    assert result is not None
    async with shop.sm() as session:
        order = await OrderRepository(session).get(result.order_id)
        order.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()
    assert await shop.orders.expire_if_due(result.order_id) is True
    order = await shop.orders.get(result.order_id)
    assert order.status == OrderStatus.EXPIRED.value
