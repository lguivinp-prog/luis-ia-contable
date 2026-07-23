import enum
import uuid
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, DeclarationHistoryMixin, TimestampMixin, UUIDPrimaryKeyMixin

# --- enums de dominio (ver "Determinacion tributaria" del plan / ADR-0038) ---


class AssessmentType(str, enum.Enum):
    ORIGINAL = "ORIGINAL"
    CORRECTION = "CORRECTION"


_ASSESSMENT_TYPE_CHECK = "assessment_type IN ('ORIGINAL', 'CORRECTION')"

_TYPE_SEQUENCE_COHERENCE_CHECK = (
    "(assessment_type = 'ORIGINAL' AND sequence_number = 1) "
    "OR (assessment_type = 'CORRECTION' AND sequence_number > 1)"
)

_AMOUNT_FORMULA_CHECK = (
    "total_payable = GREATEST(gross_tax_amount - credits_applied, 0) "
    "AND remaining_credit = GREATEST(credits_applied - gross_tax_amount, 0)"
)

_SNAPSHOT_NOT_EMPTY_CHECK = (
    "new_assessment_snapshot <> '{}'::jsonb "
    "AND (previous_assessment_snapshot IS NULL OR previous_assessment_snapshot <> '{}'::jsonb)"
)


class TaxAssessment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Determinacion de impuesto (original o correccion) de una declaracion,
    versionada por `sequence_number` (ver "Determinacion tributaria" del plan
    / ADR-0038). Solo una fila puede ser `is_current=true` por declaracion
    (indice unico parcial)."""

    __tablename__ = "tax_assessments"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_tax_assessments_id_tenant"),
        UniqueConstraint(
            "tenant_id", "declaration_id", "sequence_number", name="uq_tax_assessments_declaration_sequence"
        ),
        CheckConstraint(_ASSESSMENT_TYPE_CHECK, name="ck_tax_assessments_type"),
        CheckConstraint("sequence_number >= 1", name="ck_tax_assessments_sequence_positive"),
        CheckConstraint(_TYPE_SEQUENCE_COHERENCE_CHECK, name="ck_tax_assessments_type_sequence_coherence"),
        CheckConstraint("gross_tax_amount >= 0", name="ck_tax_assessments_gross_amount_positive"),
        CheckConstraint("credits_applied >= 0", name="ck_tax_assessments_credits_positive"),
        CheckConstraint("currency = 'PEN'", name="ck_tax_assessments_currency_pen"),
        CheckConstraint(_AMOUNT_FORMULA_CHECK, name="ck_tax_assessments_amount_formula"),
        ForeignKeyConstraint(
            ["declaration_id", "tenant_id"],
            ["declarations.id", "declarations.tenant_id"],
            name="fk_tax_assessments_declaration_tenant",
        ),
        ForeignKeyConstraint(
            ["assessed_by", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_tax_assessments_assessed_by_tenant",
        ),
        Index(
            "ux_tax_assessments_one_current_per_declaration",
            "tenant_id",
            "declaration_id",
            unique=True,
            postgresql_where=sa.text("is_current"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    declaration_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    assessment_type: Mapped[str] = mapped_column(String(20), nullable=False)
    sequence_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    gross_tax_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    credits_applied: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    total_payable: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    remaining_credit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="PEN")
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    assessed_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    observations: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TaxAssessmentHistory(Base, UUIDPrimaryKeyMixin, DeclarationHistoryMixin):
    __tablename__ = "tax_assessment_history"
    __table_args__ = (
        CheckConstraint(_SNAPSHOT_NOT_EMPTY_CHECK, name="ck_tax_assessment_history_snapshots_not_empty"),
        ForeignKeyConstraint(
            ["assessment_id", "tenant_id"],
            ["tax_assessments.id", "tax_assessments.tenant_id"],
            name="fk_tax_assessment_history_assessment_tenant",
        ),
        ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_tax_assessment_history_actor_tenant",
        ),
    )

    assessment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    previous_assessment_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    new_assessment_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
