"""Riesgo de una declaracion (`computed_risk_status`): funcion pura unica,
NUNCA persistida ni aceptada en un payload (ver ADR-0018, corregida en la
tercera ronda para usar `filing_status` y distinguir CANCELLED de
NOT_APPLICABLE). La expresion SQL equivalente se usa en el tablero para
evitar N+1; `test_declaration_risk_python_matches_sql` prueba que ambas
formas coinciden."""

from datetime import date, timedelta

from sqlalchemy import Case, case, literal

from app.models.declaration import Declaration

CRITICAL_THRESHOLD_DAYS = 15
UPCOMING_THRESHOLD_DAYS = 5


def compute_risk_status(
    current_effective_due_date: date, workflow_status: str, filing_status: str, today: date
) -> str:
    if workflow_status == "CANCELLED":
        return "CANCELLED"
    if workflow_status == "NOT_APPLICABLE":
        return "NOT_APPLICABLE"
    if workflow_status == "CLOSED":
        return "CLOSED"
    if filing_status in ("FILED", "NOT_REQUIRED"):
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
        (Declaration.workflow_status == "CLOSED", literal("CLOSED")),
        (Declaration.filing_status.in_(["FILED", "NOT_REQUIRED"]), literal("ON_TRACK")),
        (Declaration.current_effective_due_date < critical_cutoff, literal("CRITICAL")),
        (Declaration.current_effective_due_date < today, literal("OVERDUE")),
        (Declaration.current_effective_due_date == today, literal("DUE_TODAY")),
        (Declaration.current_effective_due_date <= upcoming_cutoff, literal("UPCOMING")),
        else_=literal("ON_TRACK"),
    )
