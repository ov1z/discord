"""Abstract PaymentProvider interface.

Services depend only on this interface, never on PayPay directly. Swap in
MockPaymentProvider for tests / dev, PayPayPaymentProvider for production.
"""
from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from paypay.models import PaymentInfo


class AcceptOutcome(str, enum.Enum):
    ACCEPTED = "ACCEPTED"      # confirmed received
    ALREADY = "ALREADY"        # was already received (idempotent success)
    FAILED = "FAILED"          # definitively failed, nothing received
    UNKNOWN = "UNKNOWN"        # transport error; real state undetermined


@dataclass(slots=True)
class AcceptResult:
    outcome: AcceptOutcome
    payment_id: str | None = None
    raw: dict = field(default_factory=dict)


class PaymentProvider(ABC):
    """Provider-agnostic payment operations."""

    name: str = "base"

    @abstractmethod
    async def inspect_payment(self, url: str) -> PaymentInfo:
        """Look up a payment link and return normalized info. Read-only."""

    @abstractmethod
    async def accept_payment(
        self, url: str, link_info: PaymentInfo | None = None
    ) -> AcceptResult:
        """Attempt to receive the money for *url*. Must be safe to retry."""

    @abstractmethod
    async def get_payment_status(self, url: str) -> PaymentInfo:
        """Re-fetch authoritative status (used to confirm receipt)."""

    async def is_ready(self) -> bool:
        """Whether the provider can currently perform accepts."""
        return True

    async def close(self) -> None:  # pragma: no cover - optional
        return None
