"""Per-quantity pricing + multi-quantity purchase."""
from __future__ import annotations

import json

from conftest import Shop, make_product_with_stock, mock_link
from database.models import OrderStatus
from services import pricing
from services.payment_service import PurchaseOutcome


def test_each_quantity_has_its_own_total() -> None:
    raw = pricing.parse_tiers_text("1:500,2:900,3:1300,10:4000")
    tiers = pricing.parse_tiers(raw, 0)
    assert pricing.total_for(1, tiers) == 500
    assert pricing.total_for(2, tiers) == 900
    assert pricing.total_for(3, tiers) == 1300
    assert pricing.total_for(10, tiers) == 4000
    assert pricing.quantity_options(tiers) == [1, 2, 3, 10]


def test_totals_are_not_locked_to_unit_price_times_quantity() -> None:
    """A total that no integer unit price could produce is honoured exactly."""
    tiers = pricing.parse_tiers(pricing.parse_tiers_text("1:500,3:1300"), 0)
    assert pricing.total_for(3, tiers) == 1300
    assert pricing.unit_price_for(3, tiers) == 433


def test_unpriced_quantity_is_prorated_from_nearest_lower_entry() -> None:
    tiers = pricing.parse_tiers(pricing.parse_tiers_text("1:500,3:1300,10:4000"), 0)
    assert pricing.total_for(2, tiers) == 1000
    assert pricing.total_for(5, tiers) == 2167
    assert pricing.total_for(12, tiers) == 4800


def test_flat_price_when_no_table() -> None:
    tiers = pricing.parse_tiers(None, 800)
    assert pricing.total_for(1, tiers) == 800
    assert pricing.total_for(4, tiers) == 3200
    assert pricing.quantity_options(tiers) == [1]


def test_legacy_unit_price_rows_keep_their_prices() -> None:
    """Products saved before the change stored a unit price per threshold."""
    legacy = json.dumps([{"min": 1, "price": 1800}, {"min": 5, "price": 1600}])
    tiers = pricing.parse_tiers(legacy, 0)
    assert pricing.total_for(1, tiers) == 1800
    assert pricing.total_for(5, tiers) == 8000
    assert pricing.total_for(7, tiers) == 11200


def test_parse_tiers_text_invalid() -> None:
    assert pricing.parse_tiers_text("garbage") is None
    assert pricing.parse_tiers_text("") is None
    assert pricing.parse_tiers_text("0:500") is None
    assert pricing.parse_tiers_text("1:500,1:600") is None


def test_quantity_1_is_always_priced() -> None:
    tiers = pricing.parse_tiers(pricing.parse_tiers_text("4:1000"), 0)
    assert tiers[0].quantity == 1
    assert pricing.total_for(1, tiers) == 250


async def test_order_uses_the_quantity_total(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=20)
    await shop.products.set_tiers(pid, pricing.parse_tiers_text("1:500,2:900,3:1300"))
    res = await shop.orders.create_order(5, pid, quantity=3)
    assert res is not None
    assert res.quantity == 3
    assert res.price == 1300
    assert res.unit_price == 433


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
    order = await shop.orders.get(res.order_id)
    assert order.status == OrderStatus.DELIVERED.value
    assert await shop.inventory.count_available(pid) == 7


async def test_buyer_must_pay_the_configured_total(shop: Shop) -> None:
    """The per-quantity total is what the payment link must match."""
    pid = await make_product_with_stock(shop, price=500, stock=10)
    await shop.products.set_tiers(pid, pricing.parse_tiers_text("1:500,3:1300"))
    res = await shop.orders.create_order(7, pid, quantity=3)
    assert res is not None and res.price == 1300
    result = await shop.payments.process_payment_link(
        res.order_id, mock_link(1500, "SHORT")
    )
    assert result.outcome == PurchaseOutcome.AMOUNT_MISMATCH
    assert result.expected_amount == 1300
    ok = await shop.payments.process_payment_link(
        res.order_id, mock_link(1300, "EXACT")
    )
    assert ok.outcome == PurchaseOutcome.PAID_NOT_DELIVERED


async def test_multi_quantity_amount_must_match_total(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=1000, stock=10)
    res = await shop.orders.create_order(7, pid, quantity=3)
    assert res is not None
    result = await shop.payments.process_payment_link(
        res.order_id, mock_link(1000, "SHORT")
    )
    assert result.outcome == PurchaseOutcome.AMOUNT_MISMATCH
    assert result.expected_amount == 3000


async def test_reprice_on_quantity_change_before_payment(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=1800, stock=20)
    await shop.products.set_tiers(pid, pricing.parse_tiers_text("1:1800,5:8000"))
    first = await shop.orders.create_order(9, pid, quantity=1)
    second = await shop.orders.create_order(9, pid, quantity=5)
    assert first and second
    assert first.order_id == second.order_id
    assert second.quantity == 5 and second.price == 8000
