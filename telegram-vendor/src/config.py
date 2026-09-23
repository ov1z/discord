"""Application configuration loaded from environment / .env.

Uses pydantic-settings so switching secrets or the DB backend never needs
code changes. All secrets live in the environment, never in code or the DB.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    admin_telegram_id: int = Field(default=0, alias="ADMIN_TELEGRAM_ID")

    # Database
    database_url: str = Field(
        default="sqlite+aiosqlite:///shop.db", alias="DATABASE_URL"
    )

    # Security
    session_encryption_key: str = Field(default="", alias="SESSION_ENCRYPTION_KEY")
    paypay_session_path: str = Field(
        default="paypay_session.enc", alias="PAYPAY_SESSION_PATH"
    )

    # Payment provider: "mock" | "paypay"
    payment_provider: str = Field(default="mock", alias="PAYMENT_PROVIDER")

    # Behaviour
    order_ttl_seconds: int = Field(default=600, alias="ORDER_TTL_SECONDS")
    # 一次保留が出たとき、購入者に解除を促してから再確認するまでの秒数
    hold_recheck_seconds: int = Field(default=60, alias="HOLD_RECHECK_SECONDS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
