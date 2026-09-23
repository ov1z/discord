"""PayPay temporary hold (一次保留): notify, wait, recheck, deliver only if received."""
from __future__ import annotations

from types import SimpleNamespace

from bot.container import Container
from bot.handlers import payment as payment_handlers
from config import Settings
from conftest import Shop, make_product_with_stock, mock_link
from database.models import OrderStatus
from services.payment_service import PurchaseOutcome


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))

    async def get_chat(self, chat_id: int):
        return SimpleNamespace(username="buyer", first_name="Buyer")


def _container(shop: Shop, seconds: int = 0) -> Container:
    settings = Settings(
        HOLD_RECHECK_SECONDS=seconds, ADMIN_TELEGRAM_ID=999, TELEGRAM_BOT_TOKEN="x"
    )
    return Container(
        settings=settings,
        products=shop.products,
        inventory=shop.inventory,
        orders=shop.orders,
        payments=shop.payments,
        paypay=None,  # type: ignore[arg-type]  (unused here)
        provider=shop.provider,
    )


async def _order(shop: Shop, pid: int) -> int:
    res = await shop.orders.create_order(1, pid)
    assert res is not None
    return res.order_id


async def _status(shop: Shop, oid: int) -> str:
    return (await shop.orders.get(oid)).status


async def test_hold_is_reported_and_not_delivered(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=1)
    oid = await _order(shop, pid)
    shop.provider.hold_on_accept.add("HOLD1")
    result = await shop.payments.process_payment_link(oid, mock_link(500, "HOLD1"))
    assert result.outcome == PurchaseOutcome.PAYMENT_HELD
    assert result.delivered_content is None
    assert await _status(shop, oid) == OrderStatus.PAYMENT_UNKNOWN.value
    assert await shop.inventory.count_available(pid) == 1  # nothing reserved


async def test_hold_released_then_recheck_delivers(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=1)
    oid = await _order(shop, pid)
    shop.provider.hold_on_accept.add("HOLD2")
    await shop.payments.process_payment_link(oid, mock_link(500, "HOLD2"))

    shop.provider.hold_on_accept.discard("HOLD2")  # buyer released the hold
    result = await shop.payments.reverify_and_settle(oid, retry_accept=True)
    assert result.delivered_content is not None
    await shop.payments.confirm_delivered(oid)
    assert await _status(shop, oid) == OrderStatus.DELIVERED.value


async def test_hold_not_released_stays_undelivered(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=1)
    oid = await _order(shop, pid)
    shop.provider.hold_on_accept.add("HOLD3")
    await shop.payments.process_payment_link(oid, mock_link(500, "HOLD3"))

    result = await shop.payments.reverify_and_settle(oid, retry_accept=True)
    assert result.outcome == PurchaseOutcome.PAYMENT_HELD
    assert result.delivered_content is None
    assert await _status(shop, oid) == OrderStatus.PAYMENT_UNKNOWN.value
    assert await shop.inventory.count_available(pid) == 1


async def test_recheck_never_double_receives(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=2)
    oid = await _order(shop, pid)
    shop.provider.hold_on_accept.add("HOLD4")
    await shop.payments.process_payment_link(oid, mock_link(500, "HOLD4"))
    shop.provider.hold_on_accept.discard("HOLD4")

    r1 = await shop.payments.reverify_and_settle(oid, retry_accept=True)
    r2 = await shop.payments.reverify_and_settle(oid, retry_accept=True)
    assert r1.delivered_content is not None
    assert r2.delivered_content == r1.delivered_content  # same item, no 2nd accept
    assert await shop.inventory.count_available(pid) == 1


async def test_scheduled_recheck_delivers_to_buyer(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=1)
    oid = await _order(shop, pid)
    shop.provider.hold_on_accept.add("HOLD5")
    await shop.payments.process_payment_link(oid, mock_link(500, "HOLD5"))
    shop.provider.hold_on_accept.discard("HOLD5")

    bot = FakeBot()
    await payment_handlers._hold_recheck(bot, _container(shop), oid, 1)
    buyer_msgs = [t for c, t in bot.sent if c == 1]
    assert any("購入ありがとうございます" in t for t in buyer_msgs)
    assert await _status(shop, oid) == OrderStatus.DELIVERED.value


async def test_scheduled_recheck_still_held_notifies(shop: Shop) -> None:
    pid = await make_product_with_stock(shop, price=500, stock=1)
    oid = await _order(shop, pid)
    shop.provider.hold_on_accept.add("HOLD6")
    await shop.payments.process_payment_link(oid, mock_link(500, "HOLD6"))

    bot = FakeBot()
    await payment_handlers._hold_recheck(bot, _container(shop), oid, 1)
    assert any("保留の解除が確認できませんでした" in t for c, t in bot.sent if c == 1)
    assert any("/verify_order" in t for c, t in bot.sent if c == 999)
    assert await _status(shop, oid) == OrderStatus.PAYMENT_UNKNOWN.value


def test_hold_message_mentions_deadline() -> None:
    assert "1分以内" in payment_handlers.hold_message(60)
    assert "30秒以内" in payment_handlers.hold_message(30)
