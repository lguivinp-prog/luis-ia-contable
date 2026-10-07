"""Logica PURA del Cockpit Multi-RUC (M1, Domain B): sin acceso a base de datos. Todo estado aqui es DERIVADO de
estados autoritativos ya existentes (declaraciones, liquidaciones, pagos, honorarios); nada se persiste.

Granularidad: UNA fila por (tenant, cliente, periodo tributario). El detalle por obligacion es drill-down."""

from __future__ import annotations

from datetime import date

from app.services.declaration_risk import compute_risk_status  # noqa: F401  (reexportado: unica implementacion del riesgo)

SIRE_CODES = ("SIRE-RVIE", "SIRE-RCE")

# --- fase mensual -------------------------------------------------------------------------------------------------
# Para un periodo P el trabajo operativo ocurre principalmente en el mes calendario SIGUIENTE. Precedencia determinista
# (los rangos de la operacion se solapan): dia 1-4 APERTURA, 5-14 OPERACION, >=15 CIERRE. Despues del vencimiento la fase
# sigue siendo CIERRE (el riesgo pasa a OVERDUE/CRITICAL por las reglas existentes). Antes del mes de trabajo: PRE_APERTURA.


def monthly_phase(period_end: date, today: date) -> str:
    work_year, work_month = (period_end.year + 1, 1) if period_end.month == 12 else (period_end.year, period_end.month + 1)
    work_start = date(work_year, work_month, 1)
    if today < work_start:
        return "PRE_APERTURA"
    if (today.year, today.month) == (work_year, work_month):
        if today.day < 5:
            return "APERTURA"
        if today.day < 15:
            return "OPERACION"
    return "CIERRE"


# --- SIRE (derivado de las declaraciones SIRE-RVIE / SIRE-RCE) -----------------------------------------------------

_SIRE_IN_PROGRESS_WORKFLOW = {
    "DOCUMENTS_REQUESTED",
    "INFORMATION_INCOMPLETE",
    "DOCUMENTS_COMPLETE",
    "IN_PROGRESS",
    "UNDER_REVIEW",
}
_SIRE_PENDING_RANK = {"NOT_STARTED": 0, "IN_PROGRESS": 1, "READY_FOR_SUNAT": 2, "PRESENTED": 3}


def sire_component_status(workflow_status: str, filing_status: str) -> str:
    """Mapeo CONSERVADOR de UNA declaracion SIRE al estado operativo del cockpit."""
    if workflow_status == "NOT_APPLICABLE":
        return "NOT_APPLICABLE"
    if filing_status == "FILED":
        return "PRESENTED"
    if filing_status == "NOT_REQUIRED":
        return "NOT_APPLICABLE"
    if filing_status == "FAILED":
        return "IN_PROGRESS"
    if filing_status == "READY" or workflow_status == "READY_TO_FILE":
        return "READY_FOR_SUNAT"
    if workflow_status == "PENDING":
        return "NOT_STARTED"
    return "IN_PROGRESS" if workflow_status in _SIRE_IN_PROGRESS_WORKFLOW else "NOT_STARTED"


def combine_sire(component_statuses: list[str]) -> str:
    """`component_statuses`: un valor por componente CONFIGURADO (NOT_STARTED/IN_PROGRESS/READY_FOR_SUNAT/PRESENTED/
    NOT_APPLICABLE). Sin componentes configurados -> NOT_CONFIGURED. Si discrepan, gana el MAS PENDIENTE aplicable."""
    if not component_statuses:
        return "NOT_CONFIGURED"
    applicable = [s for s in component_statuses if s != "NOT_APPLICABLE"]
    if not applicable:
        return "NOT_APPLICABLE"
    return min(applicable, key=lambda s: _SIRE_PENDING_RANK[s])


# --- agregacion de documentos / impuesto --------------------------------------------------------------------------

_DOC_SEVERITY = ("OBSERVED", "PENDING", "PARTIAL", "NOT_CONFIGURED")  # de mas a menos severo; todos "incompletos"
DOCUMENT_INCOMPLETE = ("OBSERVED", "PENDING", "PARTIAL")


def aggregate_document_status(statuses: list[str]) -> str | None:
    """Conservador: UNA declaracion aplicable incompleta impide COMPLETE. Sin declaraciones aplicables -> None."""
    if not statuses:
        return None
    for candidate in _DOC_SEVERITY:
        if candidate in statuses:
            return candidate
    if "COMPLETE" in statuses:
        return "COMPLETE"
    return "NOT_APPLICABLE"


_TAX_PAYMENT_SEVERITY = (
    "PAYMENT_PENDING",
    "PARTIALLY_PAID",
    "NOT_DETERMINED",
    "PAID",
    "CREDIT_BALANCE",
    "NO_TAX_DUE",
    "NOT_APPLICABLE",
)
TAX_PAYMENT_PENDING = ("PAYMENT_PENDING", "PARTIALLY_PAID")


def aggregate_tax_payment_status(statuses: list[str]) -> str | None:
    """Peor estado entre las declaraciones. `NOT_DETERMINED` es AUSENCIA de dato tributario (p. ej. una SIRE/PLAME sin
    liquidacion): se ignora mientras alguna declaracion tenga un estado determinado, para que no tape un `PAID`
    legitimo; si TODAS estan sin determinar, el resultado es `NOT_DETERMINED`."""
    if not statuses:
        return None
    determined = [s for s in statuses if s != "NOT_DETERMINED"]
    if determined:
        statuses = determined
    for candidate in _TAX_PAYMENT_SEVERITY:
        if candidate in statuses:
            return candidate
    return "NOT_APPLICABLE"


# --- riesgo global ------------------------------------------------------------------------------------------------
# Orden de severidad (de mas a menos severo). El riesgo de COBRANZA de honorarios (PENDING_FEES) esta SIEMPRE por debajo de
# cualquier riesgo legal/tributario vencido o por vencer hoy (CRITICAL/OVERDUE/DUE_TODAY/PENDING_TAX/PENDING_DOCUMENTS):
# nunca puede hacer que una obligacion vencida parezca menos grave.
OVERALL_RISK_ORDER = (
    "CRITICAL",
    "OVERDUE",
    "DUE_TODAY",
    "PENDING_TAX",
    "PENDING_DOCUMENTS",
    "PERIOD_NOT_GENERATED",
    "CLOSED_WITH_PENDING",
    "PENDING_FEES",
    "UPCOMING",
    "ON_TRACK",
    "CLOSED",
    "NOT_APPLICABLE",
)
_RISK_RANK = {name: index for index, name in enumerate(OVERALL_RISK_ORDER)}
_IGNORED_DECLARATION_RISK = ("CANCELLED", "NOT_APPLICABLE")


def risk_rank(risk: str) -> int:
    return _RISK_RANK.get(risk, len(OVERALL_RISK_ORDER))


def overall_risk(
    declaration_risks: list[str],
    *,
    period_generated: bool,
    document_status: str | None,
    tax_payment_status: str | None,
    fee_collection_risk: str | None,
) -> str:
    if not period_generated:
        return "PERIOD_NOT_GENERATED"
    candidates = [r for r in declaration_risks if r not in _IGNORED_DECLARATION_RISK]
    if not candidates:
        return "NOT_APPLICABLE"
    if document_status in DOCUMENT_INCOMPLETE:
        candidates.append("PENDING_DOCUMENTS")
    if tax_payment_status in TAX_PAYMENT_PENDING:
        candidates.append("PENDING_TAX")
    if fee_collection_risk in ("OVERDUE", "DUE_TODAY"):
        candidates.append("PENDING_FEES")
    return min(candidates, key=risk_rank)


# --- progreso de presentacion -------------------------------------------------------------------------------------


def filing_progress(*, period_generated: bool, applicable: int, pending: int, filed: int) -> str:
    if not period_generated:
        return "NOT_GENERATED"
    if applicable == 0:
        return "NOT_APPLICABLE"
    if pending == 0:
        return "ALL_FILED"
    return "PARTIAL" if filed > 0 else "NONE_FILED"
