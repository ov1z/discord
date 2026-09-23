"""Tiered pricing + multi-quantity purchase."""
from __future__ import annotations

from conftest import Shop, make_product_with_stock, mock_link
from database.models import OrderStatus
from services import pricing
from services.payment_service import PurchaseOutcome


def test_parse_and_unit_price() -> None:
    raw = pricing.parse_tiers_text("1:1800,5:1600,10:1500,50:1000")
    tiers = pricing.parse_tiers(raw, 0)
    assert pricing.unit_price_for(1, tiers) == 1800
    assert pricing.unit_price_for(4, tiers) == 1800
    assert pricing.unit_price_for(5, tiers) == 1600
    assert pricing.unit_price_for(9, tiers) == 1600
    assert pricing.unit_price_for(10, tiers) == 1500
    assert pricing.unit_price_for(60, tiers) == 1000
    assert pricing.total_for(5, tiers) == 8000
    assert pricing.quantity_options(tiers) == [1, 5, 10, 50]


def test_parse_tiers_text_invalid() -> None:
    assert pricing.parse_tiers_text("garbage") is None
    assert pricing.parse_tiers_text("") is None


async def test_order_uses_tier_price(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=1800, stock=20)
    await shop.products.set_tiers(pid, pricing.parse_tiers_text("1:1800,5:1600,10:1500"))
    res = await shop.orders.create_order(5, pid, quantity=5)
    assert res is not None
    assert res.quantity == 5
    assert res.unit_price == 1600
    assert res.price == 8000  # 5 * 1600


async def test_multi_quantity_purchase_delivers_all(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=1000, stock=10)
    res = await shop.orders.create_order(7, pid, quantity=3)
    assert res is not None and res.price == 3000
    result = await shop.payments.process_payment_link(
        res.order_id, mock_link(3000, "MULTI")
    )
    assert result.outcome == PurchaseOutcome.PAID_NOT_DELIVERED
    assert result.delivered_contents is not None
    assert len(result.delivered_contents) == 3
    await shop.payments.confirm_delivered(res.order_id)
    assert await shop.orders.get(res.order_id) is not None
    order = await shop.orders.get(res.order_id)
    assert order.status == OrderStatus.DELIVERED.value
    assert await shop.inventory.count_available(pid) == 7  # 10 - 3


async def test_multi_quantity_amount_must_match_total(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=1000, stock=10)
    res = await shop.orders.create_order(7, pid, quantity=3)
    assert res is not None
    # Sending only the single-unit price is rejected.
    result = await shop.payments.process_payment_link(
        res.order_id, mock_link(1000, "SHORT")
    )
    assert result.outcome == PurchaseOutcome.AMOUNT_MISMATCH
    assert result.expected_amount == 3000


async def test_reprice_on_quantity_change_before_payment(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=1800, stock=20)
    await shop.products.set_tiers(pid, pricing.parse_tiers_text("1:1800,5:1600"))
    first = await shop.orders.create_order(9, pid, quantity=1)
    second = await shop.orders.create_order(9, pid, quantity=5)
    assert first and second
    assert first.order_id == second.order_id  # same waiting order, re-priced
    assert second.quantity == 5 and second.price == 8000
