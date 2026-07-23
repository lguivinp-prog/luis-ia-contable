"""Professional fees: professional_fee_agreements, professional_fee_agreement_history,
professional_fee_charges, professional_fee_charge_status_history,
professional_fee_payments (idempotency_key), professional_fee_payment_history,
professional_receipts

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-23

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AGREEMENT_STATUS_CHECK = "status IN ('ACTIVE', 'SUPERSEDED', 'CANCELLED')"

_CHARGE_STATUS_CHECK = (
    "status IN ('PENDING_ISSUANCE', 'ISSUED', 'PAYMENT_PENDING', 'PARTIALLY_PAID', "
    "'PAID', 'CANCELLED')"
)

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


def upgrade() -> None:
    op.create_table(
        "professional_fee_agreements",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_type", sa.String(50), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="PEN"),
        sa.Column("billing_day", sa.SmallInteger, nullable=True),
        sa.Column("included_services", sa.Text, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("valid_from", sa.Date, nullable=False),
        sa.Column("valid_until", sa.Date, nullable=True),
        sa.Column("observations", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "tenant_id", name="uq_professional_fee_agreements_id_tenant"),
        sa.CheckConstraint(_AGREEMENT_STATUS_CHECK, name="ck_professional_fee_agreements_status"),
        sa.CheckConstraint("amount >= 0", name="ck_professional_fee_agreements_amount_positive"),
        sa.CheckConstraint("currency = 'PEN'", name="ck_professional_fee_agreements_currency_pen"),
        sa.CheckConstraint(
            "billing_day IS NULL OR billing_day BETWEEN 1 AND 31",
            name="ck_professional_fee_agreements_billing_day_range",
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_until >= valid_from",
            name="ck_professional_fee_agreements_valid_range",
        ),
        sa.ForeignKeyConstraint(
            ["client_id", "tenant_id"], ["clients.id", "clients.tenant_id"],
            name="fk_professional_fee_agreements_client_tenant",
        ),
    )
    op.execute(
        "ALTER TABLE professional_fee_agreements "
        "ADD COLUMN validity_range daterange "
        "GENERATED ALWAYS AS (daterange(valid_from, valid_until, '[]')) STORED"
    )
    op.execute(
        "ALTER TABLE professional_fee_agreements "
        "ADD CONSTRAINT ex_professional_fee_agreements_no_overlap "
        "EXCLUDE USING gist ("
        "tenant_id WITH =, "
        "client_id WITH =, "
        "service_type WITH =, "
        "validity_range WITH &&"
        ")"
    )

    op.create_table(
        "professional_fee_agreement_history",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column("audit_correlation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agreement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column("from_valid_until", sa.Date, nullable=True),
        sa.Column("to_valid_until", sa.Date, nullable=True),
        sa.ForeignKeyConstraint(
            ["agreement_id", "tenant_id"],
            ["professional_fee_agreements.id", "professional_fee_agreements.tenant_id"],
            name="fk_professional_fee_agreement_history_agreement_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_professional_fee_agreement_history_actor_tenant",
        ),
    )

    op.create_table(
        "professional_fee_charges",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column("agreement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tax_period_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="PEN"),
        sa.Column("status", sa.String(30), nullable=False, server_default="PENDING_ISSUANCE"),
        sa.Column("due_date", sa.Date, nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.Text, nullable=True),
        sa.Column("observations", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "tenant_id", name="uq_professional_fee_charges_id_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "agreement_id", "tax_period_id",
            name="uq_professional_fee_charges_agreement_period",
        ),
        sa.CheckConstraint(_CHARGE_STATUS_CHECK, name="ck_professional_fee_charges_status"),
        sa.CheckConstraint("amount >= 0", name="ck_professional_fee_charges_amount_positive"),
        sa.CheckConstraint("currency = 'PEN'", name="ck_professional_fee_charges_currency_pen"),
        sa.ForeignKeyConstraint(
            ["agreement_id", "tenant_id"],
            ["professional_fee_agreements.id", "professional_fee_agreements.tenant_id"],
            name="fk_professional_fee_charges_agreement_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tax_period_id", "tenant_id"], ["tax_periods.id", "tax_periods.tenant_id"],
            name="fk_professional_fee_charges_period_tenant",
        ),
    )

    op.create_table(
        "professional_fee_charge_status_history",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column("audit_correlation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("charge_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_status", sa.String(30), nullable=True),
        sa.Column("to_status", sa.String(30), nullable=False),
        sa.ForeignKeyConstraint(
            ["charge_id", "tenant_id"],
            ["professional_fee_charges.id", "professional_fee_charges.tenant_id"],
            name="fk_professional_fee_charge_status_history_charge_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_professional_fee_charge_status_history_actor_tenant",
        ),
    )

    op.create_table(
        "professional_fee_payments",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column("charge_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paid_at", sa.Date, nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("payment_method", sa.String(30), nullable=False),
        sa.Column("operation_number", sa.String(50), nullable=True),
        sa.Column("reference", sa.String(100), nullable=True),
        sa.Column("observations", sa.Text, nullable=True),
        sa.Column("overpayment_confirmed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("overpayment_reason", sa.Text, nullable=True),
        sa.Column("overpayment_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("confirmed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_voided", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("void_reason", sa.Text, nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "tenant_id", name="uq_professional_fee_payments_id_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_professional_fee_payments_idempotency_key"
        ),
        sa.CheckConstraint("amount > 0", name="ck_professional_fee_payments_amount_positive"),
        sa.CheckConstraint(
            "overpayment_amount IS NULL OR overpayment_amount > 0",
            name="ck_professional_fee_payments_overpayment_amount_positive",
        ),
        sa.CheckConstraint(
            _OVERPAYMENT_COHERENCE_CHECK, name="ck_professional_fee_payments_overpayment_coherence"
        ),
        sa.CheckConstraint(_VOID_COHERENCE_CHECK, name="ck_professional_fee_payments_void_coherence"),
        sa.ForeignKeyConstraint(
            ["charge_id", "tenant_id"],
            ["professional_fee_charges.id", "professional_fee_charges.tenant_id"],
            name="fk_professional_fee_payments_charge_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_professional_fee_payments_confirmed_by_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["voided_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_professional_fee_payments_voided_by_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["created_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_professional_fee_payments_created_by_tenant",
        ),
    )

    op.create_table(
        "professional_fee_payment_history",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column("audit_correlation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.ForeignKeyConstraint(
            ["payment_id", "tenant_id"],
            ["professional_fee_payments.id", "professional_fee_payments.tenant_id"],
            name="fk_professional_fee_payment_history_payment_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_professional_fee_payment_history_actor_tenant",
        ),
    )

    op.create_table(
        "professional_receipts",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column("charge_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("issued_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("receipt_type", sa.String(20), nullable=False),
        sa.Column("number", sa.String(50), nullable=False),
        sa.Column("issued_at", sa.Date, nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="PEN"),
        sa.Column("status", sa.String(20), nullable=False, server_default="ISSUED"),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("void_reason", sa.Text, nullable=True),
        sa.Column("document_reference", sa.Text, nullable=True),
        sa.Column("observations", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "tenant_id", "receipt_type", "number", name="uq_professional_receipts_type_number"
        ),
        sa.CheckConstraint("amount > 0", name="ck_professional_receipts_amount_positive"),
        sa.CheckConstraint("currency = 'PEN'", name="ck_professional_receipts_currency_pen"),
        sa.CheckConstraint("status IN ('ISSUED', 'VOIDED')", name="ck_professional_receipts_status"),
        sa.CheckConstraint(
            _RECEIPT_STATUS_COHERENCE_CHECK, name="ck_professional_receipts_status_coherence"
        ),
        sa.ForeignKeyConstraint(
            ["charge_id", "tenant_id"],
            ["professional_fee_charges.id", "professional_fee_charges.tenant_id"],
            name="fk_professional_receipts_charge_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["issued_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_professional_receipts_issued_by_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["voided_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_professional_receipts_voided_by_tenant",
        ),
    )


def downgrade() -> None:
    op.drop_table("professional_receipts")
    op.drop_table("professional_fee_payment_history")
    op.drop_table("professional_fee_payments")
    op.drop_table("professional_fee_charge_status_history")
    op.drop_table("professional_fee_charges")
    op.drop_table("professional_fee_agreement_history")
    op.execute(
        "ALTER TABLE professional_fee_agreements "
        "DROP CONSTRAINT ex_professional_fee_agreements_no_overlap"
    )
    op.drop_table("professional_fee_agreements")
