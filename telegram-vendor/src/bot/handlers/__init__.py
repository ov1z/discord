"""Handler routers, aggregated for registration in main.py."""
from aiogram import Router

from bot.handlers import admin, login, products, purchase, start


def build_root_router() -> Router:
    root = Router(name="root")
    # Order matters: specific/admin/login routers first, catch-alls last.
    root.include_router(start.router)
    root.include_router(login.router)
    root.include_router(admin.router)
    root.include_router(products.router)
    root.include_router(purchase.router)
    return root


__all__ = ["build_root_router"]
