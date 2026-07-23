"""Riesgo de cobranza de honorarios (ver "Riesgo de cobranza de honorarios"
del plan / ADR-0045): funcion pura, NUNCA persistida, separada del estado
`professional_fee_charges.status` (que deliberadamente no incluye
`OVERDUE`, ver `app/models/professional_fee.py`). La expresion SQL
equivalente recibe el saldo (`amount - pagado`) ya calculado por el
llamador (tablero/reportes, Dominio E) como una columna o subconsulta, para
mantenerse componible sin acoplarse a como se agrega el pago."""

from datetime import date
from decimal import Decimal

from sqlalchemy import Case, ColumnElement, case, literal

from app.models.professional_fee import ProfessionalFeeCharge


def compute_fee_collection_risk(
    *, due_date: date | None, status: str, balance: Decimal, today: date
) -> str:
    if status == "CANCELLED":
        return "CANCELLED"
    if status == "PAID" or balance <= 0:
        return "PAID"
    if due_date is None:
        return "ON_TRACK"
    if due_date < today:
        return "OVERDUE"
    if due_date == today:
        return "DUE_TODAY"
    return "ON_TRACK"


def fee_collection_risk_sql_expression(today: date, balance_expression: ColumnElement) -> Case:
    return case(
        (ProfessionalFeeCharge.status == "CANCELLED", literal("CANCELLED")),
        (ProfessionalFeeCharge.status == "PAID", literal("PAID")),
        (balance_expression <= 0, literal("PAID")),
        (ProfessionalFeeCharge.due_date.is_(None), literal("ON_TRACK")),
        (ProfessionalFeeCharge.due_date < today, literal("OVERDUE")),
        (ProfessionalFeeCharge.due_date == today, literal("DUE_TODAY")),
        else_=literal("ON_TRACK"),
    )
