from decimal import Decimal

import pytest

from app.services.tax_payment_status import compute_tax_payment_status


@pytest.mark.parametrize(
    "total_payable,remaining_credit,total_paid,expected",
    [
        (Decimal("0"), Decimal("0"), Decimal("0"), "NO_TAX_DUE"),
        (Decimal("500"), Decimal("100"), Decimal("0"), "CREDIT_BALANCE"),
        (Decimal("900"), Decimal("0"), Decimal("0"), "PAYMENT_PENDING"),
        (Decimal("900"), Decimal("0"), Decimal("400"), "PARTIALLY_PAID"),
        (Decimal("900"), Decimal("0"), Decimal("900"), "PAID"),
        (Decimal("900"), Decimal("0"), Decimal("1000"), "PAID"),
    ],
)
def test_compute_tax_payment_status(
    total_payable: Decimal, remaining_credit: Decimal, total_paid: Decimal, expected: str
) -> None:
    assert (
        compute_tax_payment_status(
            total_payable=total_payable, remaining_credit=remaining_credit, total_paid=total_paid
        )
        == expected
    )


def test_assessment_amount_formula_reference_case() -> None:
    gross = Decimal("1000")
    credits = Decimal("100")
    total_payable = max(gross - credits, Decimal("0"))
    remaining_credit = max(credits - gross, Decimal("0"))
    assert total_payable == Decimal("900")
    assert remaining_credit == Decimal("0")
