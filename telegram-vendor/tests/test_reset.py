"""/start must always get the buyer back to a clean shop.

The dangerous half is what it must NOT cancel: once money is involved the
order has to survive so it can be delivered or investigated.
"""
from __future__ import annotations

from conftest import Shop, make_product_with_stock
from database.models import OrderStatus


async def test_unpaid_order_is_cancelled(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=3)
    res = await shop.orders.create_order(42, pid, quantity=1)
    assert res is not None

    assert await shop.orders.abandon_unpaid_for_user(42) == 1
    order = await shop.orders.get(res.order_id)
    assert order.status == OrderStatus.CANCELLED.value


async def test_other_buyers_are_untouched(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=3)
    mine = await shop.orders.create_order(42, pid, quantity=1)
    theirs = await shop.orders.create_order(99, pid, quantity=1)
    assert mine is not None and theirs is not None

    await shop.orders.abandon_unpaid_for_user(42)
    other = await shop.orders.get(theirs.order_id)
    assert other.status == OrderStatus.WAITING_PAYMENT.value


async def test_paid_orders_survive_a_reset(shop: Shop) -> None:
    """Money already received: cancelling would strand the buyer's payment."""
    pid = await make_product_with_stock(shop, price=500, stock=3)
    res = await shop.orders.create_order(42, pid, quantity=1)
    assert res is not None
    await shop.orders.mark_status(res.order_id, OrderStatus.PAID)

    assert await shop.orders.abandon_unpaid_for_user(42) == 0
    order = await shop.orders.get(res.order_id)
    assert order.status == OrderStatus.PAID.value


async def test_in_flight_and_unknown_orders_survive(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=5)
    for status in (
        OrderStatus.CHECKING_PAYMENT,
        OrderStatus.ACCEPTING_PAYMENT,
        OrderStatus.DELIVERING,
        OrderStatus.PAYMENT_UNKNOWN,
    ):
        res = await shop.orders.create_order(42, pid, quantity=1)
        assert res is not None
        await shop.orders.mark_status(res.order_id, status)
        assert await shop.orders.abandon_unpaid_for_user(42) == 0
        order = await shop.orders.get(res.order_id)
        assert order.status == status.value


async def test_reset_with_nothing_to_cancel(shop: Shop) -> None:
    assert await shop.orders.abandon_unpaid_for_user(12345) == 0


async def test_buyer_can_order_again_after_reset(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=3)
    first = await shop.orders.create_order(42, pid, quantity=1)
    assert first is not None

    await shop.orders.abandon_unpaid_for_user(42)
    second = await shop.orders.create_order(42, pid, quantity=2)
    assert second is not None
    assert second.order_id != first.order_id
    assert second.reused is False
    assert second.quantity == 2
