"""Inline keyboards for the shop / product list."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.product_service import ProductView


def product_list_keyboard(products: list[ProductView]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for p in products:
        if p.available_stock <= 0:
            label = f"{p.name} - {p.price}円 (在庫切れ)"
            rows.append(
                [InlineKeyboardButton(text=label, callback_data="soldout")]
            )
        else:
            label = f"購入する: {p.name} - {p.price}円"
            rows.append(
                [InlineKeyboardButton(text=label, callback_data=f"buy:{p.id}")]
            )
    if not rows:
        rows.append(
            [InlineKeyboardButton(text="商品はまだありません", callback_data="noop")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
