"""Handler routers, aggregated for registration in main.py."""
from aiogram import Router

from bot.handlers import (
    admin,
    admin_panel,
    broadcast,
    fallback,
    login,
    products,
    purchase,
    start,
)


def build_root_router() -> Router:
    root = Router(name="root")
    root.include_router(admin_panel.router)
    root.include_router(login.router)
    root.include_router(broadcast.router)
    root.include_router(admin.router)
    root.include_router(start.router)
    root.include_router(products.router)
    root.include_router(purchase.router)
    root.include_router(fallback.router)
    return root


__all__ = ["build_root_router"]
