"""Schemas de gestion operativa de tareas (Fase 2, Bloque 1, Secciones 12/13/15
del plan). `TaskDueInput` es el unico punto de entrada de fecha/hora desde el
cliente -- `due_has_time` siempre se deriva de si `due_time` fue provisto,
nunca se acepta directamente."""

from datetime import date, datetime, time
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

_MANUAL_TASK_TYPE = Literal[
    "TAX_PAYMENT_FOLLOWUP", "FEE_COLLECTION", "CLIENT_FOLLOWUP", "INTERNAL_REVIEW", "GENERAL"
]
_PRIORITY = Literal["LOW", "NORMAL", "HIGH", "URGENT"]


class TaskDueInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    due_date: date
    due_time: time | None = None
    starts_date: date | None = None
    starts_time: time | None = None

    @model_validator(mode="after")
    def _validate_starts(self) -> "TaskDueInput":
        if self.starts_time is not None and self.starts_date is None:
            raise ValueError("starts_time requiere starts_date.")
        return self


def _validate_title(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("El título no puede estar vacío.")
    if len(stripped) > 200:
        raise ValueError("El título excede el máximo de 200 caracteres.")
    return stripped


TaskTitleStr = Annotated[str, AfterValidator(_validate_title)]


def _validate_comment_body(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise ValueError("El comentario no puede estar vacío.")
    if len(trimmed) > 4000:
        raise ValueError("El comentario excede el máximo de 4000 caracteres.")
    return trimmed


TaskCommentBody = Annotated[str, AfterValidator(_validate_comment_body)]


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: TaskTitleStr
    description: str | None = None
    task_type: _MANUAL_TASK_TYPE
    priority: _PRIORITY = "NORMAL"
    due: TaskDueInput
    assigned_to: UUID | None = None
    client_id: UUID | None = None


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int
    title: TaskTitleStr | None = None
    description: str | None = None
    priority: _PRIORITY | None = None
    task_type: _MANUAL_TASK_TYPE | None = None


class TaskVersionedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int


def _validate_reason(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("El motivo no puede estar vacío ni contener solo espacios.")
    return stripped


ReasonStr = Annotated[str, AfterValidator(_validate_reason)]


class TaskCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int
    reason: ReasonStr


class TaskReassignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int
    assigned_to: UUID | None = None


class TaskRescheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int
    due: TaskDueInput


class TaskCommentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: TaskCommentBody


class _FieldChange(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_value: str | None = Field(default=None, alias="from")
    to: str | None = None


class TaskContentChange(BaseModel):
    """Contenido de `task_change_history.changes_json` (Seccion 5 del plan).
    Rechaza cualquier campo donde `from == to` -- el servicio nunca persiste
    un "cambio" que en realidad no cambia nada."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    title: _FieldChange | None = None
    description: _FieldChange | None = None
    task_type: _FieldChange | None = None

    @model_validator(mode="after")
    def _reject_noop_changes(self) -> "TaskContentChange":
        for name in ("title", "description", "task_type"):
            change = getattr(self, name)
            if change is not None and change.from_value == change.to:
                raise ValueError(f"El campo '{name}' no tiene un cambio real (from == to).")
        return self

    def is_empty(self) -> bool:
        return self.title is None and self.description is None and self.task_type is None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tenant_id: UUID
    client_id: UUID | None
    declaration_id: UUID | None
    declaration_document_item_id: UUID | None
    professional_fee_charge_id: UUID | None
    professional_fee_agreement_id: UUID | None
    tax_payment_id: UUID | None
    title: str
    description: str | None
    task_type: str
    priority: str
    status: str
    assigned_to: UUID | None
    created_by: UUID | None
    due_at: datetime
    due_has_time: bool
    source_due_at: datetime | None
    due_is_overridden: bool
    starts_at: datetime | None
    completed_at: datetime | None
    completed_by: UUID | None
    cancelled_at: datetime | None
    cancelled_by: UUID | None
    cancellation_reason: str | None
    resolution_origin: str | None
    generation_key: str | None
    is_system_generated: bool
    generation_run_id: UUID | None
    version: int
    created_at: datetime
    updated_at: datetime


class TaskDetailOut(TaskOut):
    assignee_is_active: bool | None
    source_due_mismatch: bool


class TaskListOut(BaseModel):
    items: list[TaskOut]
    total: int
    limit: int
    offset: int


class TaskCommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    task_id: UUID
    author_user_id: UUID
    body: str
    created_at: datetime


class TaskCommentListOut(BaseModel):
    items: list[TaskCommentOut]
    total: int
    limit: int
    offset: int


class TaskChangeHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    task_id: UUID
    actor_user_id: UUID | None
    created_at: datetime
    reason: str | None
    origin: str
    event_type: str
    from_status: str | None
    to_status: str | None
    from_assigned_to: UUID | None
    to_assigned_to: UUID | None
    from_due_at: datetime | None
    to_due_at: datetime | None
    from_priority: str | None
    to_priority: str | None
    from_source_due_at: datetime | None
    to_source_due_at: datetime | None
    from_due_is_overridden: bool | None
    to_due_is_overridden: bool | None
    from_due_has_time: bool | None
    to_due_has_time: bool | None
    changes_json: dict | None


class TaskChangeHistoryListOut(BaseModel):
    items: list[TaskChangeHistoryOut]
    total: int
    limit: int
    offset: int


class TaskSyncTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trigger_source: Literal["MANUAL", "LOGIN", "AGENDA_OPEN", "DASHBOARD_OPEN"]


class TaskSyncResult(BaseModel):
    run_id: UUID
    summary: dict


class TaskGenerationRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    trigger_source: str
    triggered_by: UUID | None
    started_at: datetime
    completed_at: datetime | None
    summary: dict


class TaskGenerationRunListOut(BaseModel):
    items: list[TaskGenerationRunOut]
    total: int
    limit: int
    offset: int


class AgendaSummaryOut(BaseModel):
    overdue_tasks: int
    due_today: int
    due_next_7_days: int
    urgent_tasks: int
    unassigned_tasks: int


class AlertsSummaryOut(BaseModel):
    overdue_tasks: int
    due_today: int
    urgent_tasks: int
    unassigned_tasks: int
    source_due_mismatch: int
