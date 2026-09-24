"""Delivery + notification helpers shared by the purchase flow.

Keeps the Telegram-send / admin-notify concerns out of the service layer.
If a send to the buyer fails, the order stays DELIVERING with stock RESERVED
so it can be retried (see /retry_delivery and startup recovery).
"""
from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timezone

from aiogram import Bot

from bot.container import Container
from bot.keyboards.purchase import delivered_keyboard
from services.payment_service import PurchaseOutcome, PurchaseResult

logger = logging.getLogger("bot.payment")


def _code(value: str) -> str:
    """Wrap *value* so Telegram renders it as tap-to-copy monospace."""
    return f"<code>{html.escape(str(value))}</code>"


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
    contents = result.delivered_contents or (
        [result.delivered_content] if result.delivered_content else []
    )
    if not contents:
        return False

    order = await container.orders.get(order_id)
    product_name = ""
    product_notes: str | None = None
    if order is not None:
        product = await container.products.get(order.product_id)
        if product is not None:
            product_name = product.name
            product_notes = product.notes

    if len(contents) == 1:
        goods = f"🎁 商品（タップでコピー）\n{_code(contents[0])}"
    else:
        body = "\n".join(
            f"{i}. {_code(c)}" for i, c in enumerate(contents, 1)
        )
        goods = f"🎁 商品 {len(contents)}個（タップでコピー）\n{body}"
    title = f"✅ 購入ありがとうございました！" + (f"\n{product_name}" if product_name else "")
    text = (
        f"{title}\n"
        "━━━━━━━━━━━━━━\n"
        f"{goods}\n\n"
        f"🧾 注文ID: {_code(result.order_code)}\n"
        f"💰 金額: ¥{result.expected_amount:,}"
    )
    if product_notes and product_notes.strip():
        text += f"\n\n⚠️ 注意事項\n{html.escape(product_notes.strip())}"
    try:
        await bot.send_message(
            buyer_chat_id,
            text,
            parse_mode="HTML",
            reply_markup=delivered_keyboard(container.settings.support_url),
        )
    except Exception:
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
    except Exception:
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


_background_tasks: set[asyncio.Task] = set()


def schedule_hold_recheck(
    bot: Bot, container: Container, order_id: int, buyer_chat_id: int
) -> None:
    """After a PayPay temporary hold, wait HOLD_RECHECK_SECONDS, then re-check.

    If the buyer released the hold and the money is received, the goods are
    delivered. Otherwise the buyer is told and the admin is alerted (the order
    stays PAYMENT_UNKNOWN; /verify_order can settle it later).
    """
    task = asyncio.create_task(
        _hold_recheck(bot, container, order_id, buyer_chat_id)
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _hold_recheck(
    bot: Bot, container: Container, order_id: int, buyer_chat_id: int
) -> None:
    await asyncio.sleep(container.settings.hold_recheck_seconds)
    try:
        result = await container.payments.reverify_and_settle(
            order_id, retry_accept=True
        )
    except Exception:
        logger.exception("hold recheck failed for order %s", order_id)
        await notify_admin(
            bot, container, f"⚠️ 保留後の再確認でエラー: 注文ID(内部) {order_id}"
        )
        return

    if result.delivered_content:
        delivered = await deliver_to_buyer(
            bot, container, order_id, buyer_chat_id, result
        )
        if not delivered:
            await notify_admin(
                bot, container, f"⚠️ 保留解除後の配布に失敗（要再配布）: 注文 {result.order_code}"
            )
        return

    if result.outcome == PurchaseOutcome.DELIVERED:
        return

    if result.outcome == PurchaseOutcome.OUT_OF_STOCK:
        text = buyer_message_for(result)
        if text:
            await _safe_send(bot, buyer_chat_id, text)
        await notify_admin(
            bot, container,
            f"⚠️ 保留解除後に入金確認、在庫切れ: 注文 {result.order_code}。在庫追加後 /retry_delivery",
        )
        return

    await _safe_send(
        bot,
        buyer_chat_id,
        "保留の解除が確認できませんでした。\n"
        "入金が確定していないため、商品はまだお渡しできません。\n"
        f"管理者が確認します（注文ID: {result.order_code}）。",
    )
    await notify_admin(
        bot, container,
        f"⚠️ 保留が{container.settings.hold_recheck_seconds}秒以内に解除されず未配布: "
        f"注文 {result.order_code}。確認後 /verify_order {result.order_code}",
    )


async def _safe_send(bot: Bot, chat_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id, text)
    except Exception:
        logger.warning("failed to send message to buyer")


async def notify_admin(bot: Bot, container: Container, text: str) -> None:
    await _safe_admin_send(bot, container, text)


async def _safe_admin_send(bot: Bot, container: Container, text: str) -> None:
    admin_id = container.settings.admin_telegram_id
    if not admin_id:
        return
    try:
        await bot.send_message(admin_id, text)
    except Exception:
        logger.warning("failed to notify admin")


def hold_message(seconds: int) -> str:
    wait = f"{seconds // 60}分" if seconds % 60 == 0 else f"{seconds}秒"
    return (
        "⚠️ PayPay側で送金が一時保留になりました。\n"
        "入金が確定していないため、まだ商品はお渡しできません。\n\n"
        f"PayPayアプリで保留を解除（送金を承認）し、{wait}以内に完了してください。\n"
        f"{wait}後に自動で受け取りを再確認し、確認できればすぐ商品をお送りします。\n\n"
        "新しいリンクの作成や二重送金はしないでください。"
    )


def buyer_message_for(result: PurchaseResult) -> str | None:
    """Non-success buyer-facing messages. None -> handled elsewhere."""
    o = result.outcome
    if o == PurchaseOutcome.AMOUNT_MISMATCH:
        actual = f"（送られた額: ¥{result.actual_amount:,}）" if result.actual_amount else ""
        return (
            f"⚠️ 金額が違います{actual}\n"
            f"¥{result.expected_amount:,} ちょうどの送金リンクを作り直して送ってください。"
        )
    if o == PurchaseOutcome.INVALID_LINK:
        return ("⚠️ PayPayの送金リンクとして読み取れませんでした。\n"
                "PayPayアプリで作成した送金リンクを貼り付けてください。")
    if o == PurchaseOutcome.LINK_ALREADY_USED:
        return (
            "⚠️ この送金リンクはすでに使用済みです。\n"
            "新しく送金リンクを作成して送ってください。"
        )
    if o == PurchaseOutcome.NOT_ACCEPTABLE:
        return ("⚠️ このリンクは受け取れません（受取済み、または期限切れの可能性）。\n"
                "新しい送金リンクを作成して送ってください。")
    if o == PurchaseOutcome.ORDER_EXPIRED:
        return "注文の有効期限が切れました。\n/start からもう一度購入してください。"
    if o == PurchaseOutcome.PROVIDER_NOT_READY:
        return "現在決済を受け付けできません。しばらくしてからお試しください。"
    if o == PurchaseOutcome.PAYMENT_UNKNOWN:
        return (
            "決済状態を確認中です。二重送金はしないでください。\n"
            "確認が取れ次第、商品をお送りします。"
        )
    if o == PurchaseOutcome.PAYMENT_HELD:
        return None
    if o == PurchaseOutcome.OUT_OF_STOCK:
        return (
            "お支払いを確認しましたが、在庫確保に問題が発生しました。\n"
            "管理者が確認のうえ対応します。"
        )
    if o == PurchaseOutcome.FAILED:
        return ("決済の受け取りに失敗しました。入金は成立していません。\n"
                "管理者に通知済みです。/start からやり直してください。")
    if o == PurchaseOutcome.ORDER_NOT_WAITING:
        return "この注文はすでに処理が終わっています。\n/start から選び直してください。"
    if o == PurchaseOutcome.DELIVERED:
        return "この注文はすでに配布済みです。"
    return None
