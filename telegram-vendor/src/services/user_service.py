"""User directory operations (registration list, broadcast targets)."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from database.repository import UserRepository


class UserService:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    async def all_ids(self) -> list[int]:
        async with self._sm() as session:
            return await UserRepository(session).all_telegram_ids()

    async def count(self) -> int:
        async with self._sm() as session:
            return await UserRepository(session).count()
