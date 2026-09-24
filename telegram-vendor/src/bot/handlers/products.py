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
from bot.keyboards.products import agreement_keyboard, product_detail_keyboard
from bot.keyboards.purchase import cancel_purchase_keyboard
from bot.states.purchase import PurchaseStates
from bot.terms import PURCHASE_TERMS
from services import pricing
from services.product_service import ProductView

logger = logging.getLogger("bot.products")

router = Router(name="products")

_MAX_QTY = 1000


def _detail_text(view: ProductView) -> str:
    lines = [f"🛍 {view.name}", "━━━━━━━━━━━━━━"]
    if view.description:
        lines.append(view.description)
        lines.append("")
    priced = pricing.format_tiers(view.tiers)
    if len(view.tiers) == 1:
        lines.append(f"💴 価格: {priced}")
    else:
        lines.append("💴 価格")
        lines.append(priced)
    lines.append("")
    if view.available_stock > 0:
        lines.append(f"📦 在庫: {view.available_stock}個")
        lines.append("")
        lines.append("👇 数量を選んでください")
    else:
        lines.append("🈳 現在在庫切れです（入荷までお待ちください）")
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
            "🔢 買いたい個数を数字で送ってください\n（例: 3）",
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
            "🔢 1以上の数字で送ってください\n（例: 3）",
        )
        return
    quantity = min(int(raw), _MAX_QTY)
    await show_agreement(bot, message.chat.id, services, state, product_id, quantity)


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
    await show_agreement(
        bot, callback.message.chat.id, services, state, product_id, quantity
    )


@router.callback_query(F.data.startswith("shop:agree:"))
async def cb_agree(
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


async def show_agreement(
    bot: Bot,
    chat_id: int,
    services: Container,
    state: FSMContext,
    product_id: int,
    quantity: int,
) -> None:
    """Consent gate: show the order summary + short terms before payment."""
    view = await services.products.get_view(product_id)
    if view is None or not view.active:
        await screen.render(bot, chat_id, state, "この商品は購入できません。")
        return
    quantity = max(1, quantity)
    if quantity > view.available_stock:
        await screen.render(
            bot, chat_id, state,
            f"🈳 在庫が足りません（ご希望: {quantity}個）。\n"
            "個数を減らすか、入荷までお待ちください。",
            product_detail_keyboard(view),
        )
        return
    total = view.total(quantity)
    text = (
        "⚠️ ご購入前の確認（必読）\n"
        "━━━━━━━━━━━━━━\n"
        "【注文内容】\n"
        f"商品: {view.name}\n"
        f"数量: {quantity}個\n"
        f"合計: ¥{total:,}\n"
        "━━━━━━━━━━━━━━\n\n"
        f"{PURCHASE_TERMS}"
    )
    await state.set_state(None)
    await screen.render(
        bot, chat_id, state, text,
        agreement_keyboard(product_id, quantity, services.settings.terms_link),
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
            f"🈳 在庫が足りません（ご希望: {result.quantity}個）。\n"
            "個数を減らすか、入荷までお待ちください。",
            product_detail_keyboard(view) if view is not None else None,
        )
        return

    ttl_minutes = max(1, services.settings.order_ttl_seconds // 60)
    unit_line = (
        f"単価: ¥{result.unit_price:,}\n" if result.quantity > 1 else ""
    )
    text = (
        "🧾 注文確認\n"
        "━━━━━━━━━━━━━━\n"
        f"商品: {result.product_name}\n"
        f"数量: {result.quantity}個\n"
        f"{unit_line}"
        f"合計: ¥{result.price:,}\n"
        "━━━━━━━━━━━━━━\n\n"
        f"💳 ¥{result.price:,} の PayPay送金リンクを作成し、\n"
        "このチャットに貼り付けてください。\n"
        f"（金額は必ず ¥{result.price:,} ちょうどに）\n\n"
        "※PayPay加盟店決済ではなく、PayPay残高の個人間送金を利用します。"
        "PayPayの補償制度の対象外となる場合があります。\n\n"
        "入金を確認しだい、自動で商品をお届けします。\n"
        f"⏳ 有効時間: {ttl_minutes}分"
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
