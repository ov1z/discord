"""PayPay / payment exception hierarchy.

Never put raw credentials, tokens, OTP or PayPay raw errors into the message
that reaches Telegram — callers show a safe, generic string instead.
"""
from __future__ import annotations


class PayPayError(Exception):
    """Base class for all PayPay-related failures."""


class PayPayAuthError(PayPayError):
    """Login failed, or the session is not authenticated / was revoked."""


class PayPayOTPRequired(PayPayError):
    """Login requires a second factor (SMS OTP / one-time link)."""


class PayPaySessionExpired(PayPayAuthError):
    """Access token expired and could not be refreshed."""


class PayPayInvalidLink(PayPayError):
    """The submitted string is not a valid / usable PayPay P2P link."""


class PayPayAlreadyAccepted(PayPayError):
    """The link was already received (by us or someone else)."""


class PayPayNetworkError(PayPayError):
    """Transport-level failure where the final state is UNKNOWN."""


class PaymentAmountMismatch(PayPayError):
    """Link amount does not exactly equal the order amount."""

    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"amount mismatch: expected={expected} actual={actual}")
