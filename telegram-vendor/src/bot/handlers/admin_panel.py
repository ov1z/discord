"""Button-driven admin panel (no slash commands needed).

Opened with /admin by the admin (ADMIN_TELEGRAM_ID) only. Everything —
add product, restock, set notes, manage orders, PayPay login/logout — is done
with inline buttons. Non-admins never see it (the shop buy flow is unchanged).

Shares the order actions with handlers/admin.py so behaviour is identical to
the slash commands.
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.container import Container
from bot.handlers.admin import do_cancel_order, do_retry_delivery, do_verify_order
from bot.handlers.login import prompt_login
from bot.keyboards import admin as kb
from bot.states.admin import AdminStates
from services.paypay_service import AuthState

logger = logging.getLogger("bot.admin_panel")

router = Router(name="admin_panel")


def _is_admin_cb(callback: CallbackQuery, services: Container) -> bool:
    return services.is_admin(callback.from_user.id)


def _home_only():
    """A keyboard with just a back-to-menu button."""
    from aiogram.types import InlineKeyboardMarkup
    return InlineKeyboardMarkup(inline_keyboard=[kb.back_button()])


async def _show(callback: CallbackQuery, text: str, markup) -> None:
    """Edit the panel message in place, falling back to a new message."""
    msg = callback.message
    if msg is None:
        return
    try:
        await msg.edit_text(text, reply_markup=markup)
    except Exception:  # noqa: BLE001 - message unchanged / too old
        await msg.answer(text, reply_markup=markup)


# --------------------------------------------------------------------------- #
# Entry
# --------------------------------------------------------------------------- #
@router.message(Command("admin"))
async def cmd_admin(message: Message, services: Container, state: FSMContext) -> None:
    if message.from_user is None or not services.is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer("🛠 管理者メニュー", reply_markup=kb.admin_menu_keyboard())


@router.callback_query(F.data == "ap:home")
async def cb_home(callback: CallbackQuery, services: Container, state: FSMContext) -> None:
    if not _is_admin_cb(callback, services):
        await callback.answer("権限がありません。", show_alert=True)
        return
    await state.clear()
    await _show(callback, "🛠 管理者メニュー", kb.admin_menu_keyboard())
    await callback.answer()


# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "ap:products")
async def cb_products(callback: CallbackQuery, services: Container) -> None:
    if not _is_admin_cb(callback, services):
        await callback.answer("権限がありません。", show_alert=True)
        return
    products = await services.products.list_all()
    if products:
        lines = ["🛍 商品一覧"]
        for p in products:
            state_mark = "" if p.active else "（無効）"
            note = " 📝" if p.notes else ""
            lines.append(f"ID{p.id}: {p.name} {p.price}円 / 在庫{p.available_stock}{note}{state_mark}")
        text = "\n".join(lines)
    else:
        text = "商品はまだありません。「商品追加」から登録してください。"
    await _show(callback, text, kb.products_menu_keyboard())
    await callback.answer()


@router.callback_query(F.data == "ap:add_product")
async def cb_add_product(callback: CallbackQuery, services: Container, state: FSMContext) -> None:
    if not _is_admin_cb(callback, services):
        await callback.answer("権限がありません。", show_alert=True)
        return
    await state.set_state(AdminStates.ADD_PRODUCT)
    await _show(
        callback,
        "➕ 追加する商品を次の形式で送ってください:\n\n"
        "名前|価格|説明\n\n"
        "例: 商品A|500|プレミアムコード",
        kb.cancel_input_keyboard("ap:products"),
    )
    await callback.answer()


@router.message(AdminStates.ADD_PRODUCT, F.text)
async def on_add_product(message: Message, services: Container, state: FSMContext) -> None:
    if message.from_user is None or not services.is_admin(message.from_user.id):
        return
    args = (message.text or "").split("|")
    if len(args) < 2 or not args[0].strip():
        await message.answer("形式が違います。 名前|価格|説明 で送ってください。")
        return
    try:
        price = int(args[1].strip())
    except ValueError:
        await message.answer("価格は整数で入力してください。")
        return
    name = args[0].strip()
    description = args[2].strip() if len(args) > 2 else None
    pid = await services.products.add(name, price, description)
    await state.clear()
    await message.answer(
        f"✅ 商品を追加しました。 ID{pid}: {name} {price}円",
        reply_markup=kb.products_menu_keyboard(),
    )


@router.callback_query(F.data == "ap:del")
async def cb_del_pick(callback: CallbackQuery, services: Container) -> None:
    if not _is_admin_cb(callback, services):
        await callback.answer("権限がありません。", show_alert=True)
        return
    products = await services.products.list_all()
    if not products:
        await callback.answer("商品がありません。", show_alert=True)
        return
    await _show(callback, "🗑 無効化する商品を選んでください:",
                kb.product_picker_keyboard(products, "delp"))
    await callback.answer()


@router.callback_query(F.data.startswith("ap:delp:"))
async def cb_del_do(callback: CallbackQuery, services: Container) -> None:
    if not _is_admin_cb(callback, services):
        await callback.answer("権限がありません。", show_alert=True)
        return
    pid = int(callback.data.split(":")[2])
    await services.products.delete(pid)
    await callback.answer("無効化しました。", show_alert=True)
    await _show(callback, "🗑 削除完了。商品管理に戻ります。", kb.products_menu_keyboard())


# --------------------------------------------------------------------------- #
# Restock
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "ap:restock")
async def cb_restock_pick(callback: CallbackQuery, services: Container) -> None:
    if not _is_admin_cb(callback, services):
        await callback.answer("権限がありません。", show_alert=True)
        return
    products = await services.products.list_all()
    if not products:
        await callback.answer("先に商品を追加してください。", show_alert=True)
        return
    await _show(callback, "📦 在庫を追加する商品を選んでください:",
                kb.product_picker_keyboard(products, "restockp", back="ap:home"))
    await callback.answer()


@router.callback_query(F.data.startswith("ap:restockp:"))
async def cb_restock_start(callback: CallbackQuery, services: Container, state: FSMContext) -> None:
    if not _is_admin_cb(callback, services):
        await callback.answer("権限がありません。", show_alert=True)
        return
    pid = int(callback.data.split(":")[2])
    product = await services.products.get(pid)
    if product is None:
        await callback.answer("商品が見つかりません。", show_alert=True)
        return
    await state.set_state(AdminStates.RESTOCK)
    await state.update_data(product_id=pid)
    await _show(
        callback,
        f"📦「{product.name}」の在庫を、1行に1つずつ送ってください。\n\n"
        "例:\nAAAA-BBBB-CCCC\nDDDD-EEEE-FFFF\nGGGG-HHHH-IIII\n\n"
        "（各行が別々の在庫として登録されます）",
        kb.cancel_input_keyboard("ap:home"),
    )
    await callback.answer()


@router.message(AdminStates.RESTOCK, F.text)
async def on_restock(message: Message, services: Container, state: FSMContext) -> None:
    if message.from_user is None or not services.is_admin(message.from_user.id):
        return
    data = await state.get_data()
    pid = data.get("product_id")
    if pid is None:
        await state.clear()
        await message.answer("対象商品が不明です。最初からやり直してください。")
        return
    added = await services.inventory.add_bulk(pid, message.text or "")
    await state.clear()
    await message.answer(
        f"✅ 商品ID{pid} に {added}件の在庫を追加しました。",
        reply_markup=kb.admin_menu_keyboard(),
    )


# --------------------------------------------------------------------------- #
# Notes
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "ap:note")
async def cb_note_pick(callback: CallbackQuery, services: Container) -> None:
    if not _is_admin_cb(callback, services):
        await callback.answer("権限がありません。", show_alert=True)
        return
    products = await services.products.list_all()
    if not products:
        await callback.answer("先に商品を追加してください。", show_alert=True)
        return
    await _show(callback, "📝 注意事項を設定する商品を選んでください:",
                kb.product_picker_keyboard(products, "notep"))
    await callback.answer()


@router.callback_query(F.data.startswith("ap:notep:"))
async def cb_note_start(callback: CallbackQuery, services: Container, state: FSMContext) -> None:
    if not _is_admin_cb(callback, services):
        await callback.answer("権限がありません。", show_alert=True)
        return
    pid = int(callback.data.split(":")[2])
    product = await services.products.get(pid)
    if product is None:
        await callback.answer("商品が見つかりません。", show_alert=True)
        return
    await state.set_state(AdminStates.SET_NOTE)
    await state.update_data(product_id=pid)
    current = f"\n\n現在の注意事項:\n{product.notes}" if product.notes else ""
    await _show(
        callback,
        f"📝「{product.name}」の注意事項を送ってください（配布後に商品と一緒に届きます）。"
        f"{current}",
        kb.cancel_input_keyboard("ap:products"),
    )
    await callback.answer()


@router.message(AdminStates.SET_NOTE, F.text)
async def on_note(message: Message, services: Container, state: FSMContext) -> None:
    if message.from_user is None or not services.is_admin(message.from_user.id):
        return
    data = await state.get_data()
    pid = data.get("product_id")
    if pid is None:
        await state.clear()
        await message.answer("対象商品が不明です。最初からやり直してください。")
        return
    await services.products.set_notes(pid, (message.text or "").strip())
    await state.clear()
    await message.answer(
        "✅ 注意事項を設定しました。配布時に商品と一緒に送られます。",
        reply_markup=kb.products_menu_keyboard(),
    )


# --------------------------------------------------------------------------- #
# Price tiers (bulk discount)
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "ap:tiers")
async def cb_tiers_pick(callback: CallbackQuery, services: Container) -> None:
    if not _is_admin_cb(callback, services):
        await callback.answer("権限がありません。", show_alert=True)
        return
    products = await services.products.list_all()
    if not products:
        await callback.answer("先に商品を追加してください。", show_alert=True)
        return
    await _show(callback, "💹 価格を設定する商品を選んでください:",
                kb.product_picker_keyboard(products, "tierp"))
    await callback.answer()


@router.callback_query(F.data.startswith("ap:tierp:"))
async def cb_tiers_start(callback: CallbackQuery, services: Container, state: FSMContext) -> None:
    if not _is_admin_cb(callback, services):
        await callback.answer("権限がありません。", show_alert=True)
        return
    pid = int(callback.data.split(":")[2])
    view = await services.products.get_view(pid)
    if view is None:
        await callback.answer("商品が見つかりません。", show_alert=True)
        return
    from services import pricing
    await state.set_state(AdminStates.SET_TIERS)
    await state.update_data(product_id=pid)
    await _show(
        callback,
        f"💹「{view.name}」の数量別価格を送ってください。\n\n"
        "形式: 数量:単価 をカンマ区切り\n"
        "例: 1:1800,5:1600,10:1500,50:1000\n\n"
        f"現在:\n{pricing.format_tiers(view.tiers)}",
        kb.cancel_input_keyboard("ap:products"),
    )
    await callback.answer()


@router.message(AdminStates.SET_TIERS, F.text)
async def on_tiers(message: Message, services: Container, state: FSMContext) -> None:
    if message.from_user is None or not services.is_admin(message.from_user.id):
        return
    from services import pricing
    data = await state.get_data()
    pid = data.get("product_id")
    if pid is None:
        await state.clear()
        await message.answer("対象商品が不明です。最初からやり直してください。")
        return
    tiers_json = pricing.parse_tiers_text(message.text or "")
    if tiers_json is None:
        await message.answer("形式が違います。例: 1:1800,5:1600,10:1500,50:1000")
        return
    await services.products.set_tiers(pid, tiers_json)
    await state.clear()
    tiers = pricing.parse_tiers(tiers_json, 0)
    await message.answer(
        "✅ 価格を設定しました。\n" + pricing.format_tiers(tiers),
        reply_markup=kb.products_menu_keyboard(),
    )


# --------------------------------------------------------------------------- #
# Stock overview
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "ap:stock")
async def cb_stock(callback: CallbackQuery, services: Container) -> None:
    if not _is_admin_cb(callback, services):
        await callback.answer("権限がありません。", show_alert=True)
        return
    products = await services.products.list_all()
    if not products:
        await _show(callback, "商品がありません。", kb.admin_menu_keyboard())
        await callback.answer()
        return
    lines = ["📊 在庫状況"]
    for p in products:
        counts = await services.inventory.counts_by_status(p.id)
        avail = counts.get("AVAILABLE", 0)
        sold = counts.get("SOLD", 0)
        reserved = counts.get("RESERVED", 0)
        lines.append(f"ID{p.id} {p.name}: 在庫{avail} / 予約{reserved} / 販売済{sold}")
    await _show(callback, "\n".join(lines), _home_only())
    await callback.answer()


# --------------------------------------------------------------------------- #
# Orders
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "ap:orders")
async def cb_orders(callback: CallbackQuery, services: Container) -> None:
    if not _is_admin_cb(callback, services):
        await callback.answer("権限がありません。", show_alert=True)
        return
    attention = await services.orders.list_needs_attention()
    if attention:
        text = "📋 要対応の注文（未配布・保留・状態不明）"
        await _show(callback, text, kb.orders_keyboard(attention))
    else:
        recent = await services.orders.list_recent(10)
        if recent:
            text = "📋 要対応の注文はありません。\n\n最近の注文:"
            await _show(callback, text, kb.orders_keyboard(list(recent)))
        else:
            await _show(callback, "注文はまだありません。", _home_only())
    await callback.answer()


@router.callback_query(F.data.startswith("ap:order:"))
async def cb_order_detail(callback: CallbackQuery, services: Container) -> None:
    if not _is_admin_cb(callback, services):
        await callback.answer("権限がありません。", show_alert=True)
        return
    code = callback.data.split(":", 2)[2]
    order = await services.orders.get_by_code(code)
    if order is None:
        await callback.answer("注文が見つかりません。", show_alert=True)
        return
    product = await services.products.get(order.product_id)
    text = (
        f"📋 注文 {order.order_code}\n"
        f"状態: {order.status}\n"
        f"商品: {product.name if product else order.product_id}\n"
        f"金額: {order.price}円\n"
        f"購入者ID: {order.telegram_user_id}\n"
        f"payment_id: {order.external_payment_id or '-'}\n"
        f"支払: {order.paid_at or '-'} / 配布: {order.delivered_at or '-'}"
    )
    await _show(callback, text, kb.order_actions_keyboard(order.order_code))
    await callback.answer()


@router.callback_query(F.data.startswith("ap:retry:"))
async def cb_order_retry(callback: CallbackQuery, services: Container, bot: Bot) -> None:
    if not _is_admin_cb(callback, services):
        await callback.answer("権限がありません。", show_alert=True)
        return
    code = callback.data.split(":", 2)[2]
    order = await services.orders.get_by_code(code)
    if order is None:
        await callback.answer("注文が見つかりません。", show_alert=True)
        return
    result = await do_retry_delivery(bot, services, order)
    await callback.answer(result, show_alert=True)
    await cb_order_detail(callback, services)


@router.callback_query(F.data.startswith("ap:verify:"))
async def cb_order_verify(callback: CallbackQuery, services: Container, bot: Bot) -> None:
    if not _is_admin_cb(callback, services):
        await callback.answer("権限がありません。", show_alert=True)
        return
    code = callback.data.split(":", 2)[2]
    order = await services.orders.get_by_code(code)
    if order is None:
        await callback.answer("注文が見つかりません。", show_alert=True)
        return
    result = await do_verify_order(bot, services, order)
    await callback.answer(result, show_alert=True)
    await cb_order_detail(callback, services)


@router.callback_query(F.data.startswith("ap:cancel:"))
async def cb_order_cancel(callback: CallbackQuery, services: Container) -> None:
    if not _is_admin_cb(callback, services):
        await callback.answer("権限がありません。", show_alert=True)
        return
    code = callback.data.split(":", 2)[2]
    order = await services.orders.get_by_code(code)
    if order is None:
        await callback.answer("注文が見つかりません。", show_alert=True)
        return
    result = await do_cancel_order(services, order)
    await callback.answer(result, show_alert=True)
    await cb_order_detail(callback, services)


# --------------------------------------------------------------------------- #
# Broadcast
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "ap:broadcast")
async def cb_broadcast(callback: CallbackQuery, services: Container, state: FSMContext) -> None:
    if not _is_admin_cb(callback, services):
        await callback.answer("権限がありません。", show_alert=True)
        return
    count = await services.users.count()
    await state.set_state(AdminStates.BROADCAST)
    await _show(
        callback,
        f"📢 登録ユーザー {count} 人へ送るメッセージを送信してください。\n"
        "送信した内容がそのまま全員に配信されます。",
        kb.cancel_input_keyboard("ap:home"),
    )
    await callback.answer()


# --------------------------------------------------------------------------- #
# PayPay
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "ap:paypay")
async def cb_paypay(callback: CallbackQuery, services: Container) -> None:
    if not _is_admin_cb(callback, services):
        await callback.answer("権限がありません。", show_alert=True)
        return
    ready = await services.provider.is_ready()
    view = await services.paypay.status(ready)
    authed = view.state == AuthState.AUTHENTICATED
    exp = view.token_expires_at.strftime("%Y-%m-%d %H:%M UTC") if view.token_expires_at else "不明"
    text = (
        "💴 PayPay状態\n"
        f"ログイン: {'✅ 済み' if authed else '❌ 未ログイン'}\n"
        f"トークン有効期限: {exp}\n"
        f"PaymentProvider: {'利用可能' if ready else '利用不可'}\n"
        f"保存済みセッション: {'あり' if view.has_saved_session else 'なし'}"
    )
    await _show(callback, text, kb.paypay_keyboard(authed))
    await callback.answer()


@router.callback_query(F.data == "ap:login")
async def cb_login(callback: CallbackQuery, services: Container, state: FSMContext) -> None:
    if not _is_admin_cb(callback, services):
        await callback.answer("権限がありません。", show_alert=True)
        return
    await callback.answer()
    if callback.message is not None:
        await prompt_login(callback.message, services, state)


@router.callback_query(F.data == "ap:logout")
async def cb_logout(callback: CallbackQuery, services: Container, state: FSMContext) -> None:
    if not _is_admin_cb(callback, services):
        await callback.answer("権限がありません。", show_alert=True)
        return
    await services.paypay.logout()
    await state.clear()
    await callback.answer("ログアウトしました。", show_alert=True)
    await cb_paypay(callback, services)
