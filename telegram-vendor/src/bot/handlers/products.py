"""Shop: product detail, quantity selection, and order creation.

Everything renders into the chat's single shop message (``bot/screen.py``),
so browsing and ordering never adds to the conversation.
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot import screen
from bot.container import Container
from bot.handlers.start import show_shop
from bot.keyboards.products import product_detail_keyboard
from bot.keyboards.purchase import cancel_purchase_keyboard
from bot.states.purchase import PurchaseStates
from services import pricing
from services.product_service import ProductView

logger = logging.getLogger("bot.products")

router = Router(name="products")

_MAX_QTY = 1000


def _detail_text(view: ProductView) -> str:
    lines = [f"🧾 [{view.name}]を選択"]
    if view.description:
        lines.append("")
        lines.append(view.description)
    lines.append("")
    lines.append("価格:")
    lines.append(pricing.format_tiers(view.tiers))
    lines.append("")
    stock = f"{view.available_stock}個" if view.available_stock > 0 else "入荷待ち"
    lines.append(f"現在庫: {stock}")
    if view.available_stock > 0:
        lines.append("")
        lines.append("購入数量を選んでください:")
    return "\n".join(lines)


@router.callback_query(F.data.startswith("shop:view:"))
async def cb_view(
    callback: CallbackQuery, services: Container, state: FSMContext, bot: Bot
) -> None:
    product_id = int(callback.data.split(":")[2])
    view = await services.products.get_view(product_id)
    if view is None or not view.active:
        await callback.answer("この商品は購入できません。", show_alert=True)
        return
    if callback.message is not None:
        await screen.render(
            bot,
            callback.message.chat.id,
            state,
            _detail_text(view),
            product_detail_keyboard(view),
        )
    await callback.answer()


@router.callback_query(F.data == "shop:list")
async def cb_list(
    callback: CallbackQuery, services: Container, state: FSMContext, bot: Bot
) -> None:
    await screen.clear_keeping_screen(state)
    if callback.message is not None:
        await show_shop(bot, callback.message.chat.id, services, state)
    await callback.answer()


@router.callback_query(F.data.startswith("shop:qty:"))
async def cb_custom_qty(
    callback: CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    product_id = int(callback.data.split(":")[2])
    await state.set_state(PurchaseStates.CHOOSING_QUANTITY)
    await state.update_data(product_id=product_id)
    if callback.message is not None:
        await screen.render(
            bot,
            callback.message.chat.id,
            state,
            "🔢 購入したい数量を数字で送ってください（例: 3）",
        )
    await callback.answer()


@router.message(PurchaseStates.CHOOSING_QUANTITY, F.text, ~F.text.startswith("/"))
async def on_custom_qty(
    message: Message, services: Container, state: FSMContext, bot: Bot
) -> None:
    data = await state.get_data()
    product_id = data.get("product_id")
    raw = (message.text or "").strip()
    await screen.delete_silently(message)

    if product_id is None:
        await screen.clear_keeping_screen(state)
        await show_shop(bot, message.chat.id, services, state)
        return
    if not raw.isdigit() or int(raw) < 1:
        await screen.render(
            bot,
            message.chat.id,
            state,
            "🔢 1以上の数字で送ってください（例: 3）",
        )
        return
    quantity = min(int(raw), _MAX_QTY)
    await start_order(
        bot, message.chat.id, services, state, message.from_user.id,
        product_id, quantity,
    )


@router.callback_query(F.data.startswith("shop:buy:"))
async def cb_buy(
    callback: CallbackQuery, services: Container, state: FSMContext, bot: Bot
) -> None:
    parts = callback.data.split(":")
    product_id, quantity = int(parts[2]), int(parts[3])
    if callback.message is None:
        await callback.answer()
        return
    await callback.answer()
    await start_order(
        bot, callback.message.chat.id, services, state,
        callback.from_user.id, product_id, quantity,
    )


async def start_order(
    bot: Bot,
    chat_id: int,
    services: Container,
    state: FSMContext,
    buyer_id: int,
    product_id: int,
    quantity: int,
) -> None:
    result = await services.orders.create_order(buyer_id, product_id, quantity)
    if result is None:
        await screen.render(bot, chat_id, state, "この商品は購入できません。")
        return
    if result.out_of_stock:
        view = await services.products.get_view(product_id)
        await screen.render(
            bot,
            chat_id,
            state,
            f"在庫が不足しています（購入希望: {result.quantity}個）。\n"
            "数量を減らすか、入荷までお待ちください。",
            product_detail_keyboard(view) if view is not None else None,
        )
        return

    ttl_minutes = max(1, services.settings.order_ttl_seconds // 60)
    qty_line = (
        f"数量: {result.quantity}個（@{result.unit_price:,}円）\n"
        if result.quantity > 1 else ""
    )
    header = (
        f"🧾 {result.product_name}\n\n"
        f"{qty_line}"
        f"金額: {result.price:,}円\n"
        f"注文ID: {result.order_code}\n\n"
    )

    request_link = None
    if (
        services.settings.uses_payment_requests
        and services.provider.supports_requests
        and await services.provider.is_ready()
    ):
        try:
            request_link = await services.provider.create_request(result.price)
        except Exception as exc:
            logger.warning(
                "could not issue a payment request: %s: %s",
                type(exc).__name__, exc,
            )

    if request_link is not None:
        text = (
            header
            + "▼ この請求リンクから支払ってください\n"
            + f"{request_link.link}\n\n"
            + "支払い後、PayPayアプリに表示される「取引番号」を送信してください。\n\n"
            + f"⏳ 有効時間: {ttl_minutes}分"
        )
        await state.set_state(PurchaseStates.WAITING_TRANSACTION_ID)
    else:
        text = (
            header
            + f"PayPayアプリで {result.price:,}円 の送金リンクを作成し、\n"
            + "このチャットに送信してください。\n"
            + f"⚠️ 金額は必ず {result.price:,}円 にしてください。\n\n"
            + f"⏳ 有効時間: {ttl_minutes}分"
        )
        await state.set_state(PurchaseStates.WAITING_PAYPAY_LINK)

    await state.update_data(order_id=result.order_id, order_code=result.order_code)
    await screen.render(
        bot,
        chat_id,
        state,
        text,
        cancel_purchase_keyboard(result.order_code, services.settings.support_url),
    )
