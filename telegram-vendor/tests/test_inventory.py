"""Inventory: bulk add, atomic reservation, no double allocation, out of stock."""
from __future__ import annotations

import asyncio

import pytest

from conftest import Shop, make_product_with_stock


async def test_bulk_add_counts(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, stock=0)
    added = await shop.inventory.add_bulk(pid, "AAAA\nBBBB\nCCCC\n\n")
    assert added == 3
    assert await shop.inventory.count_available(pid) == 3


async def test_reserve_is_idempotent_per_order(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, stock=3)
    order = await shop.orders.create_order(111, pid)
    assert order is not None
    first = await shop.inventory.reserve_for_order(pid, order.order_id)
    second = await shop.inventory.reserve_for_order(pid, order.order_id)
    assert first is not None and second is not None
    assert first.id == second.id
    assert await shop.inventory.count_available(pid) == 2


async def test_concurrent_reserves_never_share_item(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, stock=1)
    o1 = await shop.orders.create_order(1, pid)
    o2 = await shop.orders.create_order(2, pid)
    assert o1 and o2
    r1, r2 = await asyncio.gather(
        shop.inventory.reserve_for_order(pid, o1.order_id),
        shop.inventory.reserve_for_order(pid, o2.order_id),
    )
    reserved = [r for r in (r1, r2) if r is not None]
    assert len(reserved) == 1
    assert await shop.inventory.count_available(pid) == 0


async def test_out_of_stock_returns_none(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, stock=0)
    order = await shop.orders.create_order(1, pid)
    assert order is not None
    assert await shop.inventory.reserve_for_order(pid, order.order_id) is None
