"""Inline keyboards for an in-progress purchase."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def _support_row(support_url: str | None) -> list[list[InlineKeyboardButton]]:
    if not support_url:
        return []
    return [[InlineKeyboardButton(text="💬 管理者に問い合わせ", url=support_url)]]


def cancel_purchase_keyboard(
    order_code: str, support_url: str | None = None
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="❌ 注文をキャンセル", callback_data=f"cancel:{order_code}"
            )
        ]
    ]
    rows += _support_row(support_url)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def support_keyboard(support_url: str | None = None) -> InlineKeyboardMarkup | None:
    """Just the contact button, for messages that stay in the chat."""
    rows = _support_row(support_url)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def error_keyboard(
    support_url: str | None = None, order_code: str | None = None
) -> InlineKeyboardMarkup:
    """Shown when something went wrong: a way out and a way to ask a human."""
    rows: list[list[InlineKeyboardButton]] = []
    if order_code:
        rows.append(
            [
                InlineKeyboardButton(
                    text="❌ 注文をキャンセル", callback_data=f"cancel:{order_code}"
                )
            ]
        )
    rows += _support_row(support_url)
    rows.append(
        [InlineKeyboardButton(text="🛒 商品一覧に戻る", callback_data="shop:list")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
