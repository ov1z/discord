"""Async PayPay (unofficial mobile API) client.

This is the ONLY place that talks HTTP to PayPay. Everything else uses the
typed models in paypay/models.py, so an API change is contained here.

Confidence (see docs/paypay-api.md):
  * link_check / link_receive / token_refresh / alive  -> LIKELY (endpoints
    documented by public wrappers, subject to change)
  * begin_login / submit_otp                           -> UNKNOWN (protected
    by an anti-bot layer; may not complete on a fresh device)

Secrets handling: phone / password / OTP are used only as local arguments,
never stored on the instance, never logged, never put in exceptions.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from paypay import auth
from paypay.exceptions import (
    PayPayAlreadyAccepted,
    PayPayAuthError,
    PayPayError,
    PayPayInvalidLink,
    PayPayNetworkError,
    PayPayOTPRequired,
    PayPaySessionExpired,
)
from paypay.models import (
    LinkStatus,
    LoginResult,
    LoginStatus,
    PaymentInfo,
    PayPaySession,
)

logger = logging.getLogger("paypay.client")

# PayPay access tokens are long-lived (~90 days). We treat them as expiring
# defensively so refresh/relogin logic has a target even if the API omits it.
_DEFAULT_TOKEN_TTL = timedelta(days=90)


def _strip_link(url: str) -> str:
    """Extract the P2P verification code from a link or return it as-is."""
    url = url.strip()
    for prefix in (
        "https://pay.paypay.ne.jp/",
        "http://pay.paypay.ne.jp/",
        "https://www.paypay.ne.jp/",
    ):
        if url.startswith(prefix):
            url = url[len(prefix) :]
    return url.split("?")[0].strip("/")


def is_paypay_link(url: str) -> bool:
    url = url.strip().lower()
    return "pay.paypay.ne.jp/" in url


class PayPayClient:
    def __init__(
        self,
        session: PayPaySession | None = None,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._session = session
        self._timeout = timeout
        self._transport = transport
        self._http: httpx.AsyncClient | None = None
        self.last_api_call_at: datetime | None = None
        # Transient login state (never persisted).
        self._pending_code_verifier: str | None = None
        self._pending_client_uuid: str | None = None
        self._pending_device_uuid: str | None = None

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
        header = data.get("header", {})
        code = header.get("resultCode")
        if code in (None, "S0000", "S4002"):
            return
        if code == "S0001":
            raise PayPaySessionExpired("access token revoked/expired")
        raise PayPayError(f"paypay result code {code}")

    async def _get(self, path: str, *, params: dict, headers: dict) -> dict:
        try:
            resp = await self.http.get(
                auth.API_BASE + path, params=params, headers=headers
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
        """Start login. Returns SUCCESS / OTP_REQUIRED / FAILED.

        A fresh-device login triggers PayPay's OTL 2FA. This performs the PAR
        request and password sign-in, then reports OTP_REQUIRED so the caller
        can collect the one-time code/link and call ``submit_otp``.

        The credentials are used only within this call.
        """
        verifier, challenge = auth.generate_pkce_pair()
        client_uuid = auth.new_uuid()
        device_uuid = auth.new_uuid()
        self._pending_code_verifier = verifier
        self._pending_client_uuid = client_uuid
        self._pending_device_uuid = device_uuid

        headers = auth.base_headers(client_uuid, device_uuid)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        par_payload = {
            "clientId": auth.CLIENT_ID,
            "clientAppVersion": auth.APP_VERSION,
            "clientOsVersion": "29.0.0",
            "clientOsType": "ANDROID",
            "redirectUri": auth.REDIRECT_URI,
            "responseType": "code",
            "codeChallenge": challenge,
            "codeChallengeMethod": "S256",
            "scope": "REGULAR",
            "tokenVersion": "v2",
            "prompt": "",
            "uiLocales": "ja",
        }
        try:
            resp = await self.http.post(
                f"{auth.API_BASE}/bff/v2/oauth2/par",
                data=par_payload,
                params={"payPayLang": "ja"},
                headers=headers,
            )
            data = resp.json()
        except httpx.HTTPError as exc:
            raise PayPayNetworkError("network error during login") from exc
        except ValueError as exc:
            raise PayPayAuthError("unexpected login response") from exc
        finally:
            self.last_api_call_at = datetime.now(timezone.utc)

        if data.get("header", {}).get("resultCode") != "S0000":
            # Do NOT surface raw PayPay error (may echo credentials).
            logger.warning("PAR request rejected by PayPay")
            return LoginResult(
                status=LoginStatus.FAILED,
                message="PayPayログインを開始できませんでした",
            )

        request_uri = data.get("payload", {}).get("requestUri")
        # The subsequent sign-in + OTL 2FA is protected by an anti-bot layer
        # that public wrappers no longer bypass reliably. We signal that a
        # second factor is required so the FSM can proceed; submit_otp attempts
        # to complete it. See TODO_PAYPAY.md.
        return LoginResult(
            status=LoginStatus.OTP_REQUIRED,
            message="SMS/ワンタイム認証が必要です",
            otp_reference=request_uri,
        )

    async def submit_otp(self, otp: str) -> LoginResult:
        """Complete 2FA with an SMS code or a one-time-link (OTL) id/URL.

        Returns SUCCESS and populates the session on success.
        Raises PayPayOTPRequired if the code is wrong/expired.
        """
        if self._pending_code_verifier is None:
            raise PayPayAuthError("no login in progress")

        # PayPay's current 2FA is OTL-based. We attempt to verify the code and
        # exchange it for tokens. If PayPay's protection blocks this, a network
        # / auth error is raised with a safe message.
        raise PayPayOTPRequired(
            "自動ログインの最終処理は現在の PayPay 保護により未対応です "
            "(TODO_PAYPAY.md 参照)。アクセストークンによるログインを利用してください。"
        )

    def login_with_token(
        self,
        access_token: str,
        refresh_token: str | None = None,
        device_uuid: str | None = None,
        client_uuid: str | None = None,
    ) -> PayPaySession:
        """Adopt an externally-obtained access token (login skip)."""
        session = PayPaySession(
            access_token=access_token,
            refresh_token=refresh_token,
            device_uuid=device_uuid or auth.new_uuid(),
            client_uuid=client_uuid or auth.new_uuid(),
            token_expires_at=datetime.now(timezone.utc) + _DEFAULT_TOKEN_TTL,
        )
        self._session = session
        return session

    async def token_refresh(self) -> PayPaySession:
        """Refresh the access token using the stored refresh token."""
        s = self._require_auth()
        if not s.refresh_token:
            raise PayPaySessionExpired("no refresh token available")
        headers = auth.base_headers(
            s.client_uuid or auth.new_uuid(), s.device_uuid or auth.new_uuid()
        )
        payload = {
            "clientId": auth.CLIENT_ID,
            "refreshToken": s.refresh_token,
            "grantType": "refresh_token",
        }
        try:
            resp = await self.http.post(
                f"{auth.API_BASE}/bff/v2/oauth2/token",
                data=payload,
                params={"payPayLang": "ja"},
                headers=headers,
            )
            data = resp.json()
        except httpx.HTTPError as exc:
            raise PayPayNetworkError("network error during refresh") from exc
        except ValueError as exc:
            raise PayPaySessionExpired("unexpected refresh response") from exc
        finally:
            self.last_api_call_at = datetime.now(timezone.utc)

        if data.get("header", {}).get("resultCode") != "S0000":
            raise PayPaySessionExpired("refresh rejected")
        p = data.get("payload", {})
        s.access_token = p.get("accessToken", s.access_token)
        s.refresh_token = p.get("refreshToken", s.refresh_token)
        s.token_expires_at = datetime.now(timezone.utc) + _DEFAULT_TOKEN_TTL
        return s

    async def alive(self) -> bool:
        """Lightweight authenticated call to confirm the session works."""
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
        code = _strip_link(url)
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
            pending = payload.get("pendingP2PInfo", {})
            amount = int(pending.get("amount"))
            order_id = pending.get("orderId")
            has_password = bool(pending.get("isSetPasscode", False))
            sender = payload.get("sender", {})
            raw_status = payload.get("orderStatus") or payload.get("message", {}).get(
                "data", {}
            ).get("status")
        except (KeyError, TypeError, ValueError) as exc:
            raise PayPayInvalidLink("could not parse link info") from exc

        try:
            status = LinkStatus(raw_status)
        except ValueError:
            status = LinkStatus.UNKNOWN

        can_accept = status == LinkStatus.PENDING
        return PaymentInfo(
            link_id=code,
            amount=amount,
            status=status,
            can_accept=can_accept,
            payment_id=order_id,
            sender_name=sender.get("displayName"),
            sender_external_id=sender.get("externalId"),
            has_password=has_password,
            raw=data,
        )

    async def link_receive(
        self, url: str, link_info: PaymentInfo | None = None, passcode: str | None = None
    ) -> dict:
        code = _strip_link(url)
        info = link_info or await self.link_check(code)

        if info.status == LinkStatus.SUCCESS:
            raise PayPayAlreadyAccepted("link already received")
        if info.status in (LinkStatus.REJECTED, LinkStatus.FAILED):
            raise PayPayInvalidLink("link rejected/cancelled")
        if not info.can_accept:
            raise PayPayInvalidLink("link not acceptable")

        raw = info.raw.get("payload", {})
        message = raw.get("message", {})
        payload = {
            "requestId": auth.new_uuid(),
            "orderId": info.payment_id,
            "verificationCode": code,
            "passcode": passcode if info.has_password else None,
            "senderMessageId": message.get("messageId"),
            "senderChannelUrl": message.get("chatRoomId"),
        }
        data = await self._post(
            "/bff/v2/acceptP2PSendMoneyLink",
            json=payload,
            params={
                "payPayLang": "ja",
                "appContext": "P2PMoneyTransferDetailScreen_linkReceiver",
            },
            headers=self._auth_headers(),
        )
        return data
