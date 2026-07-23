from decimal import Decimal

import pytest

from app.services.professional_fee_charge_status import compute_charge_payment_status


@pytest.mark.parametrize(
    "amount,total_paid,expected",
    [
        (Decimal("500"), Decimal("0"), "PAYMENT_PENDING"),
        (Decimal("500"), Decimal("200"), "PARTIALLY_PAID"),
        (Decimal("500"), Decimal("500"), "PAID"),
        (Decimal("500"), Decimal("600"), "PAID"),
    ],
)
def test_compute_charge_payment_status(amount: Decimal, total_paid: Decimal, expected: str) -> None:
    assert compute_charge_payment_status(amount=amount, total_paid=total_paid) == expected
