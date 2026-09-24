"""Inline keyboards for the shop: product list, detail, quantity picker."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services import pricing
from services.product_service import ProductView


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _link(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url)


def shop_list_keyboard(
    products: list[ProductView],
    support_url: str | None = None,
    channel_url: str | None = None,
) -> InlineKeyboardMarkup:
    """One button per product, then a footer of links (channel / support)."""
    rows: list[list[InlineKeyboardButton]] = []
    for p in products:
        if p.available_stock > 0:
            label = f"{p.name} ・ ¥{p.price:,}/個 ・ 在庫{p.available_stock}"
        else:
            label = f"{p.name} ・ ¥{p.price:,}/個 ・ 🈳売切"
        rows.append([_btn(label, f"shop:view:{p.id}")])
    if not rows:
        rows.append([_btn("商品はまだありません", "noop")])
    footer: list[InlineKeyboardButton] = []
    if channel_url:
        footer.append(_link("📣 販売CH", channel_url))
    if support_url:
        footer.append(_link("💬 サポート", support_url))
    if footer:
        rows.append(footer)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def agreement_keyboard(
    product_id: int,
    quantity: int,
    terms_url: str | None = None,
) -> InlineKeyboardMarkup:
    """Consent gate shown before payment: agree / change quantity / cancel."""
    rows: list[list[InlineKeyboardButton]] = [
        [_btn("✅ 同意して購入手続きへ", f"shop:agree:{product_id}:{quantity}")],
    ]
    if terms_url:
        rows.append([_link("📄 利用規約", terms_url)])
    rows.append([_btn("◀️ 数量を選び直す", f"shop:view:{product_id}")])
    rows.append([_btn("❌ キャンセル", "shop:list")])
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
                label = f"🛒 1個 ・ ¥{total:,}"
            else:
                unit = pricing.unit_price_for(qty, tiers)
                label = f"🛒 {qty}個 ・ ¥{total:,} (@¥{unit:,})"
            rows.append([_btn(label, f"shop:buy:{view.id}:{qty}")])
        rows.append([_btn("🔢 数量を入力して買う", f"shop:qty:{view.id}")])
    rows.append([_btn("◀️ 一覧に戻る", "shop:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
