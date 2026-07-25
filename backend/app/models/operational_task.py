import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# --- enums de dominio (Fase 2, Bloque 1 -- ver ADR-0057 a ADR-0061) ---


class OperationalTaskStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class OperationalTaskPriority(str, enum.Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class OperationalTaskType(str, enum.Enum):
    DECLARATION_FILING = "DECLARATION_FILING"
    DOCUMENT_COLLECTION = "DOCUMENT_COLLECTION"
    TAX_PAYMENT_FOLLOWUP = "TAX_PAYMENT_FOLLOWUP"
    FEE_COLLECTION = "FEE_COLLECTION"
    CLIENT_FOLLOWUP = "CLIENT_FOLLOWUP"
    INTERNAL_REVIEW = "INTERNAL_REVIEW"
    GENERAL = "GENERAL"


# Tipos disponibles para creacion manual (Seccion 7/15 del plan): los 2 tipos
# con regla de generacion automatica (DECLARATION_FILING/DOCUMENT_COLLECTION)
# quedan fuera -- ck_operational_tasks_generated_source_matches_type los
# rechazaria de todos modos si intentaran traer un ancla sin ser generados.
MANUAL_TASK_TYPES: tuple[str, ...] = (
    OperationalTaskType.TAX_PAYMENT_FOLLOWUP.value,
    OperationalTaskType.FEE_COLLECTION.value,
    OperationalTaskType.CLIENT_FOLLOWUP.value,
    OperationalTaskType.INTERNAL_REVIEW.value,
    OperationalTaskType.GENERAL.value,
)


class ResolutionOrigin(str, enum.Enum):
    MANUAL = "MANUAL"
    SYSTEM = "SYSTEM"


class TaskGenerationTriggerSource(str, enum.Enum):
    MANUAL = "MANUAL"
    LOGIN = "LOGIN"
    AGENDA_OPEN = "AGENDA_OPEN"
    DASHBOARD_OPEN = "DASHBOARD_OPEN"
    CLI = "CLI"


class TaskChangeEventType(str, enum.Enum):
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    ASSIGNED = "ASSIGNED"
    RESCHEDULED = "RESCHEDULED"
    SOURCE_DUE_UPDATED = "SOURCE_DUE_UPDATED"
    SOURCE_COMPLETED = "SOURCE_COMPLETED"
    SOURCE_CANCELLED = "SOURCE_CANCELLED"


_TASK_STATUS_VALUES = tuple(status.value for status in OperationalTaskStatus)
_TASK_PRIORITY_VALUES = tuple(priority.value for priority in OperationalTaskPriority)
_TASK_TYPE_VALUES = tuple(task_type.value for task_type in OperationalTaskType)
_TRIGGER_SOURCE_VALUES = tuple(source.value for source in TaskGenerationTriggerSource)
_EVENT_TYPE_VALUES = tuple(event.value for event in TaskChangeEventType)

# --- operational_tasks: CHECK constraints (nombres y forma exactos del plan) ---

_FEE_PAIR_COHERENCE_CHECK = (
    "(professional_fee_charge_id IS NULL AND professional_fee_agreement_id IS NULL) "
    "OR (professional_fee_charge_id IS NOT NULL AND professional_fee_agreement_id IS NOT NULL)"
)

_SOURCE_SHAPE_CHECK = """
(
  (declaration_id IS NULL AND declaration_document_item_id IS NULL
   AND professional_fee_charge_id IS NULL AND professional_fee_agreement_id IS NULL
   AND tax_payment_id IS NULL)
  OR (declaration_id IS NOT NULL AND client_id IS NOT NULL
      AND declaration_document_item_id IS NULL AND professional_fee_charge_id IS NULL
      AND professional_fee_agreement_id IS NULL AND tax_payment_id IS NULL)
  OR (declaration_document_item_id IS NOT NULL AND declaration_id IS NOT NULL
      AND client_id IS NOT NULL AND professional_fee_charge_id IS NULL
      AND professional_fee_agreement_id IS NULL AND tax_payment_id IS NULL)
  OR (professional_fee_charge_id IS NOT NULL AND professional_fee_agreement_id IS NOT NULL
      AND client_id IS NOT NULL AND declaration_id IS NULL
      AND declaration_document_item_id IS NULL AND tax_payment_id IS NULL)
  OR (tax_payment_id IS NOT NULL AND declaration_id IS NOT NULL AND client_id IS NOT NULL
      AND declaration_document_item_id IS NULL AND professional_fee_charge_id IS NULL
      AND professional_fee_agreement_id IS NULL)
)
""".strip()

_CLIENT_REQUIRED_WITH_ANCHOR_CHECK = (
    "(declaration_id IS NULL AND declaration_document_item_id IS NULL AND tax_payment_id IS NULL) "
    "OR client_id IS NOT NULL"
)

_GENERATED_SOURCE_MATCHES_TYPE_CHECK = """
(
  is_system_generated = false
  OR (task_type = 'DECLARATION_FILING' AND declaration_id IS NOT NULL AND client_id IS NOT NULL
      AND declaration_document_item_id IS NULL AND professional_fee_charge_id IS NULL
      AND professional_fee_agreement_id IS NULL AND tax_payment_id IS NULL)
  OR (task_type = 'DOCUMENT_COLLECTION' AND declaration_document_item_id IS NOT NULL
      AND declaration_id IS NOT NULL AND client_id IS NOT NULL
      AND professional_fee_charge_id IS NULL AND professional_fee_agreement_id IS NULL
      AND tax_payment_id IS NULL)
  OR (task_type = 'FEE_COLLECTION' AND professional_fee_charge_id IS NOT NULL
      AND professional_fee_agreement_id IS NOT NULL AND client_id IS NOT NULL
      AND declaration_id IS NULL AND declaration_document_item_id IS NULL
      AND tax_payment_id IS NULL)
)
""".strip()

_GENERATION_COHERENCE_CHECK = """
(is_system_generated = true AND generation_key IS NOT NULL AND generation_run_id IS NOT NULL
 AND created_by IS NULL)
OR (is_system_generated = false AND generation_key IS NULL AND generation_run_id IS NULL
    AND created_by IS NOT NULL)
""".strip()

_SOURCE_DUE_COHERENCE_CHECK = """
(
  (is_system_generated = false AND source_due_at IS NULL AND due_is_overridden = false)
  OR
  (is_system_generated = true AND source_due_at IS NOT NULL
   AND (
     (due_is_overridden = false AND due_at = source_due_at AND due_has_time = false)
     OR (due_is_overridden = true)
   ))
)
""".strip()

_STATUS_RESOLUTION_COHERENCE_CHECK = """
(
  (status IN ('PENDING','IN_PROGRESS') AND completed_at IS NULL AND completed_by IS NULL
   AND cancelled_at IS NULL AND cancelled_by IS NULL AND cancellation_reason IS NULL
   AND resolution_origin IS NULL)
  OR (status = 'COMPLETED' AND completed_at IS NOT NULL AND completed_by IS NOT NULL
      AND resolution_origin = 'MANUAL' AND cancelled_at IS NULL AND cancelled_by IS NULL
      AND cancellation_reason IS NULL)
  OR (status = 'COMPLETED' AND completed_at IS NOT NULL AND completed_by IS NULL
      AND resolution_origin = 'SYSTEM' AND is_system_generated = true
      AND cancelled_at IS NULL AND cancelled_by IS NULL AND cancellation_reason IS NULL)
  OR (status = 'CANCELLED' AND cancelled_at IS NOT NULL AND cancelled_by IS NOT NULL
      AND cancellation_reason IS NOT NULL AND resolution_origin = 'MANUAL'
      AND completed_at IS NULL AND completed_by IS NULL)
  OR (status = 'CANCELLED' AND cancelled_at IS NOT NULL AND cancelled_by IS NULL
      AND cancellation_reason IS NOT NULL AND resolution_origin = 'SYSTEM'
      AND is_system_generated = true AND completed_at IS NULL AND completed_by IS NULL)
)
""".strip()

_STARTS_BEFORE_DUE_CHECK = "starts_at IS NULL OR starts_at <= due_at"

_TASK_TYPE_CHECK = f"task_type IN {_TASK_TYPE_VALUES}"
_TASK_STATUS_CHECK = f"status IN {_TASK_STATUS_VALUES}"
_TASK_PRIORITY_CHECK = f"priority IN {_TASK_PRIORITY_VALUES}"


class TaskGenerationRun(Base, UUIDPrimaryKeyMixin):
    """Resumen minimo de una ejecucion de sincronizacion (ver Seccion 5/8 del
    plan). `triggered_by` NULL unicamente cuando `trigger_source='CLI'` --
    el CLI nunca pasa por un usuario autenticado HTTP."""

    __tablename__ = "task_generation_runs"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_task_generation_runs_id_tenant"),
        CheckConstraint(
            f"trigger_source IN {_TRIGGER_SOURCE_VALUES}",
            name="ck_task_generation_runs_trigger_source",
        ),
        CheckConstraint(
            "(trigger_source = 'CLI' AND triggered_by IS NULL) "
            "OR (trigger_source IN ('MANUAL','LOGIN','AGENDA_OPEN','DASHBOARD_OPEN') "
            "AND triggered_by IS NOT NULL)",
            name="ck_task_generation_runs_trigger_coherence",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="ck_task_generation_runs_completed_after_started",
        ),
        ForeignKeyConstraint(
            ["triggered_by", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_task_generation_runs_triggered_by_tenant",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    trigger_source: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False)


class OperationalTask(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Tarea operativa materializada (ver Seccion 2/5 del plan / ADR-0057).
    Hibrido: filas reales con ciclo de vida propio + `OVERDUE` siempre
    calculado en vivo, nunca persistido (mismo principio que
    `declaration_risk.py`/`professional_fee_risk.py`)."""

    __tablename__ = "operational_tasks"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_operational_tasks_id_tenant"),
        Index(
            "uq_operational_tasks_tenant_generation_key",
            "tenant_id",
            "generation_key",
            unique=True,
            postgresql_where=text("generation_key IS NOT NULL"),
        ),
        CheckConstraint(_TASK_TYPE_CHECK, name="ck_operational_tasks_task_type"),
        CheckConstraint(_TASK_STATUS_CHECK, name="ck_operational_tasks_status"),
        CheckConstraint(_TASK_PRIORITY_CHECK, name="ck_operational_tasks_priority"),
        CheckConstraint("version >= 1", name="ck_operational_tasks_version_positive"),
        CheckConstraint(_FEE_PAIR_COHERENCE_CHECK, name="ck_operational_tasks_fee_pair_coherence"),
        CheckConstraint(_SOURCE_SHAPE_CHECK, name="ck_operational_tasks_source_shape"),
        CheckConstraint(
            _CLIENT_REQUIRED_WITH_ANCHOR_CHECK, name="ck_operational_tasks_client_required_with_anchor"
        ),
        CheckConstraint(
            _GENERATED_SOURCE_MATCHES_TYPE_CHECK, name="ck_operational_tasks_generated_source_matches_type"
        ),
        CheckConstraint(_GENERATION_COHERENCE_CHECK, name="ck_operational_tasks_generation_coherence"),
        CheckConstraint(_SOURCE_DUE_COHERENCE_CHECK, name="ck_operational_tasks_source_due_coherence"),
        CheckConstraint(
            _STATUS_RESOLUTION_COHERENCE_CHECK, name="ck_operational_tasks_status_resolution_coherence"
        ),
        CheckConstraint(_STARTS_BEFORE_DUE_CHECK, name="ck_operational_tasks_starts_before_due"),
        ForeignKeyConstraint(
            ["client_id", "tenant_id"], ["clients.id", "clients.tenant_id"],
            name="fk_operational_tasks_client_tenant",
        ),
        ForeignKeyConstraint(
            ["declaration_id", "tenant_id", "client_id"],
            ["declarations.id", "declarations.tenant_id", "declarations.client_id"],
            name="fk_operational_tasks_declaration_tenant_client",
        ),
        ForeignKeyConstraint(
            ["declaration_document_item_id", "tenant_id", "client_id", "declaration_id"],
            [
                "declaration_document_items.id",
                "declaration_document_items.tenant_id",
                "declaration_document_items.client_id",
                "declaration_document_items.declaration_id",
            ],
            name="fk_operational_tasks_document_item_tenant_client_declaration",
        ),
        ForeignKeyConstraint(
            ["professional_fee_charge_id", "tenant_id", "professional_fee_agreement_id"],
            [
                "professional_fee_charges.id",
                "professional_fee_charges.tenant_id",
                "professional_fee_charges.agreement_id",
            ],
            name="fk_operational_tasks_fee_charge_tenant_agreement",
        ),
        ForeignKeyConstraint(
            ["professional_fee_agreement_id", "tenant_id", "client_id"],
            [
                "professional_fee_agreements.id",
                "professional_fee_agreements.tenant_id",
                "professional_fee_agreements.client_id",
            ],
            name="fk_operational_tasks_fee_agreement_tenant_client",
        ),
        ForeignKeyConstraint(
            ["tax_payment_id", "tenant_id", "declaration_id"],
            ["tax_payments.id", "tax_payments.tenant_id", "tax_payments.declaration_id"],
            name="fk_operational_tasks_tax_payment_tenant_declaration",
        ),
        ForeignKeyConstraint(
            ["assigned_to", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_operational_tasks_assigned_to_tenant",
        ),
        ForeignKeyConstraint(
            ["created_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_operational_tasks_created_by_tenant",
        ),
        ForeignKeyConstraint(
            ["completed_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_operational_tasks_completed_by_tenant",
        ),
        ForeignKeyConstraint(
            ["cancelled_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_operational_tasks_cancelled_by_tenant",
        ),
        ForeignKeyConstraint(
            ["generation_run_id", "tenant_id"],
            ["task_generation_runs.id", "task_generation_runs.tenant_id"],
            name="fk_operational_tasks_generation_run_tenant",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    declaration_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    declaration_document_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    professional_fee_charge_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    professional_fee_agreement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    tax_payment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    task_type: Mapped[str] = mapped_column(String(30), nullable=False)
    priority: Mapped[str] = mapped_column(
        String(10), nullable=False, default=OperationalTaskPriority.NORMAL.value
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=OperationalTaskStatus.PENDING.value
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_has_time: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_is_overridden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_origin: Mapped[str | None] = mapped_column(String(10), nullable=True)

    generation_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_system_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    generation_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class TaskChangeHistory(Base, UUIDPrimaryKeyMixin):
    """Historial consolidado unico de toda mutacion de una tarea (ver
    Seccion 5 del plan). Las constraints por `event_type` usan
    `IS DISTINCT FROM` -- exigen un cambio de VALOR real, no solo presencia
    de columnas pobladas."""

    __tablename__ = "task_change_history"
    __table_args__ = (
        CheckConstraint(
            f"event_type IN {_EVENT_TYPE_VALUES}", name="ck_task_change_history_event_type"
        ),
        CheckConstraint(
            "(origin = 'SYSTEM' AND actor_user_id IS NULL) "
            "OR (origin = 'MANUAL' AND actor_user_id IS NOT NULL)",
            name="ck_task_change_history_origin_actor_coherence",
        ),
        CheckConstraint(
            "from_status IS NOT NULL OR to_status IS NOT NULL OR "
            "from_assigned_to IS NOT NULL OR to_assigned_to IS NOT NULL OR "
            "from_due_at IS NOT NULL OR to_due_at IS NOT NULL OR "
            "from_priority IS NOT NULL OR to_priority IS NOT NULL OR "
            "from_source_due_at IS NOT NULL OR to_source_due_at IS NOT NULL OR "
            "from_due_is_overridden IS NOT NULL OR to_due_is_overridden IS NOT NULL OR "
            "from_due_has_time IS NOT NULL OR to_due_has_time IS NOT NULL OR "
            "(changes_json IS NOT NULL AND changes_json <> '{}'::jsonb)",
            name="ck_task_change_history_non_empty",
        ),
        CheckConstraint(
            "event_type <> 'CREATED' OR "
            "(to_status IS NOT NULL AND to_due_at IS NOT NULL AND to_priority IS NOT NULL)",
            name="ck_tch_created_complete",
        ),
        CheckConstraint(
            "event_type NOT IN ('STARTED','COMPLETED','CANCELLED') OR "
            "(from_status IS NOT NULL AND to_status IS NOT NULL AND from_status IS DISTINCT FROM to_status)",
            name="ck_tch_status_events_have_status",
        ),
        CheckConstraint(
            "event_type <> 'ASSIGNED' OR from_assigned_to IS DISTINCT FROM to_assigned_to",
            name="ck_tch_assigned_has_change",
        ),
        CheckConstraint(
            "event_type <> 'RESCHEDULED' OR (from_due_at IS DISTINCT FROM to_due_at "
            "OR from_due_is_overridden IS DISTINCT FROM to_due_is_overridden "
            "OR from_due_has_time IS DISTINCT FROM to_due_has_time)",
            name="ck_tch_rescheduled_has_change",
        ),
        CheckConstraint(
            "event_type <> 'SOURCE_DUE_UPDATED' OR from_source_due_at IS DISTINCT FROM to_source_due_at",
            name="ck_tch_source_due_has_change",
        ),
        CheckConstraint(
            "event_type NOT IN ('SOURCE_COMPLETED','SOURCE_CANCELLED') OR "
            "(from_status IS NOT NULL AND to_status IS NOT NULL AND from_status IS DISTINCT FROM to_status)",
            name="ck_tch_source_resolution_has_status",
        ),
        CheckConstraint(
            "event_type <> 'UPDATED' OR from_priority IS DISTINCT FROM to_priority "
            "OR (changes_json IS NOT NULL AND changes_json <> '{}'::jsonb)",
            name="ck_tch_updated_has_content_change",
        ),
        ForeignKeyConstraint(
            ["task_id", "tenant_id"],
            ["operational_tasks.id", "operational_tasks.tenant_id"],
            name="fk_task_change_history_task_tenant",
        ),
        ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_task_change_history_actor_tenant",
        ),
        ForeignKeyConstraint(
            ["from_assigned_to", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_task_change_history_from_assigned_to_tenant",
        ),
        ForeignKeyConstraint(
            ["to_assigned_to", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_task_change_history_to_assigned_to_tenant",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin: Mapped[str] = mapped_column(String(10), nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)

    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    from_assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    to_assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    from_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    to_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    from_priority: Mapped[str | None] = mapped_column(String(10), nullable=True)
    to_priority: Mapped[str | None] = mapped_column(String(10), nullable=True)
    from_source_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    to_source_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    from_due_is_overridden: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    to_due_is_overridden: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    from_due_has_time: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    to_due_has_time: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    changes_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class TaskComment(Base, UUIDPrimaryKeyMixin):
    """Comentario append-only sobre una tarea (ver Seccion 13 del plan). Sin
    edicion ni borrado en este bloque."""

    __tablename__ = "task_comments"
    __table_args__ = (
        CheckConstraint(
            "length(trim(body)) BETWEEN 1 AND 4000", name="ck_task_comments_body_length"
        ),
        ForeignKeyConstraint(
            ["task_id", "tenant_id"],
            ["operational_tasks.id", "operational_tasks.tenant_id"],
            name="fk_task_comments_task_tenant",
        ),
        ForeignKeyConstraint(
            ["author_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_task_comments_author_tenant",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    author_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
