"""Admin menu and management commands (products / stock / orders)."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from bot.container import Container
from bot.handlers.payment import deliver_to_buyer, notify_admin
from bot.keyboards.admin import admin_menu_keyboard
from services.payment_service import PurchaseOutcome

logger = logging.getLogger("bot.admin")

router = Router(name="admin")


def _admin_only(message: Message, services: Container) -> bool:
    return message.from_user is not None and services.is_admin(message.from_user.id)


# --------------------------------------------------------------------------- #
# Menu
# --------------------------------------------------------------------------- #
@router.message(Command("admin"))
async def cmd_admin(message: Message, services: Container) -> None:
    if not _admin_only(message, services):
        return
    await message.answer("管理者メニュー", reply_markup=admin_menu_keyboard())


@router.callback_query(F.data.startswith("admin:"))
async def cb_admin(callback: CallbackQuery, services: Container) -> None:
    if not services.is_admin(callback.from_user.id):
        await callback.answer("権限がありません。", show_alert=True)
        return
    assert callback.data is not None
    action = callback.data.split(":", 1)[1]
    hints = {
        "products": "商品管理: /product_list /product_add 名前|価格|説明 "
        "/product_edit id|field|value /product_note <id> 注意事項 /product_delete id",
        "stock": "在庫管理: /restock <product_id> (改行で1行1在庫) "
        "/stock_add も同じ /stock_count <product_id> /stock_list <product_id>",
        "orders": "注文管理: /orders /order ORD-XXXX "
        "/retry_delivery ORD-XXXX /cancel_order ORD-XXXX",
        "paypay_status": "/paypay_status を実行してください。",
        "login": "/login を実行してください（個人チャット限定）。",
        "logout": "/logout を実行してください。",
        "logs": "最近の注文は /orders で確認できます。",
    }
    await callback.message.answer(hints.get(action, "不明な操作です。"))
    await callback.answer()


# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #
@router.message(Command("product_add"))
async def cmd_product_add(
    message: Message, services: Container, command: CommandObject
) -> None:
    if not _admin_only(message, services):
        return
    args = (command.args or "").split("|")
    if len(args) < 2:
        await message.answer("形式: /product_add 名前|価格|説明(任意)")
        return
    name = args[0].strip()
    try:
        price = int(args[1].strip())
    except ValueError:
        await message.answer("価格は整数で入力してください。")
        return
    description = args[2].strip() if len(args) > 2 else None
    pid = await services.products.add(name, price, description)
    await message.answer(f"商品を追加しました。 ID={pid} {name} {price}円")


@router.message(Command("product_list"))
async def cmd_product_list(message: Message, services: Container) -> None:
    if not _admin_only(message, services):
        return
    products = await services.products.list_all()
    if not products:
        await message.answer("商品はありません。")
        return
    lines = ["商品一覧:"]
    for p in products:
        state = "有効" if p.active else "無効"
        lines.append(
            f"ID={p.id} {p.name} {p.price}円 在庫{p.available_stock} [{state}]"
        )
    await message.answer("\n".join(lines))


@router.message(Command("product_edit"))
async def cmd_product_edit(
    message: Message, services: Container, command: CommandObject
) -> None:
    if not _admin_only(message, services):
        return
    args = (command.args or "").split("|")
    if len(args) != 3:
        await message.answer("形式: /product_edit id|field|value  (field=name|price|description|active)")
        return
    try:
        pid = int(args[0].strip())
    except ValueError:
        await message.answer("IDは整数で入力してください。")
        return
    field, value = args[1].strip(), args[2].strip()
    if field not in {"name", "price", "description", "active"}:
        await message.answer("field は name|price|description|active のいずれかです。")
        return
    parsed: object = value
    if field == "price":
        try:
            parsed = int(value)
        except ValueError:
            await message.answer("価格は整数で入力してください。")
            return
    elif field == "active":
        parsed = value.lower() in {"1", "true", "yes", "on"}
    ok = await services.products.edit(pid, **{field: parsed})
    await message.answer("更新しました。" if ok else "商品が見つかりません。")


@router.message(Command("product_note"))
async def cmd_product_note(
    message: Message, services: Container, command: CommandObject
) -> None:
    """Set the per-product note shown to buyers after delivery.

    形式（1行）:   /product_note <product_id> 注意事項テキスト
    形式（複数行）: /product_note <product_id>
                    1行目の注意事項
                    2行目の注意事項
    """
    if not _admin_only(message, services):
        return
    text = command.args or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "形式: /product_note <product_id> 注意事項\n"
            "（product_id の後に改行で複数行の注意事項も可）"
        )
        return
    try:
        product_id = int(parts[0].strip())
    except ValueError:
        await message.answer("product_id は整数で入力してください。")
        return
    if await services.products.get(product_id) is None:
        await message.answer("指定の商品が見つかりません。")
        return
    notes = parts[1].strip()
    ok = await services.products.set_notes(product_id, notes)
    await message.answer(
        "注意事項を設定しました。配布時に商品と一緒に送られます。"
        if ok else "設定に失敗しました。"
    )


@router.message(Command("product_delete"))
async def cmd_product_delete(
    message: Message, services: Container, command: CommandObject
) -> None:
    if not _admin_only(message, services):
        return
    try:
        pid = int((command.args or "").strip())
    except ValueError:
        await message.answer("形式: /product_delete id")
        return
    ok = await services.products.delete(pid)
    await message.answer("商品を無効化しました。" if ok else "商品が見つかりません。")


# --------------------------------------------------------------------------- #
# Stock
# --------------------------------------------------------------------------- #
async def _handle_stock_add(
    message: Message, services: Container, command: CommandObject, cmd: str
) -> None:
    """Bulk-add stock: first token = product_id, then ONE item per line.

    各行が1つの在庫（デジタル商品）になります。2行目は別の在庫として登録され、
    行同士がまとめられる（混同される）ことはありません。
    """
    if not _admin_only(message, services):
        return
    text = command.args or ""
    parts = text.split(maxsplit=1)
    if not parts or not parts[0].strip():
        await message.answer(
            f"形式: /{cmd} <product_id> の後に、改行で在庫を1行1つ入力\n"
            f"例:\n/{cmd} 1\nAAAA-BBBB-CCCC\nDDDD-EEEE-FFFF\nGGGG-HHHH-IIII\n"
            "（各行が別々の在庫として登録されます）"
        )
        return
    try:
        product_id = int(parts[0].strip())
    except ValueError:
        await message.answer("product_id は整数で入力してください。")
        return
    body = parts[1] if len(parts) > 1 else ""
    if not body.strip():
        await message.answer("追加する在庫を改行区切り（1行1つ）で入力してください。")
        return
    if await services.products.get(product_id) is None:
        await message.answer("指定の商品が見つかりません。")
        return
    added = await services.inventory.add_bulk(product_id, body)
    await message.answer(f"商品ID {product_id} に {added}件の在庫を追加しました。")


@router.message(Command("stock_add"))
async def cmd_stock_add(
    message: Message, services: Container, command: CommandObject
) -> None:
    await _handle_stock_add(message, services, command, "stock_add")


@router.message(Command("restock"))
async def cmd_restock(
    message: Message, services: Container, command: CommandObject
) -> None:
    await _handle_stock_add(message, services, command, "restock")


@router.message(Command("stock_count"))
async def cmd_stock_count(
    message: Message, services: Container, command: CommandObject
) -> None:
    if not _admin_only(message, services):
        return
    try:
        product_id = int((command.args or "").strip())
    except ValueError:
        await message.answer("形式: /stock_count <product_id>")
        return
    counts = await services.inventory.counts_by_status(product_id)
    if not counts:
        await message.answer("在庫はありません。")
        return
    lines = [f"商品 {product_id} の在庫:"]
    for status, n in counts.items():
        lines.append(f"{status}: {n}")
    await message.answer("\n".join(lines))


@router.message(Command("stock_list"))
async def cmd_stock_list(
    message: Message, services: Container, command: CommandObject
) -> None:
    if not _admin_only(message, services):
        return
    try:
        product_id = int((command.args or "").strip())
    except ValueError:
        await message.answer("形式: /stock_list <product_id>")
        return
    items = await services.inventory.list_for_product(product_id, limit=50)
    if not items:
        await message.answer("在庫はありません。")
        return
    lines = [f"商品 {product_id} の在庫 (最大50件):"]
    for it in items:
        preview = it.content if it.status == "AVAILABLE" else "****"
        lines.append(f"#{it.id} [{it.status}] {preview}")
    await message.answer("\n".join(lines))


# --------------------------------------------------------------------------- #
# Orders
# --------------------------------------------------------------------------- #
@router.message(Command("orders"))
async def cmd_orders(message: Message, services: Container) -> None:
    if not _admin_only(message, services):
        return
    orders = await services.orders.list_recent(20)
    if not orders:
        await message.answer("注文はありません。")
        return
    lines = ["最近の注文:"]
    for o in orders:
        lines.append(f"{o.order_code} [{o.status}] {o.price}円 user={o.telegram_user_id}")
    await message.answer("\n".join(lines))


@router.message(Command("order"))
async def cmd_order(
    message: Message, services: Container, command: CommandObject
) -> None:
    if not _admin_only(message, services):
        return
    code = (command.args or "").strip()
    if not code:
        await message.answer("形式: /order ORD-XXXXXX")
        return
    order = await services.orders.get_by_code(code)
    if order is None:
        await message.answer("注文が見つかりません。")
        return
    product = await services.products.get(order.product_id)
    await message.answer(
        f"注文 {order.order_code}\n"
        f"状態: {order.status}\n"
        f"商品: {product.name if product else order.product_id}\n"
        f"金額: {order.price}円\n"
        f"user: {order.telegram_user_id}\n"
        f"link_id: {order.paypay_link_id or '-'}\n"
        f"payment_id: {order.external_payment_id or '-'}\n"
        f"作成: {order.created_at}\n"
        f"支払: {order.paid_at or '-'}\n"
        f"配布: {order.delivered_at or '-'}"
    )


@router.message(Command("retry_delivery"))
async def cmd_retry_delivery(
    message: Message, services: Container, command: CommandObject, bot: Bot
) -> None:
    if not _admin_only(message, services):
        return
    code = (command.args or "").strip()
    order = await services.orders.get_by_code(code)
    if order is None:
        await message.answer("注文が見つかりません。")
        return
    result = await services.payments.deliver_order(order.id)
    if result.outcome == PurchaseOutcome.DELIVERED:
        await message.answer("すでに配布済みです。")
        return
    if result.outcome == PurchaseOutcome.OUT_OF_STOCK:
        await message.answer("在庫が不足しています。在庫を追加してください。")
        return
    if result.delivered_content:
        delivered = await deliver_to_buyer(
            bot, services, order.id, order.telegram_user_id, result
        )
        await message.answer("再配布に成功しました。" if delivered else "配布に失敗しました。")
    else:
        await message.answer(f"再配布できませんでした（状態: {result.outcome.value}）。")


@router.message(Command("cancel_order"))
async def cmd_cancel_order(
    message: Message, services: Container, command: CommandObject
) -> None:
    if not _admin_only(message, services):
        return
    code = (command.args or "").strip()
    ok = await services.orders.cancel(code)
    if ok:
        await services.inventory.release(
            (await services.orders.get_by_code(code)).id  # type: ignore[union-attr]
        )
    await message.answer("注文をキャンセルしました。" if ok else "キャンセルできません。")
