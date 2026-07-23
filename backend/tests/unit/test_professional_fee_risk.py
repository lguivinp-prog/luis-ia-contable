from datetime import date
from decimal import Decimal

import pytest

from app.services.professional_fee_risk import compute_fee_collection_risk

TODAY = date(2026, 3, 15)


@pytest.mark.parametrize(
    "due_date,status,balance,expected",
    [
        (date(2026, 3, 1), "CANCELLED", Decimal("100"), "CANCELLED"),
        (date(2026, 3, 1), "PAID", Decimal("0"), "PAID"),
        (date(2026, 3, 1), "PARTIALLY_PAID", Decimal("0"), "PAID"),
        (None, "PAYMENT_PENDING", Decimal("100"), "ON_TRACK"),
        (date(2026, 3, 1), "PAYMENT_PENDING", Decimal("100"), "OVERDUE"),
        (TODAY, "PAYMENT_PENDING", Decimal("100"), "DUE_TODAY"),
        (date(2026, 4, 1), "PAYMENT_PENDING", Decimal("100"), "ON_TRACK"),
    ],
)
def test_compute_fee_collection_risk(due_date, status, balance, expected) -> None:
    assert (
        compute_fee_collection_risk(due_date=due_date, status=status, balance=balance, today=TODAY)
        == expected
    )
