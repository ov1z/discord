"""Handler routers, aggregated for registration in main.py."""
from aiogram import Router

from bot.handlers import (
    admin,
    admin_panel,
    broadcast,
    login,
    products,
    purchase,
    start,
)


def build_root_router() -> Router:
    root = Router(name="root")
    # Order matters: admin panel + login + broadcast (FSM) first, then slash
    # commands, then the shop; catch-alls (purchase link input) last.
    root.include_router(admin_panel.router)
    root.include_router(login.router)
    root.include_router(broadcast.router)
    root.include_router(admin.router)
    root.include_router(start.router)
    root.include_router(products.router)
    root.include_router(purchase.router)
    return root


__all__ = ["build_root_router"]
