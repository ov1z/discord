"""One shop message per chat, edited in place.

A purchase produces a lot of throwaway text (welcome, product list, detail,
payment instructions, retry notices). Left alone they bury the one message
that actually matters afterwards — the delivered goods — so every step edits
a single "screen" message instead of adding to the chat.

The screen's id lives in the FSM data, which is why ``clear_keeping_screen``
exists: clearing the purchase state must not lose track of the message.
"""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, Message

logger = logging.getLogger("bot.screen")

SCREEN_KEY = "screen_message_id"


async def delete_silently(message: Message | None) -> None:
    """Remove a message, ignoring the many reasons Telegram may refuse."""
    if message is None:
        return
    try:
        await message.delete()
    except Exception:
        pass


async def render(
    bot: Bot,
    chat_id: int,
    state: FSMContext,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = None,
) -> int | None:
    """Show *text* as the chat's single shop message.

    Edits the existing screen when there is one; otherwise sends a new message
    and remembers it. Returns the screen's message id.
    """
    data = await state.get_data()
    message_id = data.get(SCREEN_KEY)

    if message_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            return message_id
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                return message_id

    sent = await bot.send_message(
        chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode
    )
    await state.update_data(**{SCREEN_KEY: sent.message_id})
    return sent.message_id


async def drop(bot: Bot, chat_id: int, state: FSMContext) -> None:
    """Delete the screen, e.g. once the goods have been handed over."""
    data = await state.get_data()
    message_id = data.get(SCREEN_KEY)
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass
    await state.update_data(**{SCREEN_KEY: None})


async def clear_keeping_screen(state: FSMContext) -> None:
    """Clear the purchase state but keep editing the same screen message."""
    data = await state.get_data()
    message_id = data.get(SCREEN_KEY)
    await state.clear()
    if message_id:
        await state.update_data(**{SCREEN_KEY: message_id})
