"""Riesgo de una declaracion (`computed_risk_status`): funcion pura unica,
NUNCA persistida ni aceptada en un payload (ver ADR-0018, extendida en
Bloque 3.2/Dominio E para reflejar `document_status`/`tax_payment_status`/
`closed_with_pending`, ahora que esos campos pueden alcanzar valores reales
gracias a los Dominios A-C). La expresion SQL equivalente se usa en el
tablero para evitar N+1; `test_declaration_risk_python_matches_sql` prueba
que ambas formas coinciden.

`_TAX_PAYMENT_ELIGIBLE`/`_DOCUMENT_ELIGIBLE` replican intencionalmente
`declaration_service.CLOSE_ELIGIBLE_TAX_PAYMENT`/`CLOSE_ELIGIBLE_DOCUMENT`
-- esta funcion permanece pura y sin dependencias de otros servicios, igual
que `tax_payment_status.py`/`professional_fee_charge_status.py`."""

from datetime import date, timedelta

from sqlalchemy import Case, case, literal

from app.models.declaration import Declaration

CRITICAL_THRESHOLD_DAYS = 15
UPCOMING_THRESHOLD_DAYS = 5

_TAX_PAYMENT_ELIGIBLE = ("PAID", "NO_TAX_DUE", "CREDIT_BALANCE", "NOT_APPLICABLE")
_DOCUMENT_ELIGIBLE = ("COMPLETE", "NOT_APPLICABLE")


def compute_risk_status(
    current_effective_due_date: date,
    workflow_status: str,
    filing_status: str,
    today: date,
    *,
    document_status: str = "NOT_APPLICABLE",
    tax_payment_status: str = "NOT_APPLICABLE",
    closed_with_pending: bool = False,
) -> str:
    if workflow_status == "CANCELLED":
        return "CANCELLED"
    if workflow_status == "NOT_APPLICABLE":
        return "NOT_APPLICABLE"
    if workflow_status == "CLOSED":
        return "CLOSED_WITH_PENDING" if closed_with_pending else "CLOSED"
    if filing_status in ("FILED", "NOT_REQUIRED"):
        if tax_payment_status not in _TAX_PAYMENT_ELIGIBLE:
            return "PENDING_TAX"
        if document_status not in _DOCUMENT_ELIGIBLE:
            return "PENDING_DOCUMENTS"
        return "ON_TRACK"

    critical_cutoff = today - timedelta(days=CRITICAL_THRESHOLD_DAYS)
    upcoming_cutoff = today + timedelta(days=UPCOMING_THRESHOLD_DAYS)
    if current_effective_due_date < critical_cutoff:
        return "CRITICAL"
    if current_effective_due_date < today:
        return "OVERDUE"
    if current_effective_due_date == today:
        return "DUE_TODAY"
    if current_effective_due_date <= upcoming_cutoff:
        return "UPCOMING"
    return "ON_TRACK"


def risk_status_sql_expression(today: date) -> Case:
    critical_cutoff = today - timedelta(days=CRITICAL_THRESHOLD_DAYS)
    upcoming_cutoff = today + timedelta(days=UPCOMING_THRESHOLD_DAYS)
    return case(
        (Declaration.workflow_status == "CANCELLED", literal("CANCELLED")),
        (Declaration.workflow_status == "NOT_APPLICABLE", literal("NOT_APPLICABLE")),
        (
            (Declaration.workflow_status == "CLOSED") & Declaration.closed_with_pending,
            literal("CLOSED_WITH_PENDING"),
        ),
        (Declaration.workflow_status == "CLOSED", literal("CLOSED")),
        (
            Declaration.filing_status.in_(["FILED", "NOT_REQUIRED"])
            & Declaration.tax_payment_status.notin_(list(_TAX_PAYMENT_ELIGIBLE)),
            literal("PENDING_TAX"),
        ),
        (
            Declaration.filing_status.in_(["FILED", "NOT_REQUIRED"])
            & Declaration.document_status.notin_(list(_DOCUMENT_ELIGIBLE)),
            literal("PENDING_DOCUMENTS"),
        ),
        (Declaration.filing_status.in_(["FILED", "NOT_REQUIRED"]), literal("ON_TRACK")),
        (Declaration.current_effective_due_date < critical_cutoff, literal("CRITICAL")),
        (Declaration.current_effective_due_date < today, literal("OVERDUE")),
        (Declaration.current_effective_due_date == today, literal("DUE_TODAY")),
        (Declaration.current_effective_due_date <= upcoming_cutoff, literal("UPCOMING")),
        else_=literal("ON_TRACK"),
    )
