import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class DeclarationOut(BaseModel):
    id: uuid.UUID
    client_id: uuid.UUID
    client_obligation_id: uuid.UUID
    tax_period_id: uuid.UUID
    assigned_user_id: uuid.UUID | None
    workflow_status: str
    filing_status: str
    tax_payment_status: str
    document_status: str
    initial_effective_due_date: date
    current_original_due_date: date
    current_effective_due_date: date
    schedule_source_id: uuid.UUID
    schedule_version_id: uuid.UUID
    schedule_rule_type: str
    schedule_entry_id: uuid.UUID | None
    schedule_exception_id: uuid.UUID | None
    schedule_snapshot_schema_version: int
    presentation_date: date | None
    order_number: str | None
    filing_reference: str | None
    ready_to_file_override_reason: str | None
    closed_at: datetime | None
    closed_by: uuid.UUID | None
    closed_with_pending: bool
    close_override_reason: str | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    observations: str | None
    row_version: int
    created_at: datetime
    updated_at: datetime
    # Calculado en vivo (nunca persistido, ver app/services/declaration_risk.py);
    # se rellena explicitamente al construir la respuesta.
    computed_risk_status: str | None = None

    model_config = {"from_attributes": True}


class DeclarationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: uuid.UUID
    client_obligation_id: uuid.UUID
    tax_period_id: uuid.UUID
    assigned_user_id: uuid.UUID | None = None


class WorkflowStatusChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1)
    reason: str | None = None
    row_version: int


class AssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID | None = None
    reason: str | None = None
    row_version: int


class FilingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(pattern="^(READY|FILED|FAILED|NOT_REQUIRED)$")
    presentation_date: date | None = None
    order_number: str | None = None
    reason: str | None = None
    row_version: int


class FilingCorrectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(pattern="^(NOT_FILED|READY|FILED|FAILED|NOT_REQUIRED)$")
    reason: str = Field(min_length=1)


class RecalculatePreviewOut(BaseModel):
    from_original_due_date: date
    from_effective_due_date: date
    to_original_due_date: date
    to_effective_due_date: date
    from_version_label: str
    to_version_label: str
    from_rule_type: str
    to_rule_type: str
    changed: bool


class RecalculateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)
    row_version: int


class ReopenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)
    target_workflow_status: str = Field(min_length=1)
    row_version: int


class CloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force_close_with_pending: bool = False
    close_override_reason: str | None = None


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)


class DeclarationStatusHistoryOut(BaseModel):
    id: uuid.UUID
    status_field: str
    from_value: str | None
    to_value: str
    actor_user_id: uuid.UUID | None
    created_at: datetime
    reason: str | None
    origin: str
    audit_correlation_id: uuid.UUID | None

    model_config = {"from_attributes": True}


class DeclarationDueDateHistoryOut(BaseModel):
    id: uuid.UUID
    from_original_due_date: date | None
    to_original_due_date: date
    from_effective_due_date: date | None
    to_effective_due_date: date
    from_rule_type: str | None
    to_rule_type: str
    previous_snapshot_schema_version: int | None
    new_snapshot_schema_version: int
    actor_user_id: uuid.UUID | None
    created_at: datetime
    reason: str | None
    origin: str
    audit_correlation_id: uuid.UUID | None

    model_config = {"from_attributes": True}


class DeclarationAssignmentHistoryOut(BaseModel):
    id: uuid.UUID
    from_user_id: uuid.UUID | None
    to_user_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    created_at: datetime
    reason: str | None
    origin: str

    model_config = {"from_attributes": True}


class FilingHistoryOut(BaseModel):
    id: uuid.UUID
    from_filing_status: str | None
    to_filing_status: str
    presentation_date: date | None
    order_number: str | None
    actor_user_id: uuid.UUID | None
    created_at: datetime
    reason: str | None
    origin: str

    model_config = {"from_attributes": True}


class DeclarationReopenHistoryOut(BaseModel):
    id: uuid.UUID
    from_workflow_status: str
    to_workflow_status: str
    actor_user_id: uuid.UUID | None
    created_at: datetime
    reason: str | None

    model_config = {"from_attributes": True}


class DeclarationHistoryOut(BaseModel):
    status_history: list[DeclarationStatusHistoryOut]
    due_date_history: list[DeclarationDueDateHistoryOut]
    assignment_history: list[DeclarationAssignmentHistoryOut]
    filing_history: list[FilingHistoryOut]
    reopen_history: list[DeclarationReopenHistoryOut]
