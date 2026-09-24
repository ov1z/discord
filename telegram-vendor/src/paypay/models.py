"""Typed models for the PayPay layer.

These decouple the rest of the app from PayPay's raw JSON shape. If the API
changes, only ``paypay/`` needs to adapt; services keep using these models.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime


class LoginStatus(str, enum.Enum):
    SUCCESS = "SUCCESS"
    OTP_REQUIRED = "OTP_REQUIRED"
    FAILED = "FAILED"


class LinkStatus(str, enum.Enum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class LoginResult:
    status: LoginStatus
    message: str = ""
    otp_reference: str | None = None


@dataclass(slots=True)
class PayPaySession:
    """Re-usable session material. Credentials are never stored here."""

    access_token: str
    refresh_token: str | None = None
    device_uuid: str | None = None
    client_uuid: str | None = None
    session_id: str | None = None
    token_expires_at: datetime | None = None
    account_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "device_uuid": self.device_uuid,
            "client_uuid": self.client_uuid,
            "session_id": self.session_id,
            "token_expires_at": (
                self.token_expires_at.isoformat() if self.token_expires_at else None
            ),
            "account_id": self.account_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PayPaySession":
        raw_exp = data.get("token_expires_at")
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            device_uuid=data.get("device_uuid"),
            client_uuid=data.get("client_uuid"),
            session_id=data.get("session_id"),
            token_expires_at=datetime.fromisoformat(raw_exp) if raw_exp else None,
            account_id=data.get("account_id"),
        )


@dataclass(slots=True)
class RequestLink:
    """A payment request (P2P code) the shop issues for an exact amount."""

    link: str
    code: str
    amount: int
    session_id: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass(slots=True)
class Transaction:
    """One row of the account's payment history."""

    transaction_id: str
    amount: int
    incoming: bool
    status: str
    order_type: str = ""
    description: str = ""
    created_at: datetime | None = None
    raw: dict = field(default_factory=dict)

    @property
    def completed(self) -> bool:
        return self.status.upper() in {"COMPLETED", "SUCCESS", "RECEIVED"}


@dataclass(slots=True)
class RequestLink:
    """A payment request (P2P code) the shop issues for an exact amount."""

    link: str
    code: str
    amount: int
    session_id: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass(slots=True)
class Transaction:
    """One row of the account's payment history."""

    transaction_id: str
    amount: int
    incoming: bool
    status: str
    order_type: str = ""
    description: str = ""
    created_at: datetime | None = None
    raw: dict = field(default_factory=dict)

    @property
    def completed(self) -> bool:
        return self.status.upper() in {"COMPLETED", "SUCCESS", "RECEIVED"}


@dataclass(slots=True)
class PaymentInfo:
    """Normalized view of a P2P send-money link."""

    link_id: str
    amount: int
    status: LinkStatus
    can_accept: bool
    payment_id: str | None = None
    sender_name: str | None = None
    sender_external_id: str | None = None
    has_password: bool = False
    expires_at: datetime | None = None
    chat_room_id: str | None = None
    message_id: str | None = None
    request_id: str | None = None
    raw: dict = field(default_factory=dict)
