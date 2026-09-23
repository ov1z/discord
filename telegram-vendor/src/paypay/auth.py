"""Login/header helpers for the PayPay mobile API.

Isolated from client.py so the (volatile) authentication mechanics — PKCE,
device headers, the OAuth2 PAR/OTL dance — can be adjusted independently as
PayPay changes them. See docs/paypay-api.md for confidence levels.

NOTE: PayPay's *new-device login* is protected by an anti-bot layer that the
public wrappers no longer defeat, and the legacy 4-digit SMS OTP was replaced
by a one-time-link (OTL) 2FA flow. This module builds the requests; whether a
fresh login completes depends on the current server-side protection. The rest
of the bot never depends on login succeeding — see payments/mock.py and
TODO_PAYPAY.md.
"""
from __future__ import annotations

import base64
import hashlib
import os
from uuid import uuid4

APP_VERSION = "5.57.0"
CLIENT_ID = "pay2-mobile-app-client"
REDIRECT_URI = "paypay://oauth2/callback"
API_BASE = "https://app4.paypay.ne.jp"
WEB_BASE = "https://www.paypay.ne.jp"


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using S256."""
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def new_uuid() -> str:
    return str(uuid4())


def base_headers(client_uuid: str, device_uuid: str) -> dict[str, str]:
    """Static Android-app style headers used for API (app4) requests."""
    return {
        "Accept": "*/*",
        "Accept-Charset": "UTF-8",
        "Accept-Encoding": "gzip",
        "Client-Mode": "NORMAL",
        "Client-OS-Release-Version": "10",
        "Client-OS-Type": "ANDROID",
        "Client-OS-Version": "29.0.0",
        "Client-Type": "PAYPAYAPP",
        "Client-UUID": client_uuid,
        "Client-Version": APP_VERSION,
        "Device-Brand-Name": "KDDI",
        "Device-Hardware-Name": "qcom",
        "Device-Manufacturer-Name": "samsung",
        "Device-Name": "SCV38",
        "Device-UUID": device_uuid,
        "Is-Emulator": "false",
        "Network-Status": "WIFI",
        "System-Locale": "ja",
        "Timezone": "Asia/Tokyo",
        "User-Agent": f"PaypayApp/{APP_VERSION} Android10",
    }


def auth_headers(access_token: str, client_uuid: str, device_uuid: str) -> dict[str, str]:
    headers = base_headers(client_uuid, device_uuid)
    headers["Authorization"] = f"Bearer {access_token}"
    headers["Content-Type"] = "application/json"
    return headers
