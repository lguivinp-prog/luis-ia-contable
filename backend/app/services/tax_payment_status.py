"""`tax_payment_status` derivado (ver "tax_payment_status" del plan /
ADR-0040): funcion pura, sin acceso a base de datos, compartida por
`tax_assessment_service` y `tax_payment_service` para que ambos dominios
recalculen el mismo estado de la misma forma. `NOT_DETERMINED` (sin
determinacion vigente) y `NOT_APPLICABLE` (derivado del workflow) se asignan
directamente por el llamador -- esta funcion solo cubre los 5 valores que
dependen de montos."""

from decimal import Decimal


def compute_tax_payment_status(
    *, total_payable: Decimal, remaining_credit: Decimal, total_paid: Decimal
) -> str:
    if remaining_credit > 0:
        return "CREDIT_BALANCE"
    if total_payable == 0:
        return "NO_TAX_DUE"
    if total_paid <= 0:
        return "PAYMENT_PENDING"
    if total_paid < total_payable:
        return "PARTIALLY_PAID"
    return "PAID"
