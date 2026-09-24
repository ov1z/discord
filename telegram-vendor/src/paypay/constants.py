"""PayPay vocabulary that is data, not logic.

Kept apart from ``client.py`` so the transaction-type tables can be corrected
against real responses without touching request code.
"""
from __future__ import annotations

INCOMING_ORDER_TYPES: frozenset[str] = frozenset(
    {
        "CASHBACK",
        "P2P_CODE_RECEPTION",
        "INTEREST",
        "P2PRECEIVE",
        "RECEIVE",
        "CAMPAIGN_REWARD",
        "POINT_CONVERSION",
        "GIFT",
    }
)

OUTGOING_ORDER_TYPES: frozenset[str] = frozenset(
    {
        "P2PSEND",
        "PAYMENT",
        "TOPUP",
        "WITHDRAW",
        "BANK_TRANSFER",
        "DONATION",
        "PURCHASE",
        "SEND",
        "TOPUP_BANK",
    }
)

_INCOMING_HINTS = ("RECEIVE", "CASHBACK", "INTEREST", "REWARD", "GIFT")


def is_incoming_order_type(order_type: str | None) -> bool:
    """Whether *order_type* credits this account.

    Unknown types fall back to a keyword guess; an unrecognised type is
    treated as outgoing, which is the safe direction (we never hand over
    goods for a payment we cannot prove came in).
    """
    if not order_type:
        return False
    value = str(order_type).upper()
    if value in INCOMING_ORDER_TYPES:
        return True
    if value in OUTGOING_ORDER_TYPES:
        return False
    return any(hint in value for hint in _INCOMING_HINTS)
