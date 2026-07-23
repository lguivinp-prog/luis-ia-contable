"""Estado de pago derivado de un cargo de honorarios (ver "Honorarios
profesionales" del plan / ADR-0045): funcion pura compartida por
`professional_fee_service` (emision) y `professional_fee_payment_service`
(pago/anulacion), analoga a `tax_payment_status.compute_tax_payment_status`.
Solo cubre los 3 valores que dependen del monto pagado; `PENDING_ISSUANCE` y
`CANCELLED` los asigna directamente el llamador."""

from decimal import Decimal


def compute_charge_payment_status(*, amount: Decimal, total_paid: Decimal) -> str:
    if total_paid >= amount:
        return "PAID"
    if total_paid > 0:
        return "PARTIALLY_PAID"
    return "PAYMENT_PENDING"
