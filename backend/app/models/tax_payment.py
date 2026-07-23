import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, DeclarationHistoryMixin, TimestampMixin, UUIDPrimaryKeyMixin

# --- ver "Pagos tributarios" del plan / ADR-0041/0042/0043 ---

_OVERPAYMENT_COHERENCE_CHECK = (
    "overpayment_confirmed = false OR ("
    "overpayment_reason IS NOT NULL AND confirmed_by IS NOT NULL "
    "AND confirmed_at IS NOT NULL AND overpayment_amount IS NOT NULL"
    ")"
)

_VOID_COHERENCE_CHECK = (
    "is_voided = false OR ("
    "void_reason IS NOT NULL AND voided_at IS NOT NULL AND voided_by IS NOT NULL"
    ")"
)


class TaxPayment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Pago tributario contra una declaracion (ver "Pagos tributarios" del
    plan / ADR-0041/0042/0043). `assessment_id` es una referencia historica
    (a que determinacion vigente correspondia al momento del pago), pero el
    saldo siempre se calcula sumando TODOS los pagos validos de la
    declaracion contra la determinacion vigente actual -- nunca filtrado por
    `assessment_id` (ver "Concurrencia financiera"). `idempotency_key` protege
    contra reenvios de red, independiente del bloqueo `FOR UPDATE`."""

    __tablename__ = "tax_payments"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_tax_payments_id_tenant"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_tax_payments_idempotency_key"),
        CheckConstraint("amount > 0", name="ck_tax_payments_amount_positive"),
        CheckConstraint(
            "overpayment_amount IS NULL OR overpayment_amount > 0",
            name="ck_tax_payments_overpayment_amount_positive",
        ),
        CheckConstraint(_OVERPAYMENT_COHERENCE_CHECK, name="ck_tax_payments_overpayment_coherence"),
        CheckConstraint(_VOID_COHERENCE_CHECK, name="ck_tax_payments_void_coherence"),
        ForeignKeyConstraint(
            ["declaration_id", "tenant_id"],
            ["declarations.id", "declarations.tenant_id"],
            name="fk_tax_payments_declaration_tenant",
        ),
        ForeignKeyConstraint(
            ["assessment_id", "tenant_id"],
            ["tax_assessments.id", "tax_assessments.tenant_id"],
            name="fk_tax_payments_assessment_tenant",
        ),
        ForeignKeyConstraint(
            ["confirmed_by", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_tax_payments_confirmed_by_tenant",
        ),
        ForeignKeyConstraint(
            ["voided_by", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_tax_payments_voided_by_tenant",
        ),
        ForeignKeyConstraint(
            ["created_by", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_tax_payments_created_by_tenant",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    declaration_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    assessment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    paid_at: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    payment_method: Mapped[str] = mapped_column(String(30), nullable=False)
    operation_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    observations: Mapped[str | None] = mapped_column(Text, nullable=True)

    overpayment_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    overpayment_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    overpayment_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    is_voided: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    voided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    void_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class TaxPaymentHistory(Base, UUIDPrimaryKeyMixin, DeclarationHistoryMixin):
    """Historial de nacimiento y anulacion de un pago (ver "Pagos
    tributarios"): una fila `NULL->ACTIVE` al crearse, otra `ACTIVE->VOIDED`
    al anularse -- nunca solo la anulacion."""

    __tablename__ = "tax_payment_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["payment_id", "tenant_id"],
            ["tax_payments.id", "tax_payments.tenant_id"],
            name="fk_tax_payment_history_payment_tenant",
        ),
        ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_tax_payment_history_actor_tenant",
        ),
    )

    payment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
