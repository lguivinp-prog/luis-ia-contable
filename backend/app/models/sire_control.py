"""M4 -- expediente de CONTROL SIRE por contribuyente, periodo tributario y componente (RVIE | RCE).

Identidad logica: `tenant + cliente + periodo + componente`. Cada fuente de informacion es una fila VERSIONADA e INMUTABLE (`sire_control_sources`) con su
origen explicito (SUNAT oficial vs DARWIN/CONTASIS) y su modo de ingreso (manual del contador vs importacion): nunca se mezcla la informacion de SUNAT con los
resultados internos del sistema contable. Un importe no ingresado es NULL (desconocido), jamas cero. La revision profesional (`sire_control_reviews`) es una
decision humana versionada, separada del cuadre numerico, de la evidencia documental y de la presentacion oficial: ninguna de estas tablas modifica
declaraciones, pagos, vencimientos ni obligaciones."""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SireComponent(str, enum.Enum):
    RVIE = "RVIE"  # Registro de Ventas e Ingresos Electronicos
    RCE = "RCE"  # Registro de Compras Electronico


class SireOrigin(str, enum.Enum):
    SUNAT = "SUNAT"  # informacion OFICIAL obtenida de SUNAT (ingresada manualmente o por archivo)
    DARWIN = "DARWIN"  # resultados / exportaciones de DARWIN IA o CONTASIS SQL (jamas se accede a sus bases)


class SireEntryMode(str, enum.Enum):
    MANUAL = "MANUAL"  # datos de control ingresados por el contador
    IMPORT = "IMPORT"  # derivados de un archivo CSV/XLSX con mapeo explicito


class SireDecision(str, enum.Enum):
    APPROVED = "APPROVED"  # el contador verifico la comparacion (solo si cuadra)
    OBSERVED = "OBSERVED"  # con observaciones
    REQUIRES_REGULARIZATION = "REQUIRES_REGULARIZATION"  # diferencias por regularizar


def _in(values) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


COMPONENTS = tuple(c.value for c in SireComponent)
ORIGINS = tuple(o.value for o in SireOrigin)
ENTRY_MODES = tuple(m.value for m in SireEntryMode)
DECISIONS = tuple(d.value for d in SireDecision)
CURRENCIES = ("PEN", "USD")
FILE_STATUSES = ("STAGED", "ACTIVE", "SUPERSEDED")
FILE_PURPOSES = ("SOURCE_FILE", "EVIDENCE")
NUMERIC_18_2 = Numeric(18, 2)


class SireControl(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "sire_controls"
    __table_args__ = (
        UniqueConstraint("tenant_id", "client_id", "tax_period_id", "component", name="uq_sire_controls_scope"),
        UniqueConstraint("id", "tenant_id", name="uq_sire_controls_id_tenant"),
        UniqueConstraint("id", "tenant_id", "client_id", "tax_period_id", "component", name="uq_sire_controls_id_scope"),
        CheckConstraint(f"component IN {_in(COMPONENTS)}", name="ck_sire_controls_component"),
        CheckConstraint("tolerance_amount IS NULL OR tolerance_amount >= 0", name="ck_sire_controls_tolerance_nonnegative"),
        CheckConstraint(
            "(tolerance_amount IS NULL) OR (tolerance_reason IS NOT NULL AND length(btrim(tolerance_reason)) > 0 AND tolerance_set_by IS NOT NULL)",
            name="ck_sire_controls_tolerance_traceable",
        ),
        CheckConstraint("row_version >= 1", name="ck_sire_controls_row_version_positive"),
        ForeignKeyConstraint(["client_id", "tenant_id"], ["clients.id", "clients.tenant_id"], name="fk_sire_controls_client_tenant"),
        ForeignKeyConstraint(["tax_period_id", "tenant_id"], ["tax_periods.id", "tax_periods.tenant_id"], name="fk_sire_controls_period_tenant"),
        ForeignKeyConstraint(["created_by", "tenant_id"], ["users.id", "users.tenant_id"], name="fk_sire_controls_created_by_tenant"),
        Index("ix_sire_controls_tenant_period", "tenant_id", "tax_period_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tax_period_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    component: Mapped[str] = mapped_column(String(4), nullable=False)
    # Tolerancia EXPLICITA y trazable (NULL = comparacion exacta, el comportamiento por defecto). Nunca oculta las diferencias originales.
    tolerance_amount: Mapped[Decimal | None] = mapped_column(NUMERIC_18_2, nullable=True)
    tolerance_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    tolerance_set_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    tolerance_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class SireControlSource(Base, UUIDPrimaryKeyMixin):
    """Una VERSION de una fuente (SUNAT o DARWIN/CONTASIS) de un expediente. Inmutable salvo su paso a SUPERSEDED."""

    __tablename__ = "sire_control_sources"
    __table_args__ = (
        UniqueConstraint("control_id", "origin", "version", name="uq_sire_control_sources_version"),
        UniqueConstraint("id", "tenant_id", name="uq_sire_control_sources_id_tenant"),
        Index("ux_sire_control_sources_one_active", "control_id", "origin", unique=True, postgresql_where=sa.text("status = 'ACTIVE'")),
        Index("ux_sire_control_sources_idempotency", "tenant_id", "idempotency_key", unique=True, postgresql_where=sa.text("idempotency_key IS NOT NULL")),
        ForeignKeyConstraint(
            ["control_id", "tenant_id", "client_id", "tax_period_id", "component"],
            ["sire_controls.id", "sire_controls.tenant_id", "sire_controls.client_id", "sire_controls.tax_period_id", "sire_controls.component"],
            name="fk_sire_control_sources_scope",
        ),
        ForeignKeyConstraint(["created_by", "tenant_id"], ["users.id", "users.tenant_id"], name="fk_sire_control_sources_created_by_tenant"),
        CheckConstraint(f"origin IN {_in(ORIGINS)}", name="ck_sire_control_sources_origin"),
        CheckConstraint(f"entry_mode IN {_in(ENTRY_MODES)}", name="ck_sire_control_sources_entry_mode"),
        CheckConstraint("status IN ('ACTIVE', 'SUPERSEDED')", name="ck_sire_control_sources_status"),
        CheckConstraint(f"currency IN {_in(CURRENCIES)}", name="ck_sire_control_sources_currency"),
        CheckConstraint("version >= 1", name="ck_sire_control_sources_version_positive"),
        CheckConstraint("record_count IS NULL OR record_count >= 0", name="ck_sire_control_sources_record_count"),
        CheckConstraint(
            "record_count IS NOT NULL OR base_amount IS NOT NULL OR igv_amount IS NOT NULL OR total_amount IS NOT NULL",
            name="ck_sire_control_sources_has_data",
        ),
        CheckConstraint("notes IS NULL OR length(notes) <= 2000", name="ck_sire_control_sources_notes_length"),
        CheckConstraint("length(btrim(source_label)) > 0", name="ck_sire_control_sources_label_not_blank"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    control_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tax_period_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    component: Mapped[str] = mapped_column(String(4), nullable=False)
    origin: Mapped[str] = mapped_column(String(10), nullable=False)
    entry_mode: Mapped[str] = mapped_column(String(10), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="ACTIVE")
    # Datos de CONTROL (no determinaciones tributarias). NULL = desconocido; NUNCA cero implicito.
    record_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    base_amount: Mapped[Decimal | None] = mapped_column(NUMERIC_18_2, nullable=True)
    igv_amount: Mapped[Decimal | None] = mapped_column(NUMERIC_18_2, nullable=True)
    total_amount: Mapped[Decimal | None] = mapped_column(NUMERIC_18_2, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    source_label: Mapped[str] = mapped_column(String(120), nullable=False)  # identificacion de la fuente (reporte, exportacion...)
    source_date: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    import_meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # mapeo, hoja, estilo decimal, filas; jamas contenido de filas
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # huella de los valores (deteccion de duplicados)
    idempotency_key: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    replaces_source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SireControlFile(Base, UUIDPrimaryKeyMixin):
    """Archivo ORIGINAL conservado fuera del repositorio (FileStorageBackend). Nunca se interpreta un PDF/imagen; CSV/XLSX solo se leen con mapeo explicito."""

    __tablename__ = "sire_control_files"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_sire_control_files_id_tenant"),
        UniqueConstraint("storage_key", name="uq_sire_control_files_storage_key"),
        Index(
            "ux_sire_control_files_dedupe", "control_id", "origin", "sha256_hash", unique=True,
            postgresql_where=sa.text("status IN ('STAGED', 'ACTIVE')"),
        ),
        ForeignKeyConstraint(
            ["control_id", "tenant_id", "client_id", "tax_period_id", "component"],
            ["sire_controls.id", "sire_controls.tenant_id", "sire_controls.client_id", "sire_controls.tax_period_id", "sire_controls.component"],
            name="fk_sire_control_files_scope",
        ),
        ForeignKeyConstraint(["uploaded_by", "tenant_id"], ["users.id", "users.tenant_id"], name="fk_sire_control_files_uploaded_by_tenant"),
        CheckConstraint(f"origin IN {_in(ORIGINS)}", name="ck_sire_control_files_origin"),
        CheckConstraint(f"status IN {_in(FILE_STATUSES)}", name="ck_sire_control_files_status"),
        CheckConstraint(f"purpose IN {_in(FILE_PURPOSES)}", name="ck_sire_control_files_purpose"),
        CheckConstraint("size_bytes > 0", name="ck_sire_control_files_size_positive"),
        CheckConstraint("length(sha256_hash) = 64", name="ck_sire_control_files_sha256_length"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    control_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tax_period_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    component: Mapped[str] = mapped_column(String(4), nullable=False)
    origin: Mapped[str] = mapped_column(String(10), nullable=False)
    purpose: Mapped[str] = mapped_column(String(12), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="STAGED")
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    extension: Mapped[str] = mapped_column(String(10), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SireControlReview(Base, UUIDPrimaryKeyMixin):
    """Decision PROFESIONAL versionada. Registra la huella de la comparacion revisada: si una fuente cambia despues, el control exige una nueva revision."""

    __tablename__ = "sire_control_reviews"
    __table_args__ = (
        UniqueConstraint("control_id", "version", name="uq_sire_control_reviews_version"),
        Index("ux_sire_control_reviews_one_current", "control_id", unique=True, postgresql_where=sa.text("status = 'CURRENT'")),
        Index("ux_sire_control_reviews_idempotency", "tenant_id", "idempotency_key", unique=True, postgresql_where=sa.text("idempotency_key IS NOT NULL")),
        ForeignKeyConstraint(
            ["control_id", "tenant_id", "client_id", "tax_period_id", "component"],
            ["sire_controls.id", "sire_controls.tenant_id", "sire_controls.client_id", "sire_controls.tax_period_id", "sire_controls.component"],
            name="fk_sire_control_reviews_scope",
        ),
        ForeignKeyConstraint(["reviewed_by", "tenant_id"], ["users.id", "users.tenant_id"], name="fk_sire_control_reviews_reviewer_tenant"),
        CheckConstraint(f"decision IN {_in(DECISIONS)}", name="ck_sire_control_reviews_decision"),
        CheckConstraint("status IN ('CURRENT', 'SUPERSEDED')", name="ck_sire_control_reviews_status"),
        CheckConstraint("version >= 1", name="ck_sire_control_reviews_version_positive"),
        CheckConstraint("notes IS NULL OR length(notes) <= 2000", name="ck_sire_control_reviews_notes_length"),
        CheckConstraint("decision = 'APPROVED' OR (notes IS NOT NULL AND length(btrim(notes)) > 0)", name="ck_sire_control_reviews_notes_required"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    control_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tax_period_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    component: Mapped[str] = mapped_column(String(4), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="CURRENT")
    decision: Mapped[str] = mapped_column(String(30), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    comparison_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    comparison_status: Mapped[str] = mapped_column(String(20), nullable=False)
    comparison_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    sunat_source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    darwin_source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    idempotency_key: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reviewed_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
