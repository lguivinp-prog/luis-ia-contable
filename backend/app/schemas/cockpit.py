import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator



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
    buzon_pending: int
    accounting_pending: int
    client_communication_pending: int


class CockpitRowOut(BaseModel):
    # M4: resumen del CONTROL SIRE (cuadre + revision del contador). NO es un estado de presentacion y no reemplaza a sire_*_status.
    sire_control_rvie: str = "NO_CONTROL"
    sire_control_rce: str = "NO_CONTROL"

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

    # Estados MANUALES (persistidos solo al primer cambio; sin fila = valores por defecto)
    buzon_status: str
    accounting_status: str
    client_communication_status: str

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


BuzonStatusLiteral = Literal["NOT_REVIEWED", "REVIEWED_CLEAR", "REVIEWED_ACTION_REQUIRED", "NOT_APPLICABLE"]
AccountingStatusLiteral = Literal["NOT_STARTED", "IN_PROGRESS", "REVIEWED", "COMPLETED", "NOT_APPLICABLE"]
CommunicationStatusLiteral = Literal["NOT_STARTED", "PREPARED", "SENT", "NOT_APPLICABLE"]


class OperationsOut(BaseModel):
    buzon_status: str
    accounting_status: str
    client_communication_status: str
    notes: str | None
    row_version: int  # 0 = sin fila todavia (valores por defecto, nada persistido)
    updated_at: datetime | None
    updated_by_name: str | None = None


class OperationsUpdateIn(BaseModel):
    """Solo los campos presentes se cambian (`notes: null` limpia las notas). Campos desconocidos => 422."""

    model_config = ConfigDict(extra="forbid")

    expected_row_version: int = Field(ge=0)
    buzon_status: BuzonStatusLiteral | None = None
    accounting_status: AccountingStatusLiteral | None = None
    client_communication_status: CommunicationStatusLiteral | None = None
    notes: Annotated[str, StringConstraints(max_length=2000)] | None = None

    @model_validator(mode="after")
    def _at_least_one_change(self) -> "OperationsUpdateIn":
        changing = self.model_fields_set & {"buzon_status", "accounting_status", "client_communication_status", "notes"}
        if not changing:
            raise ValueError("Se requiere al menos un campo a cambiar")
        for field in ("buzon_status", "accounting_status", "client_communication_status"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} no admite null")
        return self


class OperationsHistoryEntryOut(BaseModel):
    id: uuid.UUID
    field_name: str
    from_value: str | None
    to_value: str | None
    row_version_after: int
    actor_user_id: uuid.UUID | None
    actor_name: str | None
    created_at: datetime


class WorkspaceDeclarationOut(BaseModel):
    declaration_id: uuid.UUID
    obligation_code: str
    obligation_name: str
    workflow_status: str
    filing_status: str
    document_status: str
    tax_payment_status: str
    due_date: date
    risk: str
    sire_status: str | None
    tax_gross_amount: Decimal | None
    tax_total_payable: Decimal | None
    tax_total_paid: Decimal
    missing_documents_count: int


class WorkspaceTaskOut(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    priority: str
    due_at: datetime | None
    task_type: str


class WorkspaceCommunicationDraftOut(BaseModel):
    id: uuid.UUID
    status: str
    template_code: str | None
    created_at: datetime


class SolAccessCapabilityOut(BaseModel):
    """Solo lo necesario para mostrar la accion REAL existente (acceso SOL supervisado hasta el menu). Sin secretos."""

    credential_configured: bool
    credential_status: str | None
    latest_session_status: str | None


class WorkspaceOut(BaseModel):
    summary: CockpitRowOut
    period: CockpitPeriodOut
    today: date
    operations: OperationsOut | None = None
    declarations: list[WorkspaceDeclarationOut]
    open_tasks_count: int
    overdue_tasks_count: int
    tasks: list[WorkspaceTaskOut]
    communication_drafts_total: int
    communication_drafts: list[WorkspaceCommunicationDraftOut]
    sol_access: SolAccessCapabilityOut | None = None
    can_update_operations: bool = False
