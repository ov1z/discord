"""Login/header mechanics for the PayPay mobile API.

Isolated from client.py so the (volatile) authentication mechanics — the AWS
WAF token, PKCE, the OAuth2 PAR flow and the OTL (one-time-link) 2FA — can be
adjusted as PayPay changes them. See docs/paypay-api.md for confidence levels.

Anti-bot: PayPay's sign-in host is behind AWS WAF. We obtain the
``aws-waf-token`` cookie with ONE headless Chromium page load (Playwright),
then perform the whole OAuth flow with httpx. The browser is only the "gate
pass" — no browser automation is used for OTP, receiving links, or payments.

Secrets: phone / password / OTP live only as call arguments; nothing here logs
them or the resulting tokens.

These functions are synchronous (httpx.Client + sync Playwright); the async
client runs them via ``asyncio.to_thread`` since /login is an infrequent,
interactive admin action.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx

logger = logging.getLogger("paypay.auth")

APP_VERSION = "5.55.0"
CLIENT_OS_VERSION = "36.0.0"
CLIENT_OS_RELEASE = "16"
CLIENT_ID = "pay2-mobile-app-client"
REDIRECT_URI = "paypay://oauth2/callback"
API_BASE = "https://app4.paypay.ne.jp"
WEB_BASE = "https://www.paypay.ne.jp"

UA = (
    "Mozilla/5.0 (Linux; Android 16; Pixel 6a Build/CP1A.260305.018; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
    "Chrome/147.0.7727.138 Mobile Safari/537.36 jp.pay2.app.android/5.50.0"
)

# Static Android-app style headers (verified against a working 2026 client).
_HEADERS_BASE: dict[str, str] = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ja-JP,ja;q=0.9",
    "Client-Id": CLIENT_ID,
    "Client-OS-Type": "ANDROID",
    "Client-OS-Version": CLIENT_OS_VERSION,
    "Client-OS-Release-Version": CLIENT_OS_RELEASE,
    "Client-Type": "PAYPAYAPP",
    "Client-Version": APP_VERSION,
    "Client-Mode": "NORMAL",
    "Device-Name": "Pixel 6a",
    "Device-Hardware-Name": "bluejay",
    "Device-Manufacturer-Name": "Google",
    "Device-Brand-Name": "google",
    "Is-Emulator": "false",
    "Network-Status": "WIFI",
    "System-Locale": "ja",
    "Timezone": "Asia/Tokyo",
    "User-Agent": f"PaypayApp/{APP_VERSION} Android16",
    "Device-Lock-Type": "DEVICE",
    "Device-Lock-App-Setting": "false",
    "Device-In-Call": "false",
    "App-Mode": "domestic_automatic",
    "Accept-Charset": "UTF-8",
    "Accept-Encoding": "gzip",
}


class LoginFailed(Exception):
    """Internal login failure with a SAFE message (no secrets)."""


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def generate_pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def new_uuid() -> str:
    return str(uuid4())


def make_headers(device_uuid: str, client_uuid: str) -> dict[str, str]:
    h = {**_HEADERS_BASE}
    h["Device-UUID"] = device_uuid
    h["Client-UUID"] = client_uuid
    return h


def auth_headers(access_token: str, client_uuid: str, device_uuid: str) -> dict[str, str]:
    h = make_headers(device_uuid, client_uuid)
    h["Authorization"] = f"Bearer {access_token}"
    h["Content-Type"] = "application/json"
    return h


def _update_cookies(cookies: dict, resp: httpx.Response) -> None:
    for header in resp.headers.get_list("set-cookie"):
        first = header.split(";")[0]
        name, _, val = first.partition("=")
        name, val = name.strip(), val.strip()
        if not name:
            continue
        if "1970" in header or val == "":
            cookies.pop(name, None)
        else:
            cookies[name] = val


def _cookie_str(cookies: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items() if v)


def _deep_find(d, *keys):
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d:
            return d[k]
    for v in d.values():
        if isinstance(v, dict):
            r = _deep_find(v, *keys)
            if r is not None:
                return r
    return None


def _code_from_url(url: str | None) -> str | None:
    if not url:
        return None
    params = parse_qs(urlparse(url).query)
    codes = params.get("code", [])
    return codes[0] if codes else None


def extract_verification_code(value: str | None) -> str:
    """Strip any (possibly doubled) PayPay host prefix, return the raw code."""
    if not value:
        return ""
    value = str(value).strip()
    changed = True
    while changed:
        changed = False
        for host in (
            "https://qr.paypay.ne.jp/", "http://qr.paypay.ne.jp/",
            "https://pay.paypay.ne.jp/", "http://pay.paypay.ne.jp/",
            "https://www.paypay.ne.jp/", "http://www.paypay.ne.jp/",
            "qr.paypay.ne.jp/", "pay.paypay.ne.jp/", "www.paypay.ne.jp/",
        ):
            if value.lower().startswith(host.lower()):
                value = value[len(host):]
                changed = True
                break
    return value.split("?")[0].strip("/")


def is_paypay_link(url: str) -> bool:
    return "pay.paypay.ne.jp/" in url.strip().lower()


# --------------------------------------------------------------------------- #
# WAF + OAuth2 flow (synchronous)
# --------------------------------------------------------------------------- #
def get_waf_token() -> dict:
    """Obtain the AWS WAF cookie via a single headless Chromium page load."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise LoginFailed(
            "playwright 未インストール (pip install playwright && playwright install chromium)"
        ) from exc

    cookies: dict[str, str] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(user_agent=UA)
            page = ctx.new_page()
            page.goto(
                f"{WEB_BASE}/portal/oauth2/sign-in?client_id={CLIENT_ID}",
                wait_until="networkidle",
                timeout=30000,
            )
            page.wait_for_timeout(3000)
            for c in ctx.cookies():
                cookies[c["name"]] = c["value"]
        finally:
            browser.close()
    if "aws-waf-token" not in cookies:
        logger.warning("WAF token not acquired")
    cookies["OA2_last_method"] = "mobile"
    cookies["Lang"] = "ja"
    return cookies


def _client() -> httpx.Client:
    return httpx.Client(follow_redirects=False, timeout=30.0)


def _do_par(session: httpx.Client, cookies: dict, headers: dict) -> tuple[str, str]:
    cv, cc = generate_pkce_pair()
    state = _b64url(secrets.token_bytes(32))
    r = session.post(
        f"{API_BASE}/bff/v2/oauth2/par",
        params={"payPayLang": "ja"},
        data={
            "clientId": CLIENT_ID, "clientAppVersion": APP_VERSION,
            "clientOsVersion": CLIENT_OS_VERSION, "clientOsType": "ANDROID",
            "redirectUri": REDIRECT_URI, "responseType": "code",
            "state": state, "codeChallenge": cc, "codeChallengeMethod": "S256",
            "scope": "REGULAR", "tokenVersion": "v2", "prompt": "", "uiLocales": "ja",
        },
        headers=headers,
    )
    _update_cookies(cookies, r)
    request_uri = r.json().get("payload", {}).get("requestUri")
    if not request_uri:
        raise LoginFailed("PARに失敗しました")
    return cv, request_uri


def _do_authorize(session: httpx.Client, cookies: dict, request_uri: str, headers: dict) -> None:
    r = session.get(
        f"{WEB_BASE}/portal/api/v2/oauth2/authorize",
        params={"client_id": CLIENT_ID, "request_uri": request_uri},
        headers={**headers, "Cookie": _cookie_str(cookies)},
    )
    _update_cookies(cookies, r)
    if r.status_code == 302:
        loc = r.headers.get("location", "")
        if loc.startswith("/"):
            loc = f"{WEB_BASE}{loc}"
        if loc:
            r2 = session.get(loc, headers={**headers, "Cookie": _cookie_str(cookies)})
            _update_cookies(cookies, r2)
    r_check = session.get(
        f"{WEB_BASE}/portal/api/v2/oauth2/par/check",
        headers={**headers, "Cookie": _cookie_str(cookies)},
    )
    _update_cookies(cookies, r_check)


def _do_password(
    session: httpx.Client, cookies: dict, phone: str, password: str, headers: dict
) -> dict:
    """Returns {'otp_required': bool, 'code'?: str, 'otp_ref'?, 'otp_prefix'?}."""
    r = session.post(
        f"{WEB_BASE}/portal/api/v2/oauth2/sign-in/password",
        json={"username": phone, "password": password, "signInAttemptCount": 1},
        headers={
            **headers, "Content-Type": "application/json", "Origin": WEB_BASE,
            "Cookie": _cookie_str(cookies),
            "Referer": f"{WEB_BASE}/portal/oauth2/sign-in-with-password?client_id={CLIENT_ID}&mode=navigation-notitle",
        },
    )
    data = r.json()
    _update_cookies(cookies, r)
    if data.get("header", {}).get("resultCode") != "S0000":
        # Never echo PayPay's raw message (may reflect credentials).
        raise LoginFailed("電話番号またはパスワードが正しくありません")

    payload = data.get("payload", {}) or {}
    redirect_url = payload.get("redirectUrl") or _deep_find(data, "redirectUrl", "redirect_url")
    if redirect_url:
        code = _code_from_url(redirect_url)
        if code:
            return {"otp_required": False, "code": code}
    return {
        "otp_required": True,
        "otp_ref": _deep_find(data, "otpReferenceId", "otp_reference_id", "referenceId"),
        "otp_prefix": _deep_find(data, "otpPrefix", "otp_prefix", "prefix"),
    }


def _trigger_otp(session: httpx.Client, cookies: dict, headers: dict) -> None:
    base = {
        **headers, "Content-Type": "application/json", "Origin": WEB_BASE,
        "Cookie": _cookie_str(cookies), "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin",
        "X-Requested-With": "jp.ne.paypay.android.app",
    }

    def check(r: httpx.Response) -> None:
        _update_cookies(cookies, r)
        if r.json().get("header", {}).get("resultCode") != "S0000":
            raise LoginFailed("SMS認証の送信に失敗しました")

    check(session.post(
        f"{WEB_BASE}/portal/api/v2/oauth2/extension/code-grant/update",
        json={},
        headers={**base, "Referer": f"{WEB_BASE}/portal/oauth2/sign-in?client_id={CLIENT_ID}&mode=landing"},
    ))
    check(session.post(
        f"{WEB_BASE}/portal/api/v2/oauth2/extension/code-grant/update",
        json={"params": {"extension_id": "user-main-2fa-v1", "data": {"type": "SELECT_FLOW",
            "payload": {"flow": "OTL", "sign_in_method": "MOBILE",
                        "base_url": f"{WEB_BASE}/portal/oauth2/l"}}}},
        headers={**base, "Referer": f"{WEB_BASE}/portal/oauth2/verification-method?client_id={CLIENT_ID}&mode=navigation-2fa"},
    ))
    check(session.post(
        f"{WEB_BASE}/portal/api/v2/oauth2/extension/code-grant/side-channel/next-action-polling",
        json={"waitUntil": "PT5S"},
        headers={**base, "Referer": f"{WEB_BASE}/portal/oauth2/otl-request?client_id={CLIENT_ID}&mode=navigation-2fa"},
    ))


def _verify_otp_get_code(session: httpx.Client, cookies: dict, otp: str, headers: dict) -> str:
    """OTL flow: click link -> verify -> COMPLETE_OTL / polling -> auth code."""
    hdrs = {
        **headers, "Content-Type": "application/json", "Origin": WEB_BASE,
        "Cookie": _cookie_str(cookies),
        "Referer": f"{WEB_BASE}/portal/oauth2/sign-in?client_id={CLIENT_ID}",
        "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin", "X-Requested-With": "jp.ne.paypay.android.app",
    }
    otp = extract_verification_code(otp) or otp.strip()

    # Consume the one-time link.
    r_get = session.get(
        f"{WEB_BASE}/portal/oauth2/l?id={otp}",
        headers={**headers, "Cookie": _cookie_str(cookies),
                 "Referer": f"{WEB_BASE}/portal/oauth2/otl-request?client_id={CLIENT_ID}&mode=navigation-2fa"},
    )
    _update_cookies(cookies, r_get)
    if r_get.status_code in (301, 302, 303, 307, 308):
        loc = r_get.headers.get("location", "")
        if loc.startswith("/"):
            loc = f"{WEB_BASE}{loc}"
        if loc:
            _update_cookies(cookies, session.get(loc, headers={**headers, "Cookie": _cookie_str(cookies)}))

    for body in ({"code": otp}, {"otp": otp}):
        r = session.post(
            f"{WEB_BASE}/portal/api/v2/oauth2/extension/sign-in/2fa/otl/verify",
            json=body, headers=hdrs,
        )
        _update_cookies(cookies, r)
        if r.json().get("header", {}).get("resultCode") == "S0000":
            break

    for body in (
        {"params": {"extension_id": "user-main-2fa-v1", "data": {"type": "COMPLETE_OTL", "payload": {"code": otp}}}},
        {"params": {"extension_id": "user-main-2fa-v1", "data": {"type": "COMPLETE_OTL", "payload": None}}},
        {"params": {"extension_id": "user-main-2fa-v1", "data": {"type": "COMPLETE_OTL", "payload": {}}}},
    ):
        r = session.post(
            f"{WEB_BASE}/portal/api/v2/oauth2/extension/code-grant/update",
            json=body, headers=hdrs,
        )
        _update_cookies(cookies, r)
        data = r.json()
        payload = data.get("payload", {}) or {}
        code = payload.get("code") or _deep_find(data, "code")
        if code:
            return code
        redirect_uri = payload.get("redirect_uri") or payload.get("redirectUri") or ""
        code = _code_from_url(redirect_uri)
        if code:
            return code
        if data.get("header", {}).get("resultCode") == "S0000":
            break

    for _ in range(10):
        r = session.post(
            f"{WEB_BASE}/portal/api/v2/oauth2/extension/code-grant/side-channel/next-action-polling",
            json={"waitUntil": "PT5S"}, headers=hdrs,
        )
        _update_cookies(cookies, r)
        data = r.json()
        payload = data.get("payload", {}) or {}
        code = payload.get("code") or _deep_find(data, "code")
        if code:
            return code
        redirect_url = payload.get("redirectUrl") or _deep_find(data, "redirectUrl")
        code = _code_from_url(redirect_url)
        if code:
            return code

    raise LoginFailed("認証コードが正しくありません")


def _exchange_token(session: httpx.Client, cv: str, code: str, headers: dict) -> tuple[str, str | None]:
    r = session.post(
        f"{API_BASE}/bff/v2/oauth2/token",
        params={"payPayLang": "ja"},
        data={"clientId": CLIENT_ID, "grantType": "authorization_code",
              "code": code, "codeVerifier": cv, "redirectUri": REDIRECT_URI},
        headers=headers,
    )
    payload = r.json().get("payload", {}) or {}
    access_token = payload.get("accessToken")
    if not access_token:
        raise LoginFailed("トークン取得に失敗しました")
    return access_token, payload.get("refreshToken")


def refresh_access_token(
    refresh_token: str, device_uuid: str, client_uuid: str
) -> tuple[str, str | None]:
    headers = make_headers(device_uuid, client_uuid)
    with _client() as session:
        r = session.post(
            f"{API_BASE}/bff/v2/oauth2/refresh",
            params={"payPayLang": "ja"},
            data={"clientId": CLIENT_ID, "grantType": "refresh_token",
                  "refreshToken": refresh_token, "code": refresh_token,
                  "redirectUri": REDIRECT_URI},
            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
        )
        payload = r.json().get("payload", {}) or {}
        access_token = payload.get("accessToken")
        if not access_token:
            raise LoginFailed("トークン更新に失敗しました")
        return access_token, payload.get("refreshToken") or refresh_token


# --------------------------------------------------------------------------- #
# Orchestrators used by the async client (run in a worker thread)
# --------------------------------------------------------------------------- #
def login_step1(phone: str, password: str, device_uuid: str, client_uuid: str) -> dict:
    """WAF -> PAR -> authorize -> password (-> maybe token if OTP not needed).

    Returns one of:
      {"status": "SUCCESS", "access_token", "refresh_token"}
      {"status": "OTP_REQUIRED", "ctx": {...}}   (ctx carries cookies/cv/uuids)
    """
    cookies = get_waf_token()
    headers = make_headers(device_uuid, client_uuid)
    with _client() as session:
        cv, request_uri = _do_par(session, cookies, headers)
        _do_authorize(session, cookies, request_uri, headers)
        pw = _do_password(session, cookies, phone, password, headers)
        if not pw["otp_required"]:
            access_token, refresh_token = _exchange_token(session, cv, pw["code"], headers)
            return {
                "status": "SUCCESS",
                "access_token": access_token,
                "refresh_token": refresh_token,
            }
        _trigger_otp(session, cookies, headers)
    return {
        "status": "OTP_REQUIRED",
        "ctx": {
            "cookies": cookies,
            "cv": cv,
            "device_uuid": device_uuid,
            "client_uuid": client_uuid,
        },
    }


def login_step2(ctx: dict, otp: str) -> dict:
    """OTL verify -> token exchange. Returns {'access_token','refresh_token'}."""
    cookies = dict(ctx["cookies"])
    cv = ctx["cv"]
    headers = make_headers(ctx["device_uuid"], ctx["client_uuid"])
    with _client() as session:
        code = _verify_otp_get_code(session, cookies, otp, headers)
        access_token, refresh_token = _exchange_token(session, cv, code, headers)
    return {"access_token": access_token, "refresh_token": refresh_token}
