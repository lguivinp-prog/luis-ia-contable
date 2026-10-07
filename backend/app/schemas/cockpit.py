import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class CockpitPeriodOut(BaseModel):
    id: uuid.UUID
    period_type: str
    canonical_code: str
    display_name: str | None
    start_date: date
    end_date: date
    is_open: bool


class CockpitSummaryOut(BaseModel):
    """Conteos sobre TODA la poblacion de clientes activos del periodo (no dependen de los filtros de la vista).
    Solo metricas con estado autoritativo existente (Buzon/contabilidad/comunicacion llegan en Domain C)."""

    active_clients: int
    period_not_generated: int
    due_soon: int
    overdue: int
    documents_incomplete: int
    sire_pending: int
    declarations_pending: int
    tax_payment_pending: int
    critical: int


class CockpitRowOut(BaseModel):
    client_id: uuid.UUID
    business_name: str
    ruc: str
    ruc_last_digit: int
    service_status: str

    tax_period_id: uuid.UUID
    tax_period_code: str
    tax_period_display: str | None

    monthly_phase: str
    period_generated: bool
    nearest_due_date: date | None

    declaration_count: int
    filed_declaration_count: int
    pending_declaration_count: int
    filing_progress: str

    document_status: str | None
    missing_documents_count: int

    sire_status: str | None
    sire_rvie_status: str | None
    sire_rce_status: str | None

    tax_total_assessed: Decimal | None
    tax_total_payable: Decimal | None
    tax_total_paid: Decimal
    tax_balance: Decimal | None
    tax_payment_status: str | None

    fee_total_amount: Decimal
    fee_total_paid: Decimal
    fee_balance: Decimal
    fee_worst_collection_risk: str | None

    overall_risk: str
    declaration_worst_risk: str | None


class CockpitOut(BaseModel):
    period: CockpitPeriodOut
    today: date
    summary: CockpitSummaryOut
    items: list[CockpitRowOut]
    total: int
    limit: int
    offset: int
