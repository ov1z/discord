"""Dependency container shared with all handlers via aiogram workflow data.

Handlers receive it by declaring a ``services: Container`` parameter.
"""
from __future__ import annotations

from dataclasses import dataclass

from config import Settings
from payments.base import PaymentProvider
from services.inventory_service import InventoryService
from services.order_service import OrderService
from services.payment_service import PaymentService
from services.paypay_service import PayPayService
from services.product_service import ProductService


@dataclass
class Container:
    settings: Settings
    products: ProductService
    inventory: InventoryService
    orders: OrderService
    payments: PaymentService
    paypay: PayPayService
    provider: PaymentProvider

    def is_admin(self, telegram_user_id: int) -> bool:
        return telegram_user_id == self.settings.admin_telegram_id
