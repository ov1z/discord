"""Buyer pays -> run the payment pipeline -> deliver.

Two ways in, both ending in the same outcome handling:
  * the buyer pays a payment request we issued and sends its transaction
    number (used when PAYMENT_FLOW=request);
  * the buyer creates a send-money link and we accept it (default).

Everything except the delivered goods renders into the chat's single shop
message, and the buyer's own messages are removed once read, so a finished
purchase leaves just the goods behind.
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot import screen
from bot.container import Container
from bot.handlers.payment import (
    buyer_message_for,
    deliver_to_buyer,
    hold_message,
    notify_admin,
    schedule_hold_recheck,
)
from bot.handlers.start import show_shop
from bot.keyboards.purchase import cancel_purchase_keyboard, error_keyboard
from bot.states.purchase import PurchaseStates
from services.payment_service import PurchaseOutcome, PurchaseResult

logger = logging.getLogger("bot.purchase")

router = Router(name="purchase")


@router.callback_query(F.data.startswith("cancel:"))
async def cb_cancel(
    callback: CallbackQuery, services: Container, state: FSMContext, bot: Bot
) -> None:
    assert callback.data is not None
    order_code = callback.data.split(":", 1)[1]
    ok = await services.orders.cancel(order_code)
    await screen.clear_keeping_screen(state)
    await callback.answer(
        "注文をキャンセルしました。" if ok else "この注文はキャンセルできません。"
    )
    if callback.message is not None:
        await show_shop(bot, callback.message.chat.id, services, state)


async def _order_id_from(
    state: FSMContext, bot: Bot, chat_id: int, services: Container
) -> int | None:
    data = await state.get_data()
    order_id = data.get("order_id")
    if order_id is None:
        await screen.clear_keeping_screen(state)
        await show_shop(bot, chat_id, services, state)
    return order_id


@router.message(
    PurchaseStates.WAITING_TRANSACTION_ID, F.text, ~F.text.startswith("/")
)
async def on_transaction_id(
    message: Message, services: Container, state: FSMContext, bot: Bot
) -> None:
    chat_id = message.chat.id
    text = (message.text or "").strip()
    await screen.delete_silently(message)

    order_id = await _order_id_from(state, bot, chat_id, services)
    if order_id is None:
        return

    if services.provider.looks_like_link(text):
        result = await services.payments.process_payment_link(order_id, text)
        await _handle_result(bot, chat_id, services, state, order_id, result)
        return

    if not services.provider.looks_like_transaction_id(text):
        await _reprompt(
            bot, chat_id, services, state, order_id,
            "取引番号を送ってください。\n"
            "PayPayアプリ → 該当の支払い → 取引詳細 に表示される数字です。",
        )
        return

    result = await services.payments.confirm_by_transaction(order_id, text)
    await _handle_result(bot, chat_id, services, state, order_id, result)


@router.message(PurchaseStates.WAITING_PAYPAY_LINK, F.text, ~F.text.startswith("/"))
async def on_paypay_link(
    message: Message, services: Container, state: FSMContext, bot: Bot
) -> None:
    chat_id = message.chat.id
    url = (message.text or "").strip()
    await screen.delete_silently(message)

    order_id = await _order_id_from(state, bot, chat_id, services)
    if order_id is None:
        return

    if not services.provider.looks_like_link(url):
        await _reprompt(
            bot, chat_id, services, state, order_id,
            "PayPayの送金リンクを送ってください。\n"
            "アプリで作成したリンクを貼り付けてください"
            "（メッセージが一緒でも大丈夫です）。",
        )
        return

    result = await services.payments.process_payment_link(order_id, url)
    await _handle_result(bot, chat_id, services, state, order_id, result)


async def _reprompt(
    bot: Bot,
    chat_id: int,
    services: Container,
    state: FSMContext,
    order_id: int,
    hint: str,
) -> None:
    """Re-render the waiting screen with a hint, keeping it to one message."""
    order = await services.orders.get(order_id)
    amount = f"{order.price:,}円" if order is not None else "案内した金額"
    code = order.order_code if order is not None else ""
    await screen.render(
        bot,
        chat_id,
        state,
        f"{hint}\n\n金額: {amount}\n注文ID: {code}",
        cancel_purchase_keyboard(code, services.settings.support_url),
    )


async def _handle_result(
    bot: Bot,
    chat_id: int,
    services: Container,
    state: FSMContext,
    order_id: int,
    result: PurchaseResult,
) -> None:
    """Shared outcome handling for both payment routes."""
    support_url = services.settings.support_url

    if (
        result.outcome == PurchaseOutcome.PAID_NOT_DELIVERED
        and result.delivered_content
    ):
        delivered = await deliver_to_buyer(
            bot, services, order_id, chat_id, result
        )
        if delivered:
            await screen.drop(bot, chat_id, state)
            await screen.clear_keeping_screen(state)
        else:
            await screen.render(
                bot, chat_id, state,
                "お支払いを確認しました。商品の送信に失敗したため、"
                "管理者が確認のうえお送りします。",
                error_keyboard(support_url),
            )
            await notify_admin(
                bot, services, f"⚠️ 配布失敗（要再配布）: 注文 {result.order_code}"
            )
        return

    text = buyer_message_for(result) or "処理が完了しませんでした。"
    markup = cancel_purchase_keyboard(result.order_code, support_url)

    if result.outcome == PurchaseOutcome.FAILED:
        detail = (
            f"\nPayPayからの案内:\n{result.provider_message}"
            if result.provider_message else ""
        )
        await notify_admin(
            bot,
            services,
            f"❌ 受け取り失敗: 注文 {result.order_code}（{result.expected_amount}円）。"
            f"入金は成立していません。{detail}",
        )
        await screen.clear_keeping_screen(state)
        markup = error_keyboard(support_url)
    elif result.outcome == PurchaseOutcome.PAYMENT_UNKNOWN:
        markup = error_keyboard(support_url, result.order_code)
        await notify_admin(
            bot,
            services,
            f"⚠️ 決済状態不明（PAYMENT_UNKNOWN）: 注文 {result.order_code}。"
            " 手動確認してください。",
        )
    elif result.outcome == PurchaseOutcome.PAYMENT_HELD:
        seconds = services.settings.hold_recheck_seconds
        text = hold_message(seconds)
        markup = error_keyboard(support_url, result.order_code)
        held_detail = (
            f"\nPayPayからの案内:\n{result.provider_message}"
            if result.provider_message else ""
        )
        await notify_admin(
            bot,
            services,
            f"ℹ️ PayPay一次保留: 注文 {result.order_code}。"
            f"購入者に解除を依頼、{seconds}秒後に自動で再確認します。{held_detail}",
        )
        schedule_hold_recheck(bot, services, order_id, chat_id)
        await screen.clear_keeping_screen(state)
    elif result.outcome == PurchaseOutcome.OUT_OF_STOCK:
        markup = error_keyboard(support_url)
        await notify_admin(
            bot,
            services,
            f"⚠️ 入金済みだが在庫切れ: 注文 {result.order_code}。"
            " 在庫追加後 /retry_delivery してください。",
        )
        await screen.clear_keeping_screen(state)
    elif result.outcome in (
        PurchaseOutcome.ORDER_EXPIRED,
        PurchaseOutcome.DELIVERED,
        PurchaseOutcome.ORDER_NOT_WAITING,
    ):
        markup = error_keyboard(support_url)
        await screen.clear_keeping_screen(state)

    await screen.render(bot, chat_id, state, text, markup)
