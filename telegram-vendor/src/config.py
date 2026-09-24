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

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    admin_telegram_id: int = Field(default=0, alias="ADMIN_TELEGRAM_ID")

    database_url: str = Field(
        default="sqlite+aiosqlite:///shop.db", alias="DATABASE_URL"
    )

    session_encryption_key: str = Field(default="", alias="SESSION_ENCRYPTION_KEY")
    paypay_session_path: str = Field(
        default="paypay_session.enc", alias="PAYPAY_SESSION_PATH"
    )

    payment_provider: str = Field(default="mock", alias="PAYMENT_PROVIDER")

    # PayPay通信だけを通すProxy（例: http://user:pass@host:port）。空なら直接接続。
    paypay_proxy: str = Field(default="", alias="PAYPAY_PROXY")

    support_contact: str = Field(default="@anonxdev", alias="SUPPORT_CONTACT")
    # Optional links shown as buttons. Empty = button hidden.
    sales_channel: str = Field(default="", alias="SALES_CHANNEL")
    terms_url: str = Field(default="", alias="TERMS_URL")

    order_ttl_seconds: int = Field(default=600, alias="ORDER_TTL_SECONDS")
    hold_recheck_seconds: int = Field(default=60, alias="HOLD_RECHECK_SECONDS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def support_url(self) -> str | None:
        """t.me link for the support button, or None if not configured."""
        handle = self.support_contact.strip().lstrip("@")
        if not handle:
            return None
        if handle.startswith("http://") or handle.startswith("https://"):
            return handle
        return f"https://t.me/{handle}"

    @staticmethod
    def _as_url(value: str, tme_prefix: bool) -> str | None:
        v = value.strip()
        if not v:
            return None
        if v.startswith("http://") or v.startswith("https://"):
            return v
        if tme_prefix:
            return f"https://t.me/{v.lstrip('@')}"
        return v

    @property
    def sales_channel_url(self) -> str | None:
        return self._as_url(self.sales_channel, tme_prefix=True)

    @property
    def terms_link(self) -> str | None:
        return self._as_url(self.terms_url, tme_prefix=False)

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
