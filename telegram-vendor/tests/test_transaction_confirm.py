"""Settling an order from the shop's own payment history.

The buyer supplies only a transaction number; every fact that decides whether
goods are handed over is read back from the provider by us.
"""
from __future__ import annotations

from conftest import Shop, make_product_with_stock
from database.models import OrderStatus
from services.payment_service import PurchaseOutcome


async def _order(shop: Shop, price: int = 500, stock: int = 5, user: int = 42):
    pid = await make_product_with_stock(shop, price=price, stock=stock)
    res = await shop.orders.create_order(user, pid, quantity=1)
    assert res is not None
    return pid, res


async def test_matching_transaction_delivers(shop: Shop) -> None:
    pid, res = await _order(shop, price=500)
    shop.provider.add_incoming("02366005000515321858", 500)

    result = await shop.payments.confirm_by_transaction(
        res.order_id, "02366005000515321858"
    )
    assert result.outcome == PurchaseOutcome.PAID_NOT_DELIVERED
    assert result.delivered_contents and len(result.delivered_contents) == 1

    await shop.payments.confirm_delivered(res.order_id)
    order = await shop.orders.get(res.order_id)
    assert order.status == OrderStatus.DELIVERED.value
    assert await shop.inventory.count_available(pid) == 4


async def test_unknown_transaction_number_is_refused(shop: Shop) -> None:
    """A number the shop cannot see in its own history proves nothing."""
    _, res = await _order(shop, price=500)
    shop.provider.add_incoming("11111111", 500)

    result = await shop.payments.confirm_by_transaction(res.order_id, "99999999")
    assert result.outcome == PurchaseOutcome.TRANSACTION_NOT_FOUND
    order = await shop.orders.get(res.order_id)
    assert order.status == OrderStatus.WAITING_PAYMENT.value


async def test_wrong_amount_is_refused(shop: Shop) -> None:
    _, res = await _order(shop, price=500)
    shop.provider.add_incoming("22222222", 100)

    result = await shop.payments.confirm_by_transaction(res.order_id, "22222222")
    assert result.outcome == PurchaseOutcome.AMOUNT_MISMATCH
    assert result.expected_amount == 500
    assert result.actual_amount == 100


async def test_outgoing_transaction_is_refused(shop: Shop) -> None:
    """A payment the shop SENT must never settle an order."""
    _, res = await _order(shop, price=500)
    shop.provider.add_incoming("33333333", 500, incoming=False)

    result = await shop.payments.confirm_by_transaction(res.order_id, "33333333")
    assert result.outcome == PurchaseOutcome.TRANSACTION_NOT_INCOMING


async def test_pending_transaction_is_refused(shop: Shop) -> None:
    _, res = await _order(shop, price=500)
    shop.provider.add_incoming("44444444", 500, status="PENDING")

    result = await shop.payments.confirm_by_transaction(res.order_id, "44444444")
    assert result.outcome == PurchaseOutcome.TRANSACTION_NOT_COMPLETED


async def test_transaction_cannot_settle_two_orders(shop: Shop) -> None:
    pid, first = await _order(shop, price=500, user=1)
    second = await shop.orders.create_order(2, pid, quantity=1)
    assert second is not None
    shop.provider.add_incoming("55555555", 500)

    ok = await shop.payments.confirm_by_transaction(first.order_id, "55555555")
    assert ok.outcome == PurchaseOutcome.PAID_NOT_DELIVERED

    reused = await shop.payments.confirm_by_transaction(second.order_id, "55555555")
    assert reused.outcome == PurchaseOutcome.LINK_ALREADY_USED
    order = await shop.orders.get(second.order_id)
    assert order.status == OrderStatus.WAITING_PAYMENT.value


async def test_confirming_twice_is_idempotent(shop: Shop) -> None:
    pid, res = await _order(shop, price=500, stock=5)
    shop.provider.add_incoming("66666666", 500)

    first = await shop.payments.confirm_by_transaction(res.order_id, "66666666")
    assert first.outcome == PurchaseOutcome.PAID_NOT_DELIVERED
    again = await shop.payments.confirm_by_transaction(res.order_id, "66666666")
    assert again.outcome == PurchaseOutcome.PAID_NOT_DELIVERED
    assert await shop.inventory.count_available(pid) == 4


async def test_only_the_recent_window_counts(shop: Shop) -> None:
    """Older transactions fall outside the history the shop looks at."""
    _, res = await _order(shop, price=500)
    shop.provider.add_incoming("77777777", 500)
    for i in range(10):
        shop.provider.add_incoming(f"9000000{i}", 999)

    result = await shop.payments.confirm_by_transaction(
        res.order_id, "77777777", history_limit=10
    )
    assert result.outcome == PurchaseOutcome.TRANSACTION_NOT_FOUND


async def test_expired_order_is_not_settled(shop: Shop) -> None:
    from services.order_service import OrderService

    pid = await make_product_with_stock(shop, price=500, stock=3)
    expired = OrderService(shop.sm, ttl_seconds=-1)
    res = await expired.create_order(7, pid, quantity=1)
    assert res is not None
    shop.provider.add_incoming("88888888", 500)

    result = await shop.payments.confirm_by_transaction(res.order_id, "88888888")
    assert result.outcome == PurchaseOutcome.ORDER_EXPIRED
