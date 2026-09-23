"""Buyer sends a PayPay link -> run the payment pipeline -> deliver."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.container import Container
from bot.handlers.payment import buyer_message_for, deliver_to_buyer, notify_admin
from bot.states.purchase import PurchaseStates
from services.payment_service import PurchaseOutcome

logger = logging.getLogger("bot.purchase")

router = Router(name="purchase")


@router.callback_query(F.data.startswith("cancel:"))
async def cb_cancel(callback: CallbackQuery, services: Container, state: FSMContext) -> None:
    assert callback.data is not None
    order_code = callback.data.split(":", 1)[1]
    ok = await services.orders.cancel(order_code)
    await state.clear()
    if ok:
        await callback.answer("注文をキャンセルしました。", show_alert=True)
    else:
        await callback.answer("この注文はキャンセルできません。", show_alert=True)


@router.message(PurchaseStates.WAITING_PAYPAY_LINK, F.text)
async def on_paypay_link(
    message: Message, services: Container, state: FSMContext, bot: Bot
) -> None:
    data = await state.get_data()
    order_id = data.get("order_id")
    if order_id is None:
        await state.clear()
        await message.answer("注文情報が見つかりません。もう一度やり直してください。")
        return

    url = (message.text or "").strip()

    result = await services.payments.process_payment_link(order_id, url)

    # Successful receipt path: money confirmed, content reserved -> deliver.
    if (
        result.outcome == PurchaseOutcome.PAID_NOT_DELIVERED
        and result.delivered_content
    ):
        delivered = await deliver_to_buyer(
            bot, services, order_id, message.chat.id, result
        )
        if delivered:
            await state.clear()
        else:
            await message.answer(
                "お支払いを確認しました。商品の送信に失敗したため、"
                "管理者が確認のうえお送りします。"
            )
            await notify_admin(
                bot,
                services,
                f"⚠️ 配布失敗（要再配布）: 注文 {result.order_code}",
            )
        return

    # Non-success / informational outcomes.
    msg = buyer_message_for(result)
    if msg:
        await message.answer(msg)

    if result.outcome == PurchaseOutcome.PAYMENT_UNKNOWN:
        await notify_admin(
            bot,
            services,
            f"⚠️ 決済状態不明（PAYMENT_UNKNOWN）: 注文 {result.order_code}。"
            " 手動確認してください。",
        )
    elif result.outcome == PurchaseOutcome.OUT_OF_STOCK:
        await notify_admin(
            bot,
            services,
            f"⚠️ 入金済みだが在庫切れ: 注文 {result.order_code}。"
            " 在庫追加後 /retry_delivery してください。",
        )
        await state.clear()
    elif result.outcome in (
        PurchaseOutcome.ORDER_EXPIRED,
        PurchaseOutcome.DELIVERED,
        PurchaseOutcome.ORDER_NOT_WAITING,
    ):
        await state.clear()
    # AMOUNT_MISMATCH / INVALID_LINK / LINK_ALREADY_USED / NOT_ACCEPTABLE:
    # keep the state so the buyer can send a corrected link.
