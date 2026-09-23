"""Admin PayPay login flow: /login, /logout, /paypay_status.

Security:
  * Admin-only (Telegram user id must equal ADMIN_TELEGRAM_ID).
  * /login only in a private chat.
  * phone / password / OTP are never stored, logged, or echoed; the messages
    containing them are deleted immediately after processing.
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.container import Container
from bot.states.login import LoginStates
from paypay.exceptions import PayPayError, PayPayOTPRequired
from paypay.models import LoginStatus
from services.paypay_service import AuthState

logger = logging.getLogger("bot.login")

router = Router(name="login")


def _is_private(message: Message) -> bool:
    return message.chat.type == "private"


async def _delete_silently(message: Message) -> None:
    try:
        await message.delete()
    except Exception:  # noqa: BLE001
        pass


def _fmt_status(view, ready: bool) -> str:
    exp = (
        view.token_expires_at.strftime("%Y-%m-%d %H:%M UTC")
        if view.token_expires_at
        else "不明"
    )
    last = (
        view.last_api_call_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        if view.last_api_call_at
        else "なし"
    )
    return (
        "PayPay状態\n"
        f"ログイン状態: {'ログイン済み' if view.state == AuthState.AUTHENTICATED else '未ログイン'}\n"
        f"アカウント: {view.account_id or '-'}\n"
        f"トークン有効期限: {exp}\n"
        f"PaymentProvider: {'利用可能' if ready else '利用不可'}\n"
        f"最後のAPI通信: {last}\n"
        f"保存済みセッション: {'あり' if view.has_saved_session else 'なし'}"
    )


@router.message(Command("login"))
async def cmd_login(message: Message, services: Container, state: FSMContext) -> None:
    if not services.is_admin(message.from_user.id):
        return
    if not _is_private(message):
        await message.answer("/login は管理者との個人チャットでのみ実行できます。")
        return

    if services.paypay.is_authenticated():
        ready = await services.provider.is_ready()
        view = await services.paypay.status(ready)
        await message.answer("PayPayにはすでにログイン済みです。\n\n" + _fmt_status(view, ready))
        return

    await state.set_state(LoginStates.WAITING_CREDENTIALS)
    await message.answer(
        "PayPayのログイン情報を入力してください。\n\n"
        "形式:\n電話番号:パスワード\n\n"
        "例:\n090xxxxxxxx:password\n\n"
        "この情報はログイン処理にのみ使用し、保存しません。"
    )


@router.message(LoginStates.WAITING_CREDENTIALS, F.text)
async def on_credentials(message: Message, services: Container, state: FSMContext) -> None:
    if not services.is_admin(message.from_user.id) or not _is_private(message):
        return

    raw = message.text or ""
    # Delete the credential message ASAP.
    await _delete_silently(message)

    if ":" not in raw:
        await message.answer("形式が正しくありません。 電話番号:パスワード の形式で送信してください。")
        return
    phone, password = raw.split(":", 1)
    phone, password = phone.strip(), password.strip()
    if not phone or not password:
        await message.answer("電話番号とパスワードを正しく入力してください。")
        return

    try:
        result = await services.paypay.begin_login(phone, password)
    except PayPayError:
        # Never surface raw PayPay error (may contain credentials).
        await state.clear()
        await message.answer("PayPayログインに失敗しました。")
        return
    finally:
        # Drop references immediately.
        phone = password = raw = ""  # noqa: F841

    if result.status == LoginStatus.SUCCESS:
        await state.clear()
        await message.answer("PayPayログイン成功")
    elif result.status == LoginStatus.OTP_REQUIRED:
        await state.set_state(LoginStates.WAITING_OTP)
        await message.answer(
            "SMS認証コードを送信しました。\n"
            "PayPayから届いた認証コード（またはワンタイムリンク）を入力してください。"
        )
    else:
        await state.clear()
        await message.answer("PayPayログインに失敗しました。")


@router.message(LoginStates.WAITING_OTP, F.text)
async def on_otp(message: Message, services: Container, state: FSMContext) -> None:
    if not services.is_admin(message.from_user.id) or not _is_private(message):
        return

    otp = (message.text or "").strip()
    await _delete_silently(message)

    try:
        result = await services.paypay.submit_otp(otp)
    except PayPayOTPRequired as exc:
        # Wrong code, or the final step is unavailable (see TODO_PAYPAY.md).
        await message.answer(str(exc) if str(exc) else "認証コードが正しくありません")
        return
    except PayPayError:
        await state.clear()
        await message.answer("PayPayログインに失敗しました。")
        return
    finally:
        otp = ""  # noqa: F841

    if result.status == LoginStatus.SUCCESS:
        await state.clear()
        await message.answer("PayPayログイン成功")
    else:
        await message.answer("認証コードが正しくありません")


@router.message(Command("login_token"))
async def cmd_login_token(
    message: Message, services: Container, state: FSMContext
) -> None:
    """Log in by directly supplying an access token (login-skip path).

    Format (private chat, admin only):
        /login_token <access_token>[|<refresh_token>][|<device_uuid>]

    Use this until the anti-bot login flow is implemented (see TODO_PAYPAY.md).
    The message is deleted immediately so the token never lingers in the chat.
    """
    if not services.is_admin(message.from_user.id):
        return
    if not _is_private(message):
        await message.answer("/login_token は管理者との個人チャットでのみ実行できます。")
        return

    raw = message.text or ""
    await _delete_silently(message)  # token must not linger in chat history

    parts = raw.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "形式: /login_token <access_token>[|<refresh_token>][|<device_uuid>]"
        )
        return
    fields = [f.strip() for f in parts[1].split("|")]
    access_token = fields[0]
    refresh_token = fields[1] if len(fields) > 1 and fields[1] else None
    device_uuid = fields[2] if len(fields) > 2 and fields[2] else None

    try:
        await services.paypay.adopt_token(
            access_token, refresh_token=refresh_token, device_uuid=device_uuid
        )
    except Exception:  # noqa: BLE001 - never echo token in the error
        await state.clear()
        await message.answer("トークンの設定に失敗しました。")
        return
    finally:
        raw = access_token = ""  # noqa: F841 - drop references

    await state.clear()
    ready = await services.provider.is_ready()
    await message.answer(
        "アクセストークンを設定しました（PayPayログイン成功）。\n"
        f"PaymentProvider: {'利用可能' if ready else '利用不可(要 PAYMENT_PROVIDER=paypay)'}"
    )


@router.message(Command("logout"))
async def cmd_logout(message: Message, services: Container, state: FSMContext) -> None:
    if not services.is_admin(message.from_user.id):
        return
    await services.paypay.logout()
    await state.clear()
    await message.answer("PayPayからログアウトしました")


@router.message(Command("paypay_status"))
async def cmd_paypay_status(message: Message, services: Container) -> None:
    if not services.is_admin(message.from_user.id):
        return
    ready = await services.provider.is_ready()
    view = await services.paypay.status(ready)
    await message.answer(_fmt_status(view, ready))
