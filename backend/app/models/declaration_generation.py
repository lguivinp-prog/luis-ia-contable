import enum
import uuid
from datetime import date, datetime

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
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class GenerationRunStatus(str, enum.Enum):
    PENDING = "PENDING"
    PREVIEWED = "PREVIEWED"
    STALE = "STALE"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
    FAILED = "FAILED"


class RunItemClassification(str, enum.Enum):
    APTO = "APTO"
    YA_EXISTENTE = "YA_EXISTENTE"
    CLIENTE_INACTIVO = "CLIENTE_INACTIVO"
    OBLIGACION_INACTIVA = "OBLIGACION_INACTIVA"
    SIN_CRONOGRAMA = "SIN_CRONOGRAMA"
    AMBIGUO = "AMBIGUO"
    SIN_REGLA_APLICABLE = "SIN_REGLA_APLICABLE"
    ERROR_INESPERADO = "ERROR_INESPERADO"


_ITEM_RULE_TYPE_CHECK = """
(
  (preview_schedule_rule_type IS NULL AND preview_schedule_entry_id IS NULL
   AND preview_schedule_exception_id IS NULL)
  OR (preview_schedule_rule_type = 'ENTRY' AND preview_schedule_entry_id IS NOT NULL
      AND preview_schedule_exception_id IS NULL)
  OR (preview_schedule_rule_type = 'EXCEPTION' AND preview_schedule_exception_id IS NOT NULL
      AND preview_schedule_entry_id IS NULL)
)
""".strip()


class DeclarationGenerationRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Ejecucion de generacion masiva idempotente (ver ADR-0021/ADR-0027/
    ADR-0028): preview -> clasificacion persistida por item -> confirm con
    prevalidacion de staleness -> transaccion con SAVEPOINT por item."""

    __tablename__ = "declaration_generation_runs"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_declaration_generation_runs_id_tenant"),
        ForeignKeyConstraint(
            ["tax_period_id", "tenant_id"],
            ["tax_periods.id", "tax_periods.tenant_id"],
            name="fk_declaration_generation_runs_period_tenant",
        ),
        ForeignKeyConstraint(
            ["requested_by", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_declaration_generation_runs_requested_by_tenant",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    tax_period_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    filters: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    preview_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stale_diff: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=GenerationRunStatus.PENDING.value
    )
    total_analyzed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), server_default=func.gen_random_uuid(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeclarationGenerationRunItem(Base, UUIDPrimaryKeyMixin):
    """Persiste exactamente lo que el usuario reviso en el preview (ver
    ADR-0027): el checksum del run y la pantalla de revision se calculan/leen
    SIEMPRE desde estas columnas persistidas, nunca re-resolviendo en
    silencio."""

    __tablename__ = "declaration_generation_run_items"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "client_obligation_id", name="uq_declaration_generation_run_items_run_obligation"
        ),
        CheckConstraint(_ITEM_RULE_TYPE_CHECK, name="ck_declaration_generation_run_items_rule_type"),
        ForeignKeyConstraint(
            ["run_id", "tenant_id"],
            ["declaration_generation_runs.id", "declaration_generation_runs.tenant_id"],
            name="fk_declaration_generation_run_items_run_tenant",
        ),
        ForeignKeyConstraint(
            ["client_id", "tenant_id"],
            ["clients.id", "clients.tenant_id"],
            name="fk_declaration_generation_run_items_client_tenant",
        ),
        ForeignKeyConstraint(
            ["client_obligation_id", "tenant_id", "client_id"],
            ["client_obligations.id", "client_obligations.tenant_id", "client_obligations.client_id"],
            name="fk_declaration_generation_run_items_obligation_tenant_client",
        ),
        ForeignKeyConstraint(
            ["declaration_id", "tenant_id"],
            ["declarations.id", "declarations.tenant_id"],
            name="fk_declaration_generation_run_items_declaration_tenant",
        ),
        ForeignKeyConstraint(
            ["preview_schedule_version_id", "tenant_id", "preview_schedule_source_id"],
            [
                "tax_schedule_versions.id",
                "tax_schedule_versions.tenant_id",
                "tax_schedule_versions.source_id",
            ],
            name="fk_declaration_generation_run_items_preview_version",
        ),
        ForeignKeyConstraint(
            ["preview_schedule_entry_id", "tenant_id", "preview_schedule_version_id"],
            ["tax_schedule_entries.id", "tax_schedule_entries.tenant_id", "tax_schedule_entries.version_id"],
            name="fk_declaration_generation_run_items_preview_entry",
        ),
        ForeignKeyConstraint(
            ["preview_schedule_exception_id", "tenant_id", "preview_schedule_version_id"],
            [
                "tax_schedule_exceptions.id",
                "tax_schedule_exceptions.tenant_id",
                "tax_schedule_exceptions.version_id",
            ],
            name="fk_declaration_generation_run_items_preview_exception",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    client_obligation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    classification: Mapped[str] = mapped_column(String(30), nullable=False)
    declaration_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    excluded_by_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    preview_schedule_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_schedule_sources.id"), nullable=True
    )
    preview_schedule_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    preview_schedule_rule_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    preview_schedule_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    preview_schedule_exception_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    preview_original_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    preview_effective_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    preview_snapshot_schema_version: Mapped[int | None] = mapped_column(
        SmallInteger, nullable=True
    )
    preview_resolution_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    preview_resolution_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
