"""Shop: product detail, quantity selection, and order creation."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.container import Container
from bot.keyboards.products import product_detail_keyboard, shop_list_keyboard
from bot.keyboards.purchase import cancel_purchase_keyboard
from bot.states.purchase import PurchaseStates
from services import pricing
from services.product_service import ProductView

router = Router(name="products")

# A single buyer cannot order more than this at once (sanity cap).
_MAX_QTY = 1000


def _detail_text(view: ProductView) -> str:
    lines = [f"🧾 {view.name}"]
    if view.description:
        lines.append("")
        lines.append(view.description)
    lines.append("")
    lines.append("単価:")
    lines.append(pricing.format_tiers(view.tiers))
    lines.append("")
    stock = f"{view.available_stock}個" if view.available_stock > 0 else "入荷待ち"
    lines.append(f"現在庫: {stock}")
    if view.available_stock > 0:
        lines.append("")
        lines.append("購入数量を選んでください:")
    return "\n".join(lines)


async def _show_detail(callback: CallbackQuery, services: Container, product_id: int) -> None:
    view = await services.products.get_view(product_id)
    if view is None or not view.active:
        await callback.answer("この商品は購入できません。", show_alert=True)
        return
    text = _detail_text(view)
    markup = product_detail_keyboard(view)
    if callback.message is not None:
        try:
            await callback.message.edit_text(text, reply_markup=markup)
        except Exception:  # noqa: BLE001
            await callback.message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("shop:view:"))
async def cb_view(callback: CallbackQuery, services: Container) -> None:
    product_id = int(callback.data.split(":")[2])
    await _show_detail(callback, services, product_id)
    await callback.answer()


@router.callback_query(F.data == "shop:list")
async def cb_list(callback: CallbackQuery, services: Container, state: FSMContext) -> None:
    await state.clear()
    products = await services.products.list_for_shop()
    text = (
        "🛒 商品一覧\n\n"
        "商品を押すと、価格・在庫・まとめ買い割引を確認して購入できます。\n\n"
        "現在の価格・在庫:"
    )
    if callback.message is not None:
        try:
            await callback.message.edit_text(text, reply_markup=shop_list_keyboard(products))
        except Exception:  # noqa: BLE001
            await callback.message.answer(text, reply_markup=shop_list_keyboard(products))
    await callback.answer()


@router.callback_query(F.data.startswith("shop:qty:"))
async def cb_custom_qty(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = int(callback.data.split(":")[2])
    await state.set_state(PurchaseStates.CHOOSING_QUANTITY)
    await state.update_data(product_id=product_id)
    if callback.message is not None:
        await callback.message.answer("購入したい数量を数字で送ってください（例: 3）")
    await callback.answer()


@router.message(PurchaseStates.CHOOSING_QUANTITY, F.text)
async def on_custom_qty(message: Message, services: Container, state: FSMContext) -> None:
    data = await state.get_data()
    product_id = data.get("product_id")
    if product_id is None:
        await state.clear()
        await message.answer("商品情報が見つかりません。/start からやり直してください。")
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) < 1:
        await message.answer("1以上の数字で送ってください（例: 3）")
        return
    quantity = min(int(raw), _MAX_QTY)
    await _start_order(message, services, state, message.from_user.id, product_id, quantity)


@router.callback_query(F.data.startswith("shop:buy:"))
async def cb_buy(callback: CallbackQuery, services: Container, state: FSMContext) -> None:
    parts = callback.data.split(":")
    product_id, quantity = int(parts[2]), int(parts[3])
    if callback.message is None:
        await callback.answer()
        return
    await callback.answer()
    await _start_order(
        callback.message, services, state, callback.from_user.id, product_id, quantity
    )


async def _start_order(
    target: Message, services: Container, state: FSMContext,
    buyer_id: int, product_id: int, quantity: int,
) -> None:
    result = await services.orders.create_order(buyer_id, product_id, quantity)
    if result is None:
        await target.answer("この商品は購入できません。")
        return
    if result.out_of_stock:
        await target.answer(
            f"在庫が不足しています（購入希望: {result.quantity}個）。"
            "数量を減らすか、入荷までお待ちください。"
        )
        return

    ttl_minutes = max(1, services.settings.order_ttl_seconds // 60)
    qty_line = (
        f"数量: {result.quantity}個（@{result.unit_price:,}円）\n"
        if result.quantity > 1 else ""
    )
    text = (
        f"{result.product_name} を購入します。\n\n"
        f"{qty_line}"
        f"金額: {result.price:,}円\n"
        f"注文ID: {result.order_code}\n\n"
        f"PayPayアプリから{result.price:,}円の送金リンクを作成し、"
        "このチャットに送信してください。\n"
        f"送金金額は必ず{result.price:,}円にしてください。\n\n"
        f"注文有効時間: {ttl_minutes}分"
    )
    await state.set_state(PurchaseStates.WAITING_PAYPAY_LINK)
    await state.update_data(order_id=result.order_id, order_code=result.order_code)
    await target.answer(text, reply_markup=cancel_purchase_keyboard(result.order_code))
