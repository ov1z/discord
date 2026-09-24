"""Admin toggle for the multi-quantity buttons on the product screen."""
from __future__ import annotations

from bot.keyboards.products import product_detail_keyboard
from conftest import Shop, make_product_with_stock
from services import pricing


def _quantities(markup) -> list[int]:
    """Quantities offered by the buy buttons of a product keyboard."""
    return [
        int(b.callback_data.split(":")[3])
        for row in markup.inline_keyboard
        for b in row
        if b.callback_data and b.callback_data.startswith("shop:buy:")
    ]


async def test_bulk_buttons_shown_by_default(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=20)
    await shop.products.set_tiers(pid, pricing.parse_tiers_text("1:500,2:900,3:1300"))
    view = await shop.products.get_view(pid)
    assert view.show_bulk_buttons is True
    assert _quantities(product_detail_keyboard(view)) == [1, 2, 3]


async def test_hiding_bulk_buttons_leaves_only_one_unit(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=20)
    await shop.products.set_tiers(pid, pricing.parse_tiers_text("1:500,2:900,3:1300"))
    assert await shop.products.set_bulk_buttons(pid, False) is True
    view = await shop.products.get_view(pid)
    assert view.show_bulk_buttons is False
    markup = product_detail_keyboard(view)
    assert _quantities(markup) == [1]
    data = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert f"shop:qty:{pid}" in data


async def test_hidden_buttons_do_not_change_prices(shop: Shop) -> None:
    """Hiding the buttons only affects the keyboard, not what a quantity costs."""
    pid = await make_product_with_stock(shop, price=500, stock=20)
    await shop.products.set_tiers(pid, pricing.parse_tiers_text("1:500,3:1300"))
    await shop.products.set_bulk_buttons(pid, False)
    res = await shop.orders.create_order(42, pid, quantity=3)
    assert res is not None and res.price == 1300


async def test_toggle_flips_and_reports_the_new_value(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=1)
    assert await shop.products.toggle_bulk_buttons(pid) is False
    assert await shop.products.toggle_bulk_buttons(pid) is True
    assert await shop.products.toggle_bulk_buttons(9999) is None


async def test_buttons_beyond_stock_are_not_offered(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=2)
    await shop.products.set_tiers(pid, pricing.parse_tiers_text("1:500,2:900,5:2000"))
    view = await shop.products.get_view(pid)
    assert _quantities(product_detail_keyboard(view)) == [1, 2]
