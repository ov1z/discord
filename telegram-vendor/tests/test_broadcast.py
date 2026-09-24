"""Broadcast to all registered users: counts, and tolerance to blocked users."""
from __future__ import annotations

from bot.container import Container
from bot.handlers.broadcast import broadcast
from config import Settings
from conftest import Shop
from database.repository import UserRepository


class FakeBot:
    def __init__(self, block: set[int] | None = None) -> None:
        self.block = block or set()
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        if chat_id in self.block:
            raise RuntimeError("bot was blocked by the user")
        self.sent.append((chat_id, text))


def _container(shop: Shop) -> Container:
    return Container(
        settings=Settings(ADMIN_TELEGRAM_ID=1, TELEGRAM_BOT_TOKEN="x"),
        products=shop.products,
        inventory=shop.inventory,
        orders=shop.orders,
        payments=shop.payments,
        paypay=None,
        provider=shop.provider,
        users=shop.users,
    )


async def _register(shop: Shop, *ids: int) -> None:
    async with shop.sm() as session:
        repo = UserRepository(session)
        for i in ids:
            await repo.upsert(i, f"user{i}", "N")
        await session.commit()


async def test_broadcast_sends_to_all(shop: Shop) -> None:
    await _register(shop, 10, 20, 30)
    bot = FakeBot()
    result = await broadcast(bot, _container(shop), "セール開催中！")
    assert result.total == 3
    assert result.sent == 3
    assert result.failed == 0
    assert {c for c, _ in bot.sent} == {10, 20, 30}
    assert all(t == "セール開催中！" for _, t in bot.sent)


async def test_broadcast_skips_blocked_users(shop: Shop) -> None:
    await _register(shop, 10, 20, 30)
    bot = FakeBot(block={20})
    result = await broadcast(bot, _container(shop), "お知らせ")
    assert result.total == 3
    assert result.sent == 2
    assert result.failed == 1
    assert {c for c, _ in bot.sent} == {10, 30}


async def test_broadcast_no_users(shop: Shop) -> None:
    result = await broadcast(FakeBot(), _container(shop), "hi")
    assert result.total == 0 and result.sent == 0
