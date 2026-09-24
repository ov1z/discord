"""Inline keyboards for the shop: product list, detail, quantity picker."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services import pricing
from services.product_service import ProductView


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def shop_list_keyboard(products: list[ProductView]) -> InlineKeyboardMarkup:
    """One button per product; tapping opens the detail (price/stock/quantity)."""
    rows: list[list[InlineKeyboardButton]] = []
    for p in products:
        stock = f"在庫{p.available_stock}" if p.available_stock > 0 else "入荷待ち"
        label = f"{p.name}｜¥{p.price:,}｜{stock}"
        rows.append([_btn(label, f"shop:view:{p.id}")])
    if not rows:
        rows.append([_btn("商品はまだありません", "noop")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def product_detail_keyboard(view: ProductView) -> InlineKeyboardMarkup:
    """Quantity buttons (one per priced quantity) + custom quantity + back.

    With ``show_bulk_buttons`` off only the 1-unit button is listed; buyers can
    still pick any quantity through the free quantity input.
    """
    rows: list[list[InlineKeyboardButton]] = []
    if view.available_stock <= 0:
        rows.append([_btn("🔔 入荷待ち", "noop")])
    else:
        tiers = view.tiers
        options = pricing.quantity_options(tiers) if view.show_bulk_buttons else [1]
        for qty in options:
            if qty > view.available_stock:
                continue
            total = pricing.total_for(qty, tiers)
            if qty == 1:
                label = f"{qty}個 ({total:,}円)"
            else:
                unit = pricing.unit_price_for(qty, tiers)
                label = f"{qty}個 ({total:,}円 / @{unit:,}円)"
            rows.append([_btn(label, f"shop:buy:{view.id}:{qty}")])
        rows.append([_btn("🔢 数量を入力", f"shop:qty:{view.id}")])
    rows.append([_btn("◀️ 商品一覧に戻る", "shop:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
