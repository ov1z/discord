"""Buy-button handling: create an order and start the payment wait."""
from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.container import Container
from bot.keyboards.purchase import cancel_purchase_keyboard
from bot.states.purchase import PurchaseStates

router = Router(name="products")


@router.callback_query(lambda c: c.data and c.data.startswith("buy:"))
async def cb_buy(callback: CallbackQuery, services: Container, state: FSMContext) -> None:
    assert callback.data is not None
    try:
        product_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("不正な操作です。", show_alert=True)
        return

    user = callback.from_user
    result = await services.orders.create_order(user.id, product_id)
    if result is None:
        await callback.answer("この商品は購入できません。", show_alert=True)
        return

    ttl_minutes = max(1, services.settings.order_ttl_seconds // 60)
    text = (
        f"{result.product_name}を購入します。\n\n"
        f"金額: {result.price}円\n"
        f"注文ID: {result.order_code}\n\n"
        f"PayPayアプリから{result.price}円の送金リンクを作成し、"
        "このチャットに送信してください。\n"
        f"送金金額は必ず{result.price}円にしてください。\n\n"
        f"注文有効時間: {ttl_minutes}分"
    )
    if result.reused:
        text = "処理中の注文があります。\n\n" + text

    await state.set_state(PurchaseStates.WAITING_PAYPAY_LINK)
    await state.update_data(order_id=result.order_id, order_code=result.order_code)

    if callback.message is not None:
        await callback.message.answer(
            text, reply_markup=cancel_purchase_keyboard(result.order_code)
        )
    await callback.answer()
