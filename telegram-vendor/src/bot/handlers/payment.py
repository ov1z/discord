"""Delivery + notification helpers shared by the purchase flow.

Keeps the Telegram-send / admin-notify concerns out of the service layer.
If a send to the buyer fails, the order stays DELIVERING with stock RESERVED
so it can be retried (see /retry_delivery and startup recovery).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Bot

from bot.container import Container
from services.payment_service import PurchaseOutcome, PurchaseResult

logger = logging.getLogger("bot.payment")


async def deliver_to_buyer(
    bot: Bot,
    container: Container,
    order_id: int,
    buyer_chat_id: int,
    result: PurchaseResult,
) -> bool:
    """Send the reserved content to the buyer and finalize the order.

    Returns True if the buyer received the goods.
    """
    if not result.delivered_content:
        return False

    order = await container.orders.get(order_id)
    product_name = ""
    if order is not None:
        product = await container.products.get(order.product_id)
        product_name = product.name if product else ""

    text = (
        "購入ありがとうございます。\n\n"
        f"商品: {result.delivered_content}\n"
        f"注文ID: {result.order_code}\n"
        f"決済金額: {result.expected_amount}円"
    )
    try:
        await bot.send_message(buyer_chat_id, text)
    except Exception:  # noqa: BLE001 - Telegram send may fail for many reasons
        logger.warning(
            "delivery send failed for order %s; kept for retry", result.order_code
        )
        return False

    await container.payments.confirm_delivered(order_id)
    await _notify_admin_purchase(bot, container, order_id, result, product_name)
    return True


async def _notify_admin_purchase(
    bot: Bot,
    container: Container,
    order_id: int,
    result: PurchaseResult,
    product_name: str,
) -> None:
    order = await container.orders.get(order_id)
    if order is None:
        return
    username = "-"
    try:
        chat = await bot.get_chat(order.telegram_user_id)
        username = f"@{chat.username}" if chat.username else (chat.first_name or "-")
    except Exception:  # noqa: BLE001
        pass
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    text = (
        "購入完了\n"
        f"注文ID: {result.order_code}\n"
        f"Telegram: {username}\n"
        f"User ID: {order.telegram_user_id}\n"
        f"商品: {product_name}\n"
        f"金額: {result.expected_amount}円\n"
        f"PayPay Payment ID: {result.payment_id or '-'}\n"
        f"日時: {now}"
    )
    await _safe_admin_send(bot, container, text)


async def notify_admin(bot: Bot, container: Container, text: str) -> None:
    await _safe_admin_send(bot, container, text)


async def _safe_admin_send(bot: Bot, container: Container, text: str) -> None:
    admin_id = container.settings.admin_telegram_id
    if not admin_id:
        return
    try:
        await bot.send_message(admin_id, text)
    except Exception:  # noqa: BLE001
        logger.warning("failed to notify admin")


def buyer_message_for(result: PurchaseResult) -> str | None:
    """Non-success buyer-facing messages. None -> handled elsewhere."""
    o = result.outcome
    if o == PurchaseOutcome.AMOUNT_MISMATCH:
        return (
            "送金金額が一致しません。\n"
            f"必要金額: {result.expected_amount}円"
        )
    if o == PurchaseOutcome.INVALID_LINK:
        return "有効なPayPayリンクではありません。もう一度確認してください。"
    if o == PurchaseOutcome.LINK_ALREADY_USED:
        return "このPayPayリンクはすでに使用されています。"
    if o == PurchaseOutcome.NOT_ACCEPTABLE:
        return "このリンクは受け取れません（受取済み/期限切れの可能性）。"
    if o == PurchaseOutcome.ORDER_EXPIRED:
        return "注文の有効期限が切れました。もう一度購入し直してください。"
    if o == PurchaseOutcome.PROVIDER_NOT_READY:
        return "現在決済を受け付けできません。しばらくしてからお試しください。"
    if o == PurchaseOutcome.PAYMENT_UNKNOWN:
        return (
            "決済状態を確認中です。二重送金はしないでください。\n"
            "確認が取れ次第、商品をお送りします。"
        )
    if o == PurchaseOutcome.OUT_OF_STOCK:
        return (
            "お支払いを確認しましたが、在庫確保に問題が発生しました。\n"
            "管理者が確認のうえ対応します。"
        )
    if o == PurchaseOutcome.FAILED:
        return "決済の受け取りに失敗しました。もう一度お試しください。"
    if o == PurchaseOutcome.ORDER_NOT_WAITING:
        return "この注文はすでに処理済みです。"
    if o == PurchaseOutcome.DELIVERED:
        return "この注文はすでに配布済みです。"
    return None
