"""Broadcast a message to every registered user (admin only).

Available both as /broadcast (send the text after the command, or reply flow)
and from the admin button panel (📢 一括送信).

Sending is rate-limited and tolerant: users who blocked the bot or deactivated
their account are skipped and counted, never crashing the run.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.container import Container
from bot.states.admin import AdminStates

logger = logging.getLogger("bot.broadcast")

router = Router(name="broadcast")

# Telegram allows ~30 msg/s to different users; stay well under it.
_SEND_INTERVAL = 0.05  # 20/s


@dataclass(slots=True)
class BroadcastResult:
    total: int
    sent: int
    failed: int


async def broadcast(bot: Bot, services: Container, text: str) -> BroadcastResult:
    """Send *text* to every registered user. Returns delivery counts."""
    ids = await services.users.all_ids()
    sent = 0
    failed = 0
    for uid in ids:
        try:
            await bot.send_message(uid, text)
            sent += 1
        except Exception:  # noqa: BLE001 - blocked/deactivated/etc.
            failed += 1
        await asyncio.sleep(_SEND_INTERVAL)
    logger.info("broadcast done: %d sent / %d failed", sent, failed)
    return BroadcastResult(total=len(ids), sent=sent, failed=failed)


def _admin(message: Message, services: Container) -> bool:
    return message.from_user is not None and services.is_admin(message.from_user.id)


@router.message(Command("broadcast"))
async def cmd_broadcast(
    message: Message, services: Container, command: CommandObject, state: FSMContext
) -> None:
    if not _admin(message, services):
        return
    text = (command.args or "").strip()
    if not text:
        # No inline text -> ask for it via the FSM.
        await state.set_state(AdminStates.BROADCAST)
        count = await services.users.count()
        await message.answer(
            f"📢 登録ユーザー {count} 人へ送るメッセージを送信してください。\n"
            "（送信した内容がそのまま全員に配信されます）"
        )
        return
    await _run_and_report(message, services, text)


@router.message(AdminStates.BROADCAST, F.text)
async def on_broadcast_text(
    message: Message, services: Container, state: FSMContext
) -> None:
    if not _admin(message, services):
        return
    await state.clear()
    await _run_and_report(message, services, (message.text or "").strip())


async def _run_and_report(message: Message, services: Container, text: str) -> None:
    if not text:
        await message.answer("送信内容が空です。")
        return
    await message.answer("📢 一括送信を開始します…")
    result = await broadcast(message.bot, services, text)
    await message.answer(
        f"✅ 一括送信完了\n対象: {result.total}人 / 成功: {result.sent} / 失敗: {result.failed}"
    )
