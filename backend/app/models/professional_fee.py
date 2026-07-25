import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import DATERANGE, UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, DeclarationHistoryMixin, TimestampMixin, UUIDPrimaryKeyMixin

# --- enums de dominio (ver "Honorarios profesionales" del plan / ADR-0044/0045/0046) ---


class ProfessionalFeeAgreementStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED = "CANCELLED"


class ProfessionalFeeChargeStatus(str, enum.Enum):
    """Estado persistido del cargo -- deliberadamente SIN `OVERDUE` (ver
    ADR-0045/"Riesgo de cobranza de honorarios"): depender del paso del
    tiempo sin un job programado dejaria la columna desactualizada. El
    riesgo de cobranza vencido se calcula en vivo en
    `app/services/professional_fee_risk.py`."""

    PENDING_ISSUANCE = "PENDING_ISSUANCE"
    ISSUED = "ISSUED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    CANCELLED = "CANCELLED"


_AGREEMENT_STATUS_CHECK = "status IN ('ACTIVE', 'SUPERSEDED', 'CANCELLED')"
_CHARGE_STATUS_VALUES = tuple(status.value for status in ProfessionalFeeChargeStatus)
_CHARGE_STATUS_CHECK = f"status IN {_CHARGE_STATUS_VALUES}"

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

_RECEIPT_STATUS_COHERENCE_CHECK = (
    "(status = 'ISSUED' AND voided_at IS NULL AND voided_by IS NULL AND void_reason IS NULL) "
    "OR (status = 'VOIDED' AND voided_at IS NOT NULL AND voided_by IS NOT NULL "
    "AND void_reason IS NOT NULL)"
)


class ProfessionalFeeAgreement(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Acuerdo de honorarios con un cliente, con vigencia historica (ver
    "Honorarios profesionales" del plan / ADR-0044). `supersede`/`cancel`
    cierran `valid_until` de verdad -- nunca solo cambian `status` -- porque
    el `EXCLUDE` compara `validity_range`, no `status` (mismo principio que
    `client_document_requirements`, ver ADR-0034)."""

    __tablename__ = "professional_fee_agreements"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_professional_fee_agreements_id_tenant"),
        # Auxiliar para la FK compuesta de operational_tasks (Fase 2/Bloque 1,
        # migracion 0014) -- forma 4 (honorario), ver ADR-0057.
        UniqueConstraint(
            "id", "tenant_id", "client_id", name="uq_professional_fee_agreements_id_tenant_client"
        ),
        CheckConstraint(_AGREEMENT_STATUS_CHECK, name="ck_professional_fee_agreements_status"),
        CheckConstraint("amount >= 0", name="ck_professional_fee_agreements_amount_positive"),
        CheckConstraint("currency = 'PEN'", name="ck_professional_fee_agreements_currency_pen"),
        CheckConstraint(
            "billing_day IS NULL OR billing_day BETWEEN 1 AND 31",
            name="ck_professional_fee_agreements_billing_day_range",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_until >= valid_from",
            name="ck_professional_fee_agreements_valid_range",
        ),
        ForeignKeyConstraint(
            ["client_id", "tenant_id"],
            ["clients.id", "clients.tenant_id"],
            name="fk_professional_fee_agreements_client_tenant",
        ),
        ExcludeConstraint(
            ("tenant_id", "="),
            ("client_id", "="),
            ("service_type", "="),
            ("validity_range", "&&"),
            using="gist",
            name="ex_professional_fee_agreements_no_overlap",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    service_type: Mapped[str] = mapped_column(String(50), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="PEN")
    billing_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    included_services: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ProfessionalFeeAgreementStatus.ACTIVE.value
    )
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Columna generada, solo para el EXCLUDE -- la app nunca la escribe.
    validity_range: Mapped[object] = mapped_column(
        DATERANGE,
        Computed("daterange(valid_from, valid_until, '[]')", persisted=True),
        nullable=True,
    )
    observations: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProfessionalFeeAgreementHistory(Base, UUIDPrimaryKeyMixin, DeclarationHistoryMixin):
    __tablename__ = "professional_fee_agreement_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["agreement_id", "tenant_id"],
            ["professional_fee_agreements.id", "professional_fee_agreements.tenant_id"],
            name="fk_professional_fee_agreement_history_agreement_tenant",
        ),
        ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_professional_fee_agreement_history_actor_tenant",
        ),
    )

    agreement_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    from_valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    to_valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)


class ProfessionalFeeCharge(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Cargo de honorarios generado a partir de un acuerdo vigente para un
    periodo tributario concreto -- raiz transaccional independiente de
    `declarations` (ver "Concurrencia financiera": nunca se bloquea
    `declarations` para operaciones de honorarios)."""

    __tablename__ = "professional_fee_charges"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_professional_fee_charges_id_tenant"),
        UniqueConstraint(
            "tenant_id", "agreement_id", "tax_period_id",
            name="uq_professional_fee_charges_agreement_period",
        ),
        # Auxiliar para la FK compuesta de operational_tasks (Fase 2/Bloque 1,
        # migracion 0014) -- forma 4 (honorario), ver ADR-0057.
        UniqueConstraint(
            "id", "tenant_id", "agreement_id", name="uq_professional_fee_charges_id_tenant_agreement"
        ),
        CheckConstraint(_CHARGE_STATUS_CHECK, name="ck_professional_fee_charges_status"),
        CheckConstraint("amount >= 0", name="ck_professional_fee_charges_amount_positive"),
        CheckConstraint("currency = 'PEN'", name="ck_professional_fee_charges_currency_pen"),
        ForeignKeyConstraint(
            ["agreement_id", "tenant_id"],
            ["professional_fee_agreements.id", "professional_fee_agreements.tenant_id"],
            name="fk_professional_fee_charges_agreement_tenant",
        ),
        ForeignKeyConstraint(
            ["tax_period_id", "tenant_id"],
            ["tax_periods.id", "tax_periods.tenant_id"],
            name="fk_professional_fee_charges_period_tenant",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    agreement_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tax_period_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="PEN")
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=ProfessionalFeeChargeStatus.PENDING_ISSUANCE.value
    )
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    observations: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProfessionalFeeChargeStatusHistory(Base, UUIDPrimaryKeyMixin, DeclarationHistoryMixin):
    """Historial del ciclo de vida del CARGO (no de cada pago individual --
    ver `ProfessionalFeePaymentHistory`, que describe el nacimiento/anulacion
    de cada pago)."""

    __tablename__ = "professional_fee_charge_status_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["charge_id", "tenant_id"],
            ["professional_fee_charges.id", "professional_fee_charges.tenant_id"],
            name="fk_professional_fee_charge_status_history_charge_tenant",
        ),
        ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_professional_fee_charge_status_history_actor_tenant",
        ),
    )

    charge_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    to_status: Mapped[str] = mapped_column(String(30), nullable=False)


class ProfessionalFeePayment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Pago de un cargo de honorarios (ver "Honorarios profesionales" del
    plan / ADR-0041/0042/0043). El pago y la anulacion bloquean
    `SELECT ... FOR UPDATE` sobre `professional_fee_charges`, NUNCA sobre
    `declarations`."""

    __tablename__ = "professional_fee_payments"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_professional_fee_payments_id_tenant"),
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_professional_fee_payments_idempotency_key"
        ),
        CheckConstraint("amount > 0", name="ck_professional_fee_payments_amount_positive"),
        CheckConstraint(
            "overpayment_amount IS NULL OR overpayment_amount > 0",
            name="ck_professional_fee_payments_overpayment_amount_positive",
        ),
        CheckConstraint(
            _OVERPAYMENT_COHERENCE_CHECK, name="ck_professional_fee_payments_overpayment_coherence"
        ),
        CheckConstraint(_VOID_COHERENCE_CHECK, name="ck_professional_fee_payments_void_coherence"),
        ForeignKeyConstraint(
            ["charge_id", "tenant_id"],
            ["professional_fee_charges.id", "professional_fee_charges.tenant_id"],
            name="fk_professional_fee_payments_charge_tenant",
        ),
        ForeignKeyConstraint(
            ["confirmed_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_professional_fee_payments_confirmed_by_tenant",
        ),
        ForeignKeyConstraint(
            ["voided_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_professional_fee_payments_voided_by_tenant",
        ),
        ForeignKeyConstraint(
            ["created_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_professional_fee_payments_created_by_tenant",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    charge_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    paid_at: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    payment_method: Mapped[str] = mapped_column(String(30), nullable=False)
    operation_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    observations: Mapped[str | None] = mapped_column(Text, nullable=True)

    overpayment_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    overpayment_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    overpayment_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    is_voided: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    voided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    void_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class ProfessionalFeePaymentHistory(Base, UUIDPrimaryKeyMixin, DeclarationHistoryMixin):
    """Historial de nacimiento y anulacion de cada PAGO individual (no
    confundir con `ProfessionalFeeChargeStatusHistory`, que describe el
    cargo) -- misma logica que `tax_payment_history`."""

    __tablename__ = "professional_fee_payment_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["payment_id", "tenant_id"],
            ["professional_fee_payments.id", "professional_fee_payments.tenant_id"],
            name="fk_professional_fee_payment_history_payment_tenant",
        ),
        ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_professional_fee_payment_history_actor_tenant",
        ),
    )

    payment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)


class ProfessionalReceipt(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Recibo emitido contra un cargo de honorarios. Puede ser parcial, pero
    la suma de recibos `ISSUED` de un mismo cargo nunca puede exceder
    `professional_fee_charges.amount` (validado en servicio, ver
    `professional_fee_receipt_service`)."""

    __tablename__ = "professional_receipts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "receipt_type", "number", name="uq_professional_receipts_type_number"
        ),
        CheckConstraint("amount > 0", name="ck_professional_receipts_amount_positive"),
        CheckConstraint("currency = 'PEN'", name="ck_professional_receipts_currency_pen"),
        CheckConstraint("status IN ('ISSUED', 'VOIDED')", name="ck_professional_receipts_status"),
        CheckConstraint(
            _RECEIPT_STATUS_COHERENCE_CHECK, name="ck_professional_receipts_status_coherence"
        ),
        ForeignKeyConstraint(
            ["charge_id", "tenant_id"],
            ["professional_fee_charges.id", "professional_fee_charges.tenant_id"],
            name="fk_professional_receipts_charge_tenant",
        ),
        ForeignKeyConstraint(
            ["issued_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_professional_receipts_issued_by_tenant",
        ),
        ForeignKeyConstraint(
            ["voided_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_professional_receipts_voided_by_tenant",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    charge_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    issued_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    receipt_type: Mapped[str] = mapped_column(String(20), nullable=False)
    number: Mapped[str] = mapped_column(String(50), nullable=False)
    issued_at: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="PEN")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ISSUED")
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    voided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    void_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    observations: Mapped[str | None] = mapped_column(Text, nullable=True)
