"""/start and the shop product list."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.container import Container
from bot.keyboards.products import shop_list_keyboard
from database.engine import get_sessionmaker
from database.repository import UserRepository

router = Router(name="start")


async def _show_products(target: Message, services: Container) -> None:
    products = await services.products.list_for_shop()
    if not products:
        await target.answer("現在販売中の商品はありません。")
        return
    text = (
        "🛒 商品一覧\n\n"
        "商品を押すと、価格・在庫・まとめ買い割引を確認して購入できます。\n\n"
        "現在の価格・在庫:"
    )
    await target.answer(text, reply_markup=shop_list_keyboard(products))


@router.message(Command("start"))
async def cmd_start(message: Message, services: Container) -> None:
    user = message.from_user
    if user is not None:
        async with get_sessionmaker()() as session:
            await UserRepository(session).upsert(
                telegram_user_id=user.id,
                username=user.username,
                first_name=user.first_name,
            )
            await session.commit()
    await message.answer(
        "デジタル商品自動販売Botへようこそ！\n購入したい商品を選んでください。"
    )
    await _show_products(message, services)


@router.message(Command("shop"))
async def cmd_shop(message: Message, services: Container) -> None:
    await _show_products(message, services)


@router.callback_query(lambda c: c.data in {"soldout", "noop"})
async def cb_noop(callback: CallbackQuery) -> None:
    if callback.data == "soldout":
        await callback.answer("この商品は在庫切れです。", show_alert=True)
    else:
        await callback.answer()
