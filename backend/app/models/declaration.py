import enum
import uuid
from datetime import date, datetime
from decimal import Decimal  # noqa: F401  (reservado para 3.2, ver docs/TAX_RULES_MODEL.md)

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, DeclarationHistoryMixin, TimestampMixin, UUIDPrimaryKeyMixin

# --- enums de dominio (ver "Maquinas de estado" del plan / ADR-0018) ---


class WorkflowStatus(str, enum.Enum):
    PENDING = "PENDING"
    DOCUMENTS_REQUESTED = "DOCUMENTS_REQUESTED"
    INFORMATION_INCOMPLETE = "INFORMATION_INCOMPLETE"
    DOCUMENTS_COMPLETE = "DOCUMENTS_COMPLETE"
    IN_PROGRESS = "IN_PROGRESS"
    UNDER_REVIEW = "UNDER_REVIEW"
    READY_TO_FILE = "READY_TO_FILE"
    CLOSED = "CLOSED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    CANCELLED = "CANCELLED"


class FilingStatus(str, enum.Enum):
    NOT_FILED = "NOT_FILED"
    READY = "READY"
    FILED = "FILED"
    FAILED = "FAILED"
    NOT_REQUIRED = "NOT_REQUIRED"


# tax_payment_status y document_status no tienen maquina de transiciones
# propia en 3.1 (sus tablas de origen -- tax_assessments/declaration_document_items
# -- llegan en 3.2); en 3.1 toda declaracion aplicable nace y permanece en estos
# dos valores iniciales (ver "Cierre de declaraciones durante 3.1").
class TaxPaymentStatus(str, enum.Enum):
    NOT_DETERMINED = "NOT_DETERMINED"
    NO_TAX_DUE = "NO_TAX_DUE"
    CREDIT_BALANCE = "CREDIT_BALANCE"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DocumentStatus(str, enum.Enum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    OBSERVED = "OBSERVED"
    COMPLETE = "COMPLETE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ScheduleRuleType(str, enum.Enum):
    ENTRY = "ENTRY"
    EXCEPTION = "EXCEPTION"


class StatusHistoryField(str, enum.Enum):
    WORKFLOW = "WORKFLOW"
    FILING = "FILING"
    TAX_PAYMENT = "TAX_PAYMENT"
    DOCUMENT = "DOCUMENT"


class HistoryOrigin(str, enum.Enum):
    MANUAL = "MANUAL"
    GENERATION = "GENERATION"
    RECALCULATION = "RECALCULATION"
    SYSTEM = "SYSTEM"


_RULE_TYPE_CHECK = (
    "(schedule_rule_type = 'ENTRY' AND schedule_entry_id IS NOT NULL "
    "AND schedule_exception_id IS NULL) OR "
    "(schedule_rule_type = 'EXCEPTION' AND schedule_exception_id IS NOT NULL "
    "AND schedule_entry_id IS NULL)"
)

_DUE_DATE_HISTORY_RULE_TYPE_CHECK = """
(
  (from_rule_type IS NULL AND from_entry_id IS NULL AND from_exception_id IS NULL)
  OR (from_rule_type = 'ENTRY' AND from_entry_id IS NOT NULL AND from_exception_id IS NULL)
  OR (from_rule_type = 'EXCEPTION' AND from_exception_id IS NOT NULL AND from_entry_id IS NULL)
)
AND
(
  (to_rule_type = 'ENTRY' AND to_entry_id IS NOT NULL AND to_exception_id IS NULL)
  OR (to_rule_type = 'EXCEPTION' AND to_exception_id IS NOT NULL AND to_entry_id IS NULL)
)
""".strip()


class Declaration(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Instancia real de una obligacion de un cliente para un periodo,
    generada desde una version APPROVED del motor de cronogramas (ver
    ADR-0016..0018, ADR-0025). El vencimiento tiene 3 campos sin ambiguedad
    (ver "Semantica de fechas de vencimiento" del plan): `initial_effective_due_date`
    nunca cambia; `current_original_due_date`/`current_effective_due_date` se
    actualizan en cada recalculo. `computed_risk_status` NO es columna: se
    calcula en vivo (ver app/services/declaration_risk.py)."""

    __tablename__ = "declarations"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_declarations_id_tenant"),
        # Auxiliar para FKs compuestas (id, tenant_id, client_id) desde
        # Bloque 3.2 (ver ADR-0036): declaration_document_items usa esta
        # combinacion junto con la de client_document_requirements para que
        # la base rechace un cruce entre clientes del mismo tenant.
        UniqueConstraint(
            "id", "tenant_id", "client_id", name="uq_declarations_id_tenant_client"
        ),
        UniqueConstraint(
            "tenant_id",
            "client_id",
            "client_obligation_id",
            "tax_period_id",
            name="uq_declarations_tenant_client_obligation_period",
        ),
        CheckConstraint(_RULE_TYPE_CHECK, name="ck_declarations_rule_type_coherence"),
        ForeignKeyConstraint(
            ["client_id", "tenant_id"],
            ["clients.id", "clients.tenant_id"],
            name="fk_declarations_client_tenant",
        ),
        ForeignKeyConstraint(
            ["client_obligation_id", "tenant_id", "client_id"],
            ["client_obligations.id", "client_obligations.tenant_id", "client_obligations.client_id"],
            name="fk_declarations_obligation_tenant_client",
        ),
        ForeignKeyConstraint(
            ["tax_period_id", "tenant_id"],
            ["tax_periods.id", "tax_periods.tenant_id"],
            name="fk_declarations_period_tenant",
        ),
        ForeignKeyConstraint(
            ["assigned_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_declarations_assigned_user_tenant",
        ),
        ForeignKeyConstraint(
            ["closed_by", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_declarations_closed_by_tenant",
        ),
        ForeignKeyConstraint(
            ["schedule_version_id", "tenant_id", "schedule_source_id"],
            [
                "tax_schedule_versions.id",
                "tax_schedule_versions.tenant_id",
                "tax_schedule_versions.source_id",
            ],
            name="fk_declarations_schedule_version_tenant_source",
        ),
        ForeignKeyConstraint(
            ["schedule_entry_id", "tenant_id", "schedule_version_id"],
            ["tax_schedule_entries.id", "tax_schedule_entries.tenant_id", "tax_schedule_entries.version_id"],
            name="fk_declarations_schedule_entry_tenant_version",
        ),
        ForeignKeyConstraint(
            ["schedule_exception_id", "tenant_id", "schedule_version_id"],
            [
                "tax_schedule_exceptions.id",
                "tax_schedule_exceptions.tenant_id",
                "tax_schedule_exceptions.version_id",
            ],
            name="fk_declarations_schedule_exception_tenant_version",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    client_obligation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tax_period_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    workflow_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=WorkflowStatus.PENDING.value
    )
    filing_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FilingStatus.NOT_FILED.value
    )
    tax_payment_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TaxPaymentStatus.NOT_DETERMINED.value
    )
    document_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DocumentStatus.NOT_CONFIGURED.value
    )

    initial_effective_due_date: Mapped[date] = mapped_column(Date, nullable=False)
    current_original_due_date: Mapped[date] = mapped_column(Date, nullable=False)
    current_effective_due_date: Mapped[date] = mapped_column(Date, nullable=False)

    schedule_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_schedule_sources.id"), nullable=False
    )
    schedule_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    schedule_rule_type: Mapped[str] = mapped_column(String(20), nullable=False)
    schedule_entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    schedule_exception_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    schedule_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    schedule_snapshot_schema_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1
    )

    presentation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    order_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    filing_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ready_to_file_override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    closed_with_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    close_override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    observations: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class DeclarationStatusHistory(Base, UUIDPrimaryKeyMixin, DeclarationHistoryMixin):
    __tablename__ = "declaration_status_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["declaration_id", "tenant_id"],
            ["declarations.id", "declarations.tenant_id"],
            name="fk_declaration_status_history_declaration_tenant",
        ),
        ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_declaration_status_history_actor_tenant",
        ),
    )

    declaration_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status_field: Mapped[str] = mapped_column(String(20), nullable=False)
    from_value: Mapped[str | None] = mapped_column(String(30), nullable=True)
    to_value: Mapped[str] = mapped_column(String(30), nullable=False)


class DeclarationDueDateHistory(Base, UUIDPrimaryKeyMixin, DeclarationHistoryMixin):
    """Snapshot historico versionado del vencimiento (ver ADR-0017): conserva
    el par completo anterior/nuevo, cada uno con su propia version de schema
    -- nunca una sola columna compartida. Se escribe tanto en la generacion
    inicial (par `from_*`/`previous_schedule_snapshot` en NULL) como en cada
    recalculo posterior."""

    __tablename__ = "declaration_due_date_history"
    __table_args__ = (
        CheckConstraint(
            _DUE_DATE_HISTORY_RULE_TYPE_CHECK, name="ck_declaration_due_date_history_rule_type"
        ),
        ForeignKeyConstraint(
            ["declaration_id", "tenant_id"],
            ["declarations.id", "declarations.tenant_id"],
            name="fk_declaration_due_date_history_declaration_tenant",
        ),
        ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_declaration_due_date_history_actor_tenant",
        ),
        ForeignKeyConstraint(
            ["from_version_id", "tenant_id", "from_source_id"],
            [
                "tax_schedule_versions.id",
                "tax_schedule_versions.tenant_id",
                "tax_schedule_versions.source_id",
            ],
            name="fk_declaration_due_date_history_from_version",
        ),
        ForeignKeyConstraint(
            ["to_version_id", "tenant_id", "to_source_id"],
            [
                "tax_schedule_versions.id",
                "tax_schedule_versions.tenant_id",
                "tax_schedule_versions.source_id",
            ],
            name="fk_declaration_due_date_history_to_version",
        ),
        ForeignKeyConstraint(
            ["from_entry_id", "tenant_id", "from_version_id"],
            ["tax_schedule_entries.id", "tax_schedule_entries.tenant_id", "tax_schedule_entries.version_id"],
            name="fk_declaration_due_date_history_from_entry",
        ),
        ForeignKeyConstraint(
            ["to_entry_id", "tenant_id", "to_version_id"],
            ["tax_schedule_entries.id", "tax_schedule_entries.tenant_id", "tax_schedule_entries.version_id"],
            name="fk_declaration_due_date_history_to_entry",
        ),
        ForeignKeyConstraint(
            ["from_exception_id", "tenant_id", "from_version_id"],
            [
                "tax_schedule_exceptions.id",
                "tax_schedule_exceptions.tenant_id",
                "tax_schedule_exceptions.version_id",
            ],
            name="fk_declaration_due_date_history_from_exception",
        ),
        ForeignKeyConstraint(
            ["to_exception_id", "tenant_id", "to_version_id"],
            [
                "tax_schedule_exceptions.id",
                "tax_schedule_exceptions.tenant_id",
                "tax_schedule_exceptions.version_id",
            ],
            name="fk_declaration_due_date_history_to_exception",
        ),
    )

    declaration_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    from_original_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    to_original_due_date: Mapped[date] = mapped_column(Date, nullable=False)
    from_effective_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    to_effective_due_date: Mapped[date] = mapped_column(Date, nullable=False)

    from_source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    to_source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    from_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    to_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    from_rule_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_rule_type: Mapped[str] = mapped_column(String(20), nullable=False)
    from_entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    to_entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    from_exception_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    to_exception_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    previous_snapshot_schema_version: Mapped[int | None] = mapped_column(
        SmallInteger, nullable=True
    )
    new_snapshot_schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    previous_schedule_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    new_schedule_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)


class DeclarationReopenHistory(Base, UUIDPrimaryKeyMixin, DeclarationHistoryMixin):
    __tablename__ = "declaration_reopen_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["declaration_id", "tenant_id"],
            ["declarations.id", "declarations.tenant_id"],
            name="fk_declaration_reopen_history_declaration_tenant",
        ),
        ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_declaration_reopen_history_actor_tenant",
        ),
    )

    declaration_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    from_workflow_status: Mapped[str] = mapped_column(String(30), nullable=False)
    to_workflow_status: Mapped[str] = mapped_column(String(30), nullable=False)


class DeclarationAssignmentHistory(Base, UUIDPrimaryKeyMixin, DeclarationHistoryMixin):
    __tablename__ = "declaration_assignment_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["declaration_id", "tenant_id"],
            ["declarations.id", "declarations.tenant_id"],
            name="fk_declaration_assignment_history_declaration_tenant",
        ),
        ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_declaration_assignment_history_actor_tenant",
        ),
        ForeignKeyConstraint(
            ["from_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_declaration_assignment_history_from_user_tenant",
        ),
        ForeignKeyConstraint(
            ["to_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_declaration_assignment_history_to_user_tenant",
        ),
    )

    declaration_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    from_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    to_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class FilingHistory(Base, UUIDPrimaryKeyMixin, DeclarationHistoryMixin):
    __tablename__ = "filing_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["declaration_id", "tenant_id"],
            ["declarations.id", "declarations.tenant_id"],
            name="fk_filing_history_declaration_tenant",
        ),
        ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_filing_history_actor_tenant",
        ),
    )

    declaration_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    from_filing_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_filing_status: Mapped[str] = mapped_column(String(20), nullable=False)
    presentation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    order_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    filing_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
