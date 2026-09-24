"""Last-resort handling so nothing is silently ignored or crashes a handler.

FSM state lives in memory, so a restart (deploy, crash, host reboot) drops
whatever flow someone was in. Their next message then matches no handler and
the bot stays silent, which looks broken. This router runs last and tells
them how to get going again.

It also catches errors that escape the handlers — most commonly a button
pressed while the bot was down, whose callback query has since expired.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import ErrorEvent, Message

from bot.container import Container

logger = logging.getLogger("bot.fallback")

router = Router(name="fallback")

_STALE_QUERY = ("query is too old", "query ID is invalid")


@router.message(F.text)
async def on_unhandled_text(message: Message, services: Container) -> None:
    user = message.from_user
    if user is None:
        return
    logger.info("unhandled message from %s", user.id)

    if services.is_admin(user.id):
        await message.answer(
            "そのメッセージに対応する操作がありません。\n"
            "入力の途中だった場合、Botの再起動で中断された可能性があります。\n\n"
            "購入は /start、管理は /admin から始めてください。"
        )
        return

    await message.answer(
        "そのメッセージに対応する操作がありません。\n"
        "/start から商品を選んでください。"
    )


@router.errors()
async def on_error(event: ErrorEvent) -> bool:
    """Keep polling alive whatever a handler threw, but never hide it."""
    exc = event.exception
    if isinstance(exc, TelegramBadRequest) and any(
        marker in str(exc) for marker in _STALE_QUERY
    ):
        logger.info("stale callback query ignored")
        return True
    logger.exception("unhandled error in a handler: %s", type(exc).__name__)
    return True
