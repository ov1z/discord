"""/start and the shop product list.

The shop lives in ONE message that gets edited as the buyer moves around, so
the chat does not fill up with dead panels. See ``bot/screen.py``.
"""
from __future__ import annotations

import logging

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot import screen
from bot.container import Container
from bot.keyboards.products import shop_list_keyboard
from database.engine import get_sessionmaker
from database.repository import UserRepository

logger = logging.getLogger("bot.start")

router = Router(name="start")

SHOP_HEADER = (
    "🛒 Xアカウントショップ\n"
    "━━━━━━━━━━━━━━\n"
    "24時間自動配送でお買い求めいただけます。\n"
    "購入される商品をタップしてください。"
)


async def show_shop(
    bot: Bot, chat_id: int, services: Container, state: FSMContext
) -> None:
    """Render the product list into the chat's single shop message."""
    products = await services.products.list_for_shop()
    if not products:
        await screen.render(
            bot, chat_id, state,
            "🛒 Xアカウントショップ\n"
            "━━━━━━━━━━━━━━\n"
            "ただいま販売中の商品はありません。\n"
            "入荷までしばらくお待ちください。",
        )
        return
    await screen.render(
        bot, chat_id, state, SHOP_HEADER,
        shop_list_keyboard(
            products,
            support_url=services.settings.support_url,
            channel_url=services.settings.sales_channel_url,
        ),
    )


async def reset_to_shop(
    bot: Bot,
    chat_id: int,
    services: Container,
    state: FSMContext,
    telegram_user_id: int,
) -> None:
    """Start over completely: drop the flow, the order and the old screen.

    /start must always work, whatever state the buyer got stuck in, so any
    not-yet-paid order is abandoned and a fresh screen is posted at the
    bottom of the chat rather than an old one edited in place.
    """
    if telegram_user_id:
        try:
            abandoned = await services.orders.abandon_unpaid_for_user(
                telegram_user_id
            )
            if abandoned:
                logger.info(
                    "reset: cancelled %s unpaid order(s) for %s",
                    abandoned, telegram_user_id,
                )
        except Exception:
            logger.warning("could not abandon unpaid orders", exc_info=True)

    await screen.drop(bot, chat_id, state)
    await state.clear()
    await show_shop(bot, chat_id, services, state)


def _is_private(message: Message) -> bool:
    return message.chat.type == "private"


@router.message(Command("start"))
async def cmd_start(
    message: Message, services: Container, state: FSMContext, bot: Bot
) -> None:
    if not _is_private(message):
        await message.answer("ショップは個人チャットでご利用ください。")
        return
    user = message.from_user
    if user is not None:
        async with get_sessionmaker()() as session:
            await UserRepository(session).upsert(
                telegram_user_id=user.id,
                username=user.username,
                first_name=user.first_name,
            )
            await session.commit()

    # Keep the buyer's /start message (do not delete it).
    await reset_to_shop(bot, message.chat.id, services, state, user.id if user else 0)


@router.message(Command("shop"))
async def cmd_shop(
    message: Message, services: Container, state: FSMContext, bot: Bot
) -> None:
    if not _is_private(message):
        await message.answer("ショップは個人チャットでご利用ください。")
        return
    user_id = message.from_user.id if message.from_user else 0
    await reset_to_shop(bot, message.chat.id, services, state, user_id)


@router.callback_query(lambda c: c.data in {"soldout", "noop"})
async def cb_noop(callback: CallbackQuery) -> None:
    if callback.data == "soldout":
        await callback.answer("この商品は在庫切れです。", show_alert=True)
    else:
        await callback.answer()
