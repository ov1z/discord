"""Inline keyboard for the admin menu."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="商品管理", callback_data="admin:products")],
            [InlineKeyboardButton(text="在庫管理", callback_data="admin:stock")],
            [InlineKeyboardButton(text="注文管理", callback_data="admin:orders")],
            [InlineKeyboardButton(text="PayPay状態", callback_data="admin:paypay_status")],
            [InlineKeyboardButton(text="PayPayログイン", callback_data="admin:login")],
            [InlineKeyboardButton(text="PayPayログアウト", callback_data="admin:logout")],
            [InlineKeyboardButton(text="ログ確認", callback_data="admin:logs")],
        ]
    )
