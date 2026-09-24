"""MockPaymentProvider behaviour."""
from __future__ import annotations

import pytest

from conftest import mock_link
from payments.base import AcceptOutcome
from payments.mock import MockPaymentProvider
from paypay.exceptions import (
    PayPayAlreadyAccepted,
    PayPayInvalidLink,
    PayPayNetworkError,
)
from paypay.models import LinkStatus


async def test_inspect_parses_amount() -> None:
    p = MockPaymentProvider()
    info = await p.inspect_payment(mock_link(500, "T1"))
    assert info.amount == 500
    assert info.link_id == "T1"
    assert info.status == LinkStatus.PENDING
    assert info.can_accept is True


async def test_invalid_link_rejected() -> None:
    p = MockPaymentProvider()
    with pytest.raises(PayPayInvalidLink):
        await p.inspect_payment("https://evil.example/nope")


async def test_accept_then_already() -> None:
    p = MockPaymentProvider()
    url = mock_link(500, "T2")
    result = await p.accept_payment(url)
    assert result.outcome == AcceptOutcome.ACCEPTED
    status = await p.get_payment_status(url)
    assert status.status == LinkStatus.SUCCESS
    with pytest.raises(PayPayAlreadyAccepted):
        await p.accept_payment(url)


async def test_timeout_on_accept_is_network_error() -> None:
    p = MockPaymentProvider()
    p.timeout_on_accept.add("T3")
    with pytest.raises(PayPayNetworkError):
        await p.accept_payment(mock_link(500, "T3"))
    info = await p.inspect_payment(mock_link(500, "T3"))
    assert info.status == LinkStatus.PENDING


async def test_timeout_after_accept_marks_received() -> None:
    p = MockPaymentProvider()
    p.timeout_after_accept.add("T4")
    with pytest.raises(PayPayNetworkError):
        await p.accept_payment(mock_link(500, "T4"))
    info = await p.inspect_payment(mock_link(500, "T4"))
    assert info.status == LinkStatus.SUCCESS
