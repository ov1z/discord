"""Inline keyboards for an in-progress purchase."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def cancel_purchase_keyboard(order_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="注文をキャンセル", callback_data=f"cancel:{order_code}"
                )
            ]
        ]
    )
