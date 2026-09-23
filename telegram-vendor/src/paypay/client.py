"""Async PayPay (unofficial mobile API) client.

This is the ONLY place that talks HTTP to PayPay. Everything else uses the
typed models in paypay/models.py, so an API change is contained here.

Login (begin_login/submit_otp) delegates to paypay/auth.py, which bypasses the
AWS WAF with a one-shot headless Chromium page load and then drives the OAuth2
PAR + OTL(one-time-link) 2FA flow over httpx. Those steps are synchronous and
run in a worker thread; the authenticated link/refresh calls below are async.

Secrets: phone / password / OTP are only local arguments; never stored on the
instance, never logged, never placed in exceptions.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx

from paypay import auth
from paypay.auth import LoginFailed, extract_verification_code, is_paypay_link  # re-export
from paypay.exceptions import (
    PayPayAlreadyAccepted,
    PayPayAuthError,
    PayPayError,
    PayPayInvalidLink,
    PayPayNetworkError,
    PayPayOTPRequired,
    PayPaySessionExpired,
    PayPayTemporaryHold,
)
from paypay.models import (
    LinkStatus,
    LoginResult,
    LoginStatus,
    PaymentInfo,
    PayPaySession,
)

logger = logging.getLogger("paypay.client")

# PayPay access tokens are long-lived (~90 days). Treated defensively.
_DEFAULT_TOKEN_TTL = timedelta(days=90)

__all__ = ["PayPayClient", "extract_verification_code", "is_paypay_link"]


class PayPayClient:
    def __init__(
        self,
        session: PayPaySession | None = None,
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._session = session
        self._timeout = timeout
        self._transport = transport
        self._http: httpx.AsyncClient | None = None
        self.last_api_call_at: datetime | None = None
        # Transient login context (cookies/cv/uuids); never persisted.
        self._login_ctx: dict | None = None

    # ----------------------------------------------------------------- infra
    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            )
        return self._http

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def set_session(self, session: PayPaySession | None) -> None:
        self._session = session

    def get_session(self) -> PayPaySession | None:
        return self._session

    def is_authenticated(self) -> bool:
        return self._session is not None and bool(self._session.access_token)

    def _require_auth(self) -> PayPaySession:
        if not self.is_authenticated():
            raise PayPayAuthError("not authenticated")
        assert self._session is not None
        return self._session

    def _auth_headers(self) -> dict[str, str]:
        s = self._require_auth()
        return auth.auth_headers(
            s.access_token,
            s.client_uuid or auth.new_uuid(),
            s.device_uuid or auth.new_uuid(),
        )

    @staticmethod
    def _check_header(data: dict) -> None:
        code = data.get("header", {}).get("resultCode")
        if code in (None, "S0000", "S4002"):
            return
        if code == "S0001":
            raise PayPaySessionExpired("access token revoked/expired")
        # Temporary hold / P2P receiving limited (money not finally settled).
        backend = (data.get("error", {}) or {}).get("backendResultCode")
        if backend == "42007013":
            raise PayPayTemporaryHold("P2P receipt temporarily held")
        raise PayPayError(f"paypay result code {code}")

    async def _get(self, path: str, *, params: dict, headers: dict) -> dict:
        try:
            resp = await self.http.get(auth.API_BASE + path, params=params, headers=headers)
            data = resp.json()
        except httpx.HTTPError as exc:
            raise PayPayNetworkError(f"network error: {type(exc).__name__}") from exc
        except ValueError as exc:
            raise PayPayNetworkError("invalid JSON from PayPay") from exc
        finally:
            self.last_api_call_at = datetime.now(timezone.utc)
        self._check_header(data)
        return data

    async def _post(self, path: str, *, json: dict, params: dict, headers: dict) -> dict:
        try:
            resp = await self.http.post(
                auth.API_BASE + path, json=json, params=params, headers=headers
            )
            data = resp.json()
        except httpx.HTTPError as exc:
            raise PayPayNetworkError(f"network error: {type(exc).__name__}") from exc
        except ValueError as exc:
            raise PayPayNetworkError("invalid JSON from PayPay") from exc
        finally:
            self.last_api_call_at = datetime.now(timezone.utc)
        self._check_header(data)
        return data

    # ----------------------------------------------------------------- login
    async def begin_login(self, phone: str, password: str) -> LoginResult:
        """Start login. Returns SUCCESS (rare, known device) or OTP_REQUIRED.

        Credentials are used only within this call and the worker thread.
        """
        device_uuid = (self._session.device_uuid if self._session else None) or auth.new_uuid()
        client_uuid = (self._session.client_uuid if self._session else None) or auth.new_uuid()
        try:
            result = await asyncio.to_thread(
                auth.login_step1, phone, password, device_uuid, client_uuid
            )
        except LoginFailed as exc:
            return LoginResult(status=LoginStatus.FAILED, message=str(exc))
        except Exception as exc:  # noqa: BLE001 - never leak internals/secrets
            logger.warning("login_step1 error: %s", type(exc).__name__)
            return LoginResult(status=LoginStatus.FAILED, message="PayPayログインに失敗しました")

        if result["status"] == "SUCCESS":
            self._adopt(result["access_token"], result.get("refresh_token"),
                        device_uuid, client_uuid)
            self._login_ctx = None
            return LoginResult(status=LoginStatus.SUCCESS, message="PayPayログイン成功")

        self._login_ctx = result["ctx"]
        return LoginResult(status=LoginStatus.OTP_REQUIRED, message="SMS/OTL認証が必要です")

    async def submit_otp(self, otp: str) -> LoginResult:
        """Complete OTL/SMS 2FA and populate the session."""
        if self._login_ctx is None:
            raise PayPayAuthError("no login in progress")
        ctx = self._login_ctx
        try:
            tokens = await asyncio.to_thread(auth.login_step2, ctx, otp)
        except LoginFailed as exc:
            raise PayPayOTPRequired(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            logger.warning("login_step2 error: %s", type(exc).__name__)
            raise PayPayError("PayPayログインに失敗しました") from exc

        self._adopt(tokens["access_token"], tokens.get("refresh_token"),
                    ctx["device_uuid"], ctx["client_uuid"])
        self._login_ctx = None
        return LoginResult(status=LoginStatus.SUCCESS, message="PayPayログイン成功")

    def _adopt(
        self, access_token: str, refresh_token: str | None,
        device_uuid: str, client_uuid: str,
    ) -> PayPaySession:
        session = PayPaySession(
            access_token=access_token,
            refresh_token=refresh_token,
            device_uuid=device_uuid,
            client_uuid=client_uuid,
            token_expires_at=datetime.now(timezone.utc) + _DEFAULT_TOKEN_TTL,
        )
        self._session = session
        return session

    def login_with_token(
        self,
        access_token: str,
        refresh_token: str | None = None,
        device_uuid: str | None = None,
        client_uuid: str | None = None,
    ) -> PayPaySession:
        """Adopt an externally-obtained access token (login skip)."""
        return self._adopt(
            access_token, refresh_token,
            device_uuid or auth.new_uuid(), client_uuid or auth.new_uuid(),
        )

    async def token_refresh(self) -> PayPaySession:
        s = self._require_auth()
        if not s.refresh_token:
            raise PayPaySessionExpired("no refresh token available")
        try:
            access_token, refresh_token = await asyncio.to_thread(
                auth.refresh_access_token,
                s.refresh_token,
                s.device_uuid or auth.new_uuid(),
                s.client_uuid or auth.new_uuid(),
            )
        except LoginFailed as exc:
            raise PayPaySessionExpired(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise PayPayNetworkError("network error during refresh") from exc
        s.access_token = access_token
        s.refresh_token = refresh_token or s.refresh_token
        s.token_expires_at = datetime.now(timezone.utc) + _DEFAULT_TOKEN_TTL
        return s

    async def alive(self) -> bool:
        try:
            await self._get(
                "/bff/v1/getGlobalServiceStatus",
                params={"payPayLang": "ja"},
                headers=self._auth_headers(),
            )
            return True
        except PayPaySessionExpired:
            return False

    # ------------------------------------------------------------------ links
    async def link_check(self, url: str) -> PaymentInfo:
        code = extract_verification_code(url)
        if not code:
            raise PayPayInvalidLink("empty link")
        data = await self._get(
            "/bff/v2/getP2PLinkInfo",
            params={"verificationCode": code, "payPayLang": "ja"},
            headers=self._auth_headers(),
        )
        return self._parse_link_info(code, data)

    @staticmethod
    def _parse_link_info(code: str, data: dict) -> PaymentInfo:
        try:
            payload = data["payload"]
            pending = payload.get("pendingP2PInfo", {}) or {}
            message = payload.get("message", {}) or {}
            msg_data = message.get("data", {}) or {}
            amount = int(pending.get("amount"))
            raw_status = payload.get("orderStatus") or msg_data.get("status")
        except (KeyError, TypeError, ValueError) as exc:
            raise PayPayInvalidLink("could not parse link info") from exc

        try:
            status = LinkStatus(raw_status)
        except ValueError:
            status = LinkStatus.UNKNOWN

        return PaymentInfo(
            link_id=code,
            amount=amount,
            status=status,
            can_accept=status == LinkStatus.PENDING,
            payment_id=msg_data.get("orderId") or pending.get("orderId"),
            sender_name=pending.get("senderName") or payload.get("sender", {}).get("displayName"),
            sender_external_id=payload.get("sender", {}).get("externalId"),
            has_password=bool(pending.get("isSetPasscode", False)),
            chat_room_id=message.get("chatRoomId"),
            message_id=message.get("messageId"),
            request_id=msg_data.get("requestId"),
            raw=data,
        )

    async def link_receive(
        self, url: str, link_info: PaymentInfo | None = None, passcode: str | None = None
    ) -> dict:
        code = extract_verification_code(url)
        info = link_info or await self.link_check(code)

        if info.status == LinkStatus.SUCCESS:
            raise PayPayAlreadyAccepted("link already received")
        if info.status in (LinkStatus.REJECTED, LinkStatus.FAILED):
            raise PayPayInvalidLink("link rejected/cancelled")
        if not info.can_accept:
            raise PayPayInvalidLink("link not acceptable")

        payload = {
            "requestId": info.request_id or auth.new_uuid(),
            "orderId": info.payment_id,
            "verificationCode": code,
            "senderMessageId": info.message_id,
            "senderChannelUrl": info.chat_room_id,
        }
        if info.has_password and passcode:
            payload["passcode"] = passcode
        return await self._post(
            "/bff/v2/acceptP2PSendMoneyLink",
            json=payload,
            params={"payPayLang": "ja",
                    "appContext": "P2PMoneyTransferDetailScreen_linkReceiver"},
            headers=self._auth_headers(),
        )
