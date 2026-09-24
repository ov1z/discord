"""Inline keyboards for the button-driven admin panel.

All callback data is namespaced with the ``ap:`` prefix. Order codes / product
ids embedded in callback data stay well under Telegram's 64-byte limit.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from database.models import Order
from services.product_service import ProductView


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("🛍 商品管理", "ap:products"), _btn("📦 在庫追加", "ap:restock")],
            [_btn("📋 注文管理", "ap:orders"), _btn("📊 在庫状況", "ap:stock")],
            [_btn("📢 一括送信", "ap:broadcast"), _btn("💴 PayPay状態", "ap:paypay")],
        ]
    )


def back_button(target: str = "ap:home") -> list[InlineKeyboardButton]:
    return [_btn("⬅️ 戻る", target)]


def products_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("➕ 商品追加", "ap:add_product")],
            [_btn("📄 商品説明", "ap:desc"), _btn("📝 注意事項設定", "ap:note")],
            [_btn("📦 在庫追加", "ap:restock")],
            [_btn("💹 価格設定", "ap:tiers"), _btn("🔢 まとめ買いボタン", "ap:bulk")],
            [_btn("🗑 商品削除", "ap:del")],
            back_button(),
        ]
    )


def _product_rows(products: list[ProductView], action: str) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    for p in products:
        label = f"{p.name} ({p.price}円 / 在庫{p.available_stock})"
        rows.append([_btn(label, f"ap:{action}:{p.id}")])
    return rows


def product_picker_keyboard(
    products: list[ProductView], action: str, back: str = "ap:products"
) -> InlineKeyboardMarkup:
    rows = _product_rows(products, action)
    rows.append(back_button(back))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def bulk_buttons_keyboard(products: list[ProductView]) -> InlineKeyboardMarkup:
    """One row per product; tapping flips its multi-quantity buttons on/off."""
    rows: list[list[InlineKeyboardButton]] = []
    for p in products:
        mark = "✅ 表示" if p.show_bulk_buttons else "⬜️ 非表示"
        rows.append([_btn(f"{mark} | {p.name}", f"ap:bulkp:{p.id}")])
    rows.append(back_button("ap:products"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def orders_keyboard(orders: list[Order]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for o in orders:
        rows.append([_btn(f"{o.order_code} [{o.status}] {o.price}円", f"ap:order:{o.order_code}")])
    rows.append([_btn("🔄 更新", "ap:orders")])
    rows.append(back_button())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def order_actions_keyboard(order_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("📤 再配布", f"ap:retry:{order_code}"),
             _btn("🔍 入金再確認", f"ap:verify:{order_code}")],
            [_btn("❌ キャンセル", f"ap:cancel:{order_code}")],
            back_button("ap:orders"),
        ]
    )


def paypay_keyboard(authenticated: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if authenticated:
        rows.append([_btn("🚪 ログアウト", "ap:logout"), _btn("🔄 更新", "ap:paypay")])
    else:
        rows.append([_btn("🔑 PayPayログイン", "ap:login"), _btn("🔄 更新", "ap:paypay")])
    rows.append(back_button())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_input_keyboard(target: str = "ap:home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("キャンセル", target)]])


def text_input_keyboard(
    clear_callback: str, target: str = "ap:products"
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("🗑 空にする", clear_callback)],
            [_btn("キャンセル", target)],
        ]
    )
