"""Regressions for the abuse/robustness fixes.

All mock-based: no network, no real PayPay.
"""
from __future__ import annotations

import pytest

from conftest import Shop, make_product_with_stock, mock_link
from database.models import OrderStatus
from services import pricing
from services.payment_service import PurchaseOutcome


async def _order(shop: Shop, user: int, pid: int, quantity: int = 1) -> int:
    res = await shop.orders.create_order(user, pid, quantity)
    assert res is not None and not res.out_of_stock
    return res.order_id


async def _status(shop: Shop, oid: int) -> str:
    return (await shop.orders.get(oid)).status


# ---- (4) receipt we did not get must never deliver ------------------------- #
async def test_already_received_link_is_not_delivered(shop: Shop) -> None:
    """Link is PENDING at inspect but someone else receives it before we accept.

    We must NOT hand over goods for money that may never have reached us.
    """
    pid = await make_product_with_stock(shop, price=500, stock=2)
    oid = await _order(shop, 1, pid)
    shop.provider.already_on_accept.add("RACE")
    r = await shop.payments.process_payment_link(oid, mock_link(500, "RACE"))
    assert r.outcome == PurchaseOutcome.PAYMENT_UNKNOWN
    assert r.delivered_contents is None
    assert await _status(shop, oid) != OrderStatus.DELIVERED.value
    assert await shop.inventory.count_available(pid) == 2


# ---- (1) quantity changed during inspection cannot underpay ---------------- #
async def test_reprice_during_processing_is_rejected(shop: Shop) -> None:
    """Buyer pays for 1, then enlarges the order to 10 mid-flight.

    The re-read price must reject the now-too-small payment instead of
    delivering 10 for the price of 1.
    """
    pid = await make_product_with_stock(shop, price=500, stock=20)
    await shop.products.set_tiers(pid, pricing.parse_tiers_text("1:500,10:5000"))
    oid = await _order(shop, 1, pid, quantity=1)  # expects 500

    # Simulate the buyer re-pricing the same waiting order to qty=10 (=5000)
    # while it is still WAITING_PAYMENT (as happens during the slow inspect).
    again = await shop.orders.create_order(1, pid, quantity=10)
    assert again.order_id == oid and again.price == 5000

    # The old 500 link must no longer be accepted.
    r = await shop.payments.process_payment_link(oid, mock_link(500, "OLD"))
    assert r.outcome == PurchaseOutcome.AMOUNT_MISMATCH
    assert r.delivered_contents is None
    assert await shop.inventory.count_available(pid) == 20

    # Paying the correct new amount still works and delivers exactly 10.
    ok = await shop.payments.process_payment_link(oid, mock_link(5000, "NEW"))
    assert ok.outcome == PurchaseOutcome.PAID_NOT_DELIVERED
    assert len(ok.delivered_contents) == 10


# ---- (2) unknown provider must fail loudly, never silently mock ------------ #
def test_unknown_provider_raises() -> None:
    import main
    from paypay.client import PayPayClient

    with pytest.raises(SystemExit):
        main._build_provider("payay", PayPayClient())  # typo
    # valid values still work
    assert main._build_provider("mock", PayPayClient()).name == "mock"
    assert main._build_provider("paypay", PayPayClient()).name == "paypay"


# ---- (5) the raw link code must not leak into logs ------------------------- #
def test_httpx_url_logging_is_suppressed() -> None:
    """httpx logs request URLs (with ?verificationCode=...) at INFO; that must
    be raised to WARNING so live links never land in the log file."""
    import logging
    import main

    main._configure_logging("INFO")
    assert logging.getLogger("httpx").level == logging.WARNING
