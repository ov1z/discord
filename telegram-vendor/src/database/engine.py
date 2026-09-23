"""Async engine / session factory and schema init.

Kept backend-neutral so PostgreSQL is a drop-in via DATABASE_URL.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import Settings, get_settings
from database.models import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _build_engine(settings: Settings) -> AsyncEngine:
    kwargs: dict = {"echo": False, "future": True}
    if settings.is_sqlite:
        # Allow use across the single-threaded asyncio loop.
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_async_engine(settings.database_url, **kwargs)


def init_engine(settings: Settings | None = None) -> async_sessionmaker[AsyncSession]:
    """Create (once) the engine + session factory. Idempotent."""
    global _engine, _sessionmaker
    if _sessionmaker is None:
        settings = settings or get_settings()
        _engine = _build_engine(settings)
        _sessionmaker = async_sessionmaker(
            _engine, expire_on_commit=False, class_=AsyncSession
        )
    return _sessionmaker


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        return init_engine()
    return _sessionmaker


async def create_all() -> None:
    """Create tables if they do not exist, and apply tiny additive migrations.

    No Alembic yet; we only need to add new nullable columns to existing DBs.
    """
    if _engine is None:
        init_engine()
    assert _engine is not None
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_new_columns)


def _ensure_new_columns(sync_conn) -> None:
    """Add columns introduced after the initial schema (idempotent)."""
    from sqlalchemy import inspect, text

    inspector = inspect(sync_conn)
    # products.notes
    try:
        cols = {c["name"] for c in inspector.get_columns("products")}
    except Exception:  # table missing -> create_all handled it
        cols = set()
    if cols and "notes" not in cols:
        sync_conn.execute(text("ALTER TABLE products ADD COLUMN notes TEXT"))


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
