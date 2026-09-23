"""End-to-end purchase pipeline via PaymentService + MockPaymentProvider.

Covers: success, amount under/over, invalid link, used link, expired order,
out of stock, accept timeout, post-accept timeout, double submit idempotency,
delivery retry, and startup-recovery-style re-delivery.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from conftest import Shop, make_product_with_stock, mock_link
from database.models import OrderStatus
from database.repository import OrderRepository
from services.payment_service import PurchaseOutcome


async def _new_order(shop: Shop, user: int, pid: int) -> int:
    res = await shop.orders.create_order(user, pid)
    assert res is not None
    return res.order_id


async def _expire(shop: Shop, order_id: int) -> None:
    async with shop.sm() as session:
        order = await OrderRepository(session).get(order_id)
        order.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()


async def _status(shop: Shop, order_id: int) -> str:
    order = await shop.orders.get(order_id)
    return order.status


# --------------------------------------------------------------------------- #
async def test_successful_purchase(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=2)
    oid = await _new_order(shop, 1, pid)
    result = await shop.payments.process_payment_link(oid, mock_link(500, "OK1"))
    assert result.outcome == PurchaseOutcome.PAID_NOT_DELIVERED
    assert result.delivered_content is not None
    assert await _status(shop, oid) == OrderStatus.DELIVERING.value
    # Buyer received it -> confirm.
    await shop.payments.confirm_delivered(oid)
    assert await _status(shop, oid) == OrderStatus.DELIVERED.value


async def test_amount_insufficient(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500)
    oid = await _new_order(shop, 1, pid)
    result = await shop.payments.process_payment_link(oid, mock_link(300, "LO"))
    assert result.outcome == PurchaseOutcome.AMOUNT_MISMATCH
    assert result.actual_amount == 300
    assert await _status(shop, oid) == OrderStatus.WAITING_PAYMENT.value


async def test_amount_excess_not_accepted(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500)
    oid = await _new_order(shop, 1, pid)
    result = await shop.payments.process_payment_link(oid, mock_link(600, "HI"))
    assert result.outcome == PurchaseOutcome.AMOUNT_MISMATCH
    # Not received.
    info = await shop.provider.inspect_payment(mock_link(600, "HI"))
    assert info.status.value == "PENDING"


async def test_invalid_link(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500)
    oid = await _new_order(shop, 1, pid)
    result = await shop.payments.process_payment_link(oid, "not-a-link")
    assert result.outcome == PurchaseOutcome.INVALID_LINK


async def test_used_link_rejected_for_second_order(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=5)
    o1 = await _new_order(shop, 1, pid)
    r1 = await shop.payments.process_payment_link(o1, mock_link(500, "SHARED"))
    assert r1.outcome == PurchaseOutcome.PAID_NOT_DELIVERED

    o2 = await _new_order(shop, 2, pid)
    r2 = await shop.payments.process_payment_link(o2, mock_link(500, "SHARED"))
    assert r2.outcome == PurchaseOutcome.LINK_ALREADY_USED


async def test_expired_order(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500)
    oid = await _new_order(shop, 1, pid)
    await _expire(shop, oid)
    result = await shop.payments.process_payment_link(oid, mock_link(500, "EXP"))
    assert result.outcome == PurchaseOutcome.ORDER_EXPIRED
    assert await _status(shop, oid) == OrderStatus.EXPIRED.value


async def test_out_of_stock_keeps_money(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=0)
    oid = await _new_order(shop, 1, pid)
    result = await shop.payments.process_payment_link(oid, mock_link(500, "NS"))
    assert result.outcome == PurchaseOutcome.OUT_OF_STOCK
    # Money confirmed: order left in DELIVERING for later re-delivery.
    assert await _status(shop, oid) == OrderStatus.DELIVERING.value


async def test_timeout_on_accept_is_payment_unknown(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500)
    oid = await _new_order(shop, 1, pid)
    shop.provider.timeout_on_accept.add("TMO")
    result = await shop.payments.process_payment_link(oid, mock_link(500, "TMO"))
    assert result.outcome == PurchaseOutcome.PAYMENT_UNKNOWN
    assert await _status(shop, oid) == OrderStatus.PAYMENT_UNKNOWN.value


async def test_timeout_after_accept_is_payment_unknown(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500)
    oid = await _new_order(shop, 1, pid)
    shop.provider.timeout_after_accept.add("TAA")
    result = await shop.payments.process_payment_link(oid, mock_link(500, "TAA"))
    assert result.outcome == PurchaseOutcome.PAYMENT_UNKNOWN
    assert await _status(shop, oid) == OrderStatus.PAYMENT_UNKNOWN.value


async def test_double_submit_is_idempotent(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=2)
    oid = await _new_order(shop, 1, pid)
    r1 = await shop.payments.process_payment_link(oid, mock_link(500, "DUP"))
    assert r1.outcome == PurchaseOutcome.PAID_NOT_DELIVERED
    # Second submit while DELIVERING: no double accept, no second item.
    r2 = await shop.payments.process_payment_link(oid, mock_link(500, "DUP"))
    assert r2.outcome == PurchaseOutcome.PAID_NOT_DELIVERED
    assert r2.delivered_content is None
    assert await shop.inventory.count_available(pid) == 1  # only one consumed


async def test_delivery_retry_after_send_failure(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=1)
    oid = await _new_order(shop, 1, pid)
    r1 = await shop.payments.process_payment_link(oid, mock_link(500, "RETRY"))
    content1 = r1.delivered_content
    assert content1 is not None
    # Simulate Telegram send failure: do NOT confirm. Order stays DELIVERING.
    assert await _status(shop, oid) == OrderStatus.DELIVERING.value
    # Retry (admin /retry_delivery or startup recovery).
    r2 = await shop.payments.deliver_order(oid)
    assert r2.delivered_content == content1  # same reserved item
    await shop.payments.confirm_delivered(oid)
    assert await _status(shop, oid) == OrderStatus.DELIVERED.value


async def test_product_notes_persist_for_delivery(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=1)
    await shop.products.set_notes(pid, "初回起動時にライセンス認証してください")
    view = next(p for p in await shop.products.list_all() if p.id == pid)
    assert view.notes == "初回起動時にライセンス認証してください"
    # A delivered order carries the content; the note is attached at send time
    # from the product record (see bot/handlers/payment.deliver_to_buyer).
    oid = await _new_order(shop, 1, pid)
    result = await shop.payments.process_payment_link(oid, mock_link(500, "NOTE"))
    assert result.delivered_content is not None
    product = await shop.products.get(pid)
    assert product.notes == "初回起動時にライセンス認証してください"


async def test_startup_recovery_redelivers_paid(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=1)
    oid = await _new_order(shop, 1, pid)
    # Put order into PAID directly (money in, never delivered).
    await shop.payments._set_status(oid, OrderStatus.PAID)
    result = await shop.payments.deliver_order(oid)
    assert result.delivered_content is not None
    await shop.payments.confirm_delivered(oid)
    assert await _status(shop, oid) == OrderStatus.DELIVERED.value
