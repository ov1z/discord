"""Admin-facing PayPay session management.

Owns the PayPayClient + session lifecycle, and produces only SAFE status
views (never the token itself). Credentials passed to login are used once and
never retained here.
"""
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from paypay.client import PayPayClient
from paypay.exceptions import PayPayError, PayPaySessionExpired
from paypay.models import LoginResult, LoginStatus, PayPaySession
from paypay.session_store import PayPaySessionStore

logger = logging.getLogger("services.paypay")


class AuthState(str, enum.Enum):
    AUTHENTICATED = "AUTHENTICATED"
    UNAUTHENTICATED = "UNAUTHENTICATED"


@dataclass(slots=True)
class PayPayStatusView:
    state: AuthState
    account_id: str | None
    token_expires_at: datetime | None
    provider_ready: bool
    last_api_call_at: datetime | None
    has_saved_session: bool


class PayPayService:
    def __init__(
        self, client: PayPayClient, store: PayPaySessionStore
    ) -> None:
        self._client = client
        self._store = store

    @property
    def client(self) -> PayPayClient:
        return self._client

    def is_authenticated(self) -> bool:
        return self._client.is_authenticated()

    # ---------------------------------------------------------------- startup
    async def restore_session(self) -> AuthState:
        """Load a saved session at boot; refresh if expired; verify liveness."""
        session = await self._store.load()
        if session is None:
            return AuthState.UNAUTHENTICATED
        self._client.set_session(session)

        # Refresh if we believe the token is expired and a refresh token exists.
        exp = session.token_expires_at
        if exp and exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp and datetime.now(timezone.utc) >= exp:
            try:
                refreshed = await self._client.token_refresh()
                await self._store.save(refreshed)
            except PayPaySessionExpired:
                self._client.set_session(None)
                return AuthState.UNAUTHENTICATED
            except PayPayError:
                logger.warning("token refresh failed at startup")
                self._client.set_session(None)
                return AuthState.UNAUTHENTICATED
        return AuthState.AUTHENTICATED

    # ----------------------------------------------------------------- login
    async def begin_login(self, phone: str, password: str) -> LoginResult:
        """Start a login. Credentials are used only for this call."""
        result = await self._client.begin_login(phone, password)
        # Never log phone/password/result raw.
        return result

    async def submit_otp(self, otp: str) -> LoginResult:
        result = await self._client.submit_otp(otp)
        if result.status == LoginStatus.SUCCESS:
            session = self._client.get_session()
            if session is not None:
                await self._store.save(session)
        return result

    async def adopt_token(
        self,
        access_token: str,
        refresh_token: str | None = None,
        device_uuid: str | None = None,
    ) -> PayPaySession:
        """Login by directly supplying an access token (login skip path)."""
        session = self._client.login_with_token(
            access_token, refresh_token=refresh_token, device_uuid=device_uuid
        )
        await self._store.save(session)
        return session

    # ---------------------------------------------------------------- logout
    async def logout(self) -> None:
        self._client.set_session(None)
        await self._store.clear()

    # ---------------------------------------------------------------- status
    async def status(self, provider_ready: bool) -> PayPayStatusView:
        session = self._client.get_session()
        return PayPayStatusView(
            state=AuthState.AUTHENTICATED
            if self.is_authenticated()
            else AuthState.UNAUTHENTICATED,
            account_id=session.account_id if session else None,
            token_expires_at=session.token_expires_at if session else None,
            provider_ready=provider_ready,
            last_api_call_at=self._client.last_api_call_at,
            has_saved_session=self._store.exists(),
        )
