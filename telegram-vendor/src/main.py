"""Application entrypoint: wire everything and start long-polling.

Boot sequence:
  1. load settings, init DB (create tables)
  2. build PayPay client + session store, restore saved session
  3. choose PaymentProvider (mock | paypay)
  4. build services + DI container
  5. register routers
  6. recover undelivered (PAID/DELIVERING) orders
  7. start polling
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from bot.container import Container
from bot.handlers import build_root_router
from bot.handlers.payment import deliver_to_buyer, notify_admin
from config import get_settings
from database.engine import create_all, get_sessionmaker, init_engine, dispose_engine
from database.models import OrderStatus
from database.repository import OrderRepository
from payments.base import PaymentProvider
from payments.mock import MockPaymentProvider
from payments.paypay import PayPayPaymentProvider
from paypay.client import PayPayClient
from paypay.session_store import DeviceStore, PayPaySessionStore
from security.crypto import Cryptor
from services.inventory_service import InventoryService
from services.order_service import OrderService
from services.payment_service import PaymentService
from services.paypay_service import AuthState, PayPayService
from services.product_service import ProductService
from services.user_service import UserService


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _build_provider(
    name: str, paypay_client: PayPayClient
) -> PaymentProvider:
    if name.lower() == "paypay":
        return PayPayPaymentProvider(paypay_client)
    return MockPaymentProvider()


async def _recover_undelivered(bot: Bot, container: Container) -> None:
    """Re-attempt delivery for orders that paid but never fully delivered."""
    sm = get_sessionmaker()
    async with sm() as session:
        orders = await OrderRepository(session).list_by_status(
            [OrderStatus.PAID.value, OrderStatus.DELIVERING.value]
        )
        pending = [(o.id, o.telegram_user_id, o.order_code) for o in orders]
    for order_id, buyer_id, code in pending:
        try:
            result = await container.payments.deliver_order(order_id)
            if result.delivered_content:
                delivered = await deliver_to_buyer(
                    bot, container, order_id, buyer_id, result
                )
                if not delivered:
                    await notify_admin(
                        bot, container, f"⚠️ 起動時再配布に失敗: 注文 {code}"
                    )
            else:
                await notify_admin(
                    bot, container, f"⚠️ 未配布注文あり: {code} ({result.outcome.value})"
                )
        except Exception:
            logging.getLogger("main").exception("recovery failed for %s", code)

    async with sm() as session:
        unknown = await OrderRepository(session).list_by_status(
            [OrderStatus.PAYMENT_UNKNOWN.value]
        )
        unknown_list = [(o.id, o.telegram_user_id, o.order_code) for o in unknown]
    for order_id, buyer_id, code in unknown_list:
        try:
            result = await container.payments.reverify_and_settle(order_id)
            if result.delivered_content:
                await deliver_to_buyer(bot, container, order_id, buyer_id, result)
            else:
                await notify_admin(
                    bot, container,
                    f"⚠️ 未確定の注文あり: {code}（{result.outcome.value}）。"
                    f"確認後 /verify_order {code}",
                )
        except Exception:
            logging.getLogger("main").exception("reverify failed for %s", code)


async def main() -> None:
    settings = get_settings()
    _configure_logging(settings.log_level)
    log = logging.getLogger("main")

    if not settings.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set (see .env.example)")

    init_engine(settings)
    await create_all()

    cryptor = Cryptor(settings.session_encryption_key)
    store = PayPaySessionStore(settings.paypay_session_path, cryptor)
    devices = DeviceStore(
        str(Path(settings.paypay_session_path).with_name("paypay_devices.enc")),
        cryptor,
    )
    paypay_client = PayPayClient()
    paypay_service = PayPayService(paypay_client, store, devices)
    state = await paypay_service.restore_session()
    log.info("PayPay auth state at boot: %s", state.value)

    provider = _build_provider(settings.payment_provider, paypay_client)

    sm = get_sessionmaker()
    container = Container(
        settings=settings,
        products=ProductService(sm),
        inventory=InventoryService(sm),
        orders=OrderService(sm, ttl_seconds=settings.order_ttl_seconds),
        payments=PaymentService(sm, provider),
        paypay=paypay_service,
        provider=provider,
        users=UserService(sm),
    )

    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher(storage=MemoryStorage())
    dp["services"] = container
    dp.include_router(build_root_router())

    await _recover_undelivered(bot, container)
    if settings.payment_provider.lower() == "paypay" and state != AuthState.AUTHENTICATED:
        await notify_admin(
            bot,
            container,
            "PayPay未ログインです。/login を実行してください。",
        )

    log.info("Bot starting (provider=%s)", provider.name)
    try:
        await dp.start_polling(bot)
    finally:
        await provider.close()
        await paypay_client.close()
        await bot.session.close()
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
