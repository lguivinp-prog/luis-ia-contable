import enum
import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# Postgres no permite subconsultas dentro de un CHECK; se usa el operador de
# contencion de arreglos (<@) para validar el rango 0-9 sin subquery. La
# ausencia de duplicados y de arreglos vacios se valida en el servicio
# (ver ADR-0014 / app/services/tax_schedule_digits.py).
_DIGITS_RANGE_CHECK = (
    "applicable_last_digits IS NULL OR "
    "applicable_last_digits <@ ARRAY[0,1,2,3,4,5,6,7,8,9]::smallint[]"
)


class TaxScheduleVersionStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    IMPORTED = "IMPORTED"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class TaxScheduleImportRunStatus(str, enum.Enum):
    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    CONFIRMED = "CONFIRMED"


class TaxScheduleSource(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Fuente de un cronograma (ver docs/TAX_RULES_MODEL.md). Cada fuente
    pertenece a una unica obligacion: no se asume que todas las obligaciones
    comparten el mismo cronograma."""

    __tablename__ = "tax_schedule_sources"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    obligation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("obligation_catalog.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)


class TaxScheduleVersion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Version de un cronograma (maquina de estados, ver ADR-0011).

    Solo puede existir una version APPROVED por combinacion exacta de
    (tenant_id, source_id, fiscal_year, schedule_type, scope_category_code);
    el indice unico parcial de abajo es el mecanismo de concurrencia real
    (una carrera de aprobaciones conflictivas falla con IntegrityError).
    """

    __tablename__ = "tax_schedule_versions"
    __table_args__ = (
        Index(
            "ux_tax_schedule_versions_one_approved_per_scope",
            "tenant_id",
            "source_id",
            "fiscal_year",
            "schedule_type",
            sa.text("COALESCE(scope_category_code, '')"),
            unique=True,
            postgresql_where=sa.text("status = 'APPROVED'"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_schedule_sources.id"), nullable=False
    )
    version_label: Mapped[str] = mapped_column(String(50), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    schedule_type: Mapped[str] = mapped_column(String(30), nullable=False, default="GENERAL")
    scope_category_code: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("tax_category_catalog.code"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TaxScheduleVersionStatus.DRAFT.value
    )
    normative_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_document_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    import_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "tax_schedule_import_runs.id",
            use_alter=True,
            name="fk_tax_schedule_versions_import_run_id",
        ),
        nullable=True,
    )
    superseded_by_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_schedule_versions.id"), nullable=True
    )
    observations: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaxScheduleEntry(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Regla general de vencimiento dentro de una version."""

    __tablename__ = "tax_schedule_entries"
    __table_args__ = (
        CheckConstraint(_DIGITS_RANGE_CHECK, name="ck_tax_schedule_entries_digits_range"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_schedule_versions.id"), nullable=False
    )
    tax_period: Mapped[str] = mapped_column(String(7), nullable=False)
    applicable_last_digits: Mapped[list[int] | None] = mapped_column(
        ARRAY(SmallInteger), nullable=True
    )
    required_category_code: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("tax_category_catalog.code"), nullable=True
    )
    priority: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    due_date_original: Mapped[date] = mapped_column(Date, nullable=False)
    due_date_effective: Mapped[date] = mapped_column(Date, nullable=False)
    due_date_type: Mapped[str] = mapped_column(String(30), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class TaxScheduleException(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Excepcion: siempre tiene prioridad sobre las entradas generales."""

    __tablename__ = "tax_schedule_exceptions"
    __table_args__ = (
        CheckConstraint(_DIGITS_RANGE_CHECK, name="ck_tax_schedule_exceptions_digits_range"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_schedule_versions.id"), nullable=False
    )
    tax_period: Mapped[str] = mapped_column(String(7), nullable=False)
    applicable_last_digits: Mapped[list[int] | None] = mapped_column(
        ARRAY(SmallInteger), nullable=True
    )
    required_category_code: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("tax_category_catalog.code"), nullable=True
    )
    priority: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    due_date_effective: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class TaxScheduleApprovalHistory(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "tax_schedule_approval_history"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_schedule_versions.id"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TaxScheduleImportRun(Base, UUIDPrimaryKeyMixin):
    """Evidencia de una importacion de CSV (ver ADR-0012). raw_content
    conserva el archivo crudo como texto; no se usa filesystem/Drive todavia.
    """

    __tablename__ = "tax_schedule_import_runs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_schedule_sources.id"), nullable=False
    )
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_schedule_versions.id"), nullable=True
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)
    # Alcance declarado por el usuario al subir el CSV (necesario para
    # comparar contra la version APPROVED previa del mismo scope y, al
    # confirmar, crear la version con el scope correcto).
    fiscal_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    schedule_type: Mapped[str] = mapped_column(String(30), nullable=False, default="GENERAL")
    scope_category_code: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("tax_category_catalog.code"), nullable=True
    )
    parsed_rows: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    row_count: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TaxScheduleImportRunStatus.RECEIVED.value
    )
    validation_errors: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    diff_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
