"""Abuse-resistance: no goods without a real, matching payment."""
from __future__ import annotations

from conftest import Shop, make_product_with_stock, mock_link
from database.models import OrderStatus
from database.repository import OrderRepository
from services.payment_service import PurchaseOutcome


async def _order(shop: Shop, user: int, pid: int, qty: int = 1) -> int:
    res = await shop.orders.create_order(user, pid, qty)
    assert res is not None and not res.out_of_stock
    return res.order_id


async def _status(shop: Shop, oid: int) -> str:
    return (await shop.orders.get(oid)).status


async def test_invalid_link_gives_nothing(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=3)
    oid = await _order(shop, 1, pid)
    for bad in ("not-a-link", "https://evil.example/x", "https://pay.paypay.ne.jp/"):
        r = await shop.payments.process_payment_link(oid, bad)
        assert r.outcome == PurchaseOutcome.INVALID_LINK
        assert r.delivered_contents is None
    assert await _status(shop, oid) == OrderStatus.WAITING_PAYMENT.value
    assert await shop.inventory.count_available(pid) == 3


async def test_unaccepted_link_never_delivers(shop: Shop) -> None:
    """A link that inspects fine but can never be accepted yields no goods."""
    pid = await make_product_with_stock(shop, price=500, stock=1)
    oid = await _order(shop, 1, pid)
    shop.provider.fail_accept.add("NOACC")
    r = await shop.payments.process_payment_link(oid, mock_link(500, "NOACC"))
    assert r.delivered_contents is None
    assert r.outcome in (PurchaseOutcome.FAILED, PurchaseOutcome.PAYMENT_UNKNOWN)
    assert await _status(shop, oid) != OrderStatus.DELIVERED.value
    assert await shop.inventory.count_available(pid) == 1


async def test_wrong_amount_never_delivers(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=1)
    oid = await _order(shop, 1, pid)
    for amt in (1, 300, 499, 501, 1000):
        r = await shop.payments.process_payment_link(oid, mock_link(amt, f"A{amt}"))
        assert r.outcome == PurchaseOutcome.AMOUNT_MISMATCH
        assert r.delivered_contents is None
    assert await shop.inventory.count_available(pid) == 1


async def test_buyer_cannot_cancel_other_users_order(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=1)
    victim = await _order(shop, 111, pid)
    code = (await shop.orders.get(victim)).order_code
    assert await shop.orders.cancel_for_user(code, 999) is False
    assert await _status(shop, victim) == OrderStatus.WAITING_PAYMENT.value
    assert await shop.orders.cancel_for_user(code, 111) is True


async def test_buyer_cannot_cancel_paid_order(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=1)
    oid = await _order(shop, 1, pid)
    async with shop.sm() as session:
        order = await OrderRepository(session).get(oid)
        order.status = OrderStatus.PAID.value
        await session.commit()
    code = (await shop.orders.get(oid)).order_code
    assert await shop.orders.cancel_for_user(code, 1) is False
