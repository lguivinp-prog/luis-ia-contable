"""Tax assessments and payments: tax_assessments, tax_assessment_history,
tax_payments (idempotency_key), tax_payment_history

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-23

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

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


def upgrade() -> None:
    op.create_table(
        "tax_assessments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column("declaration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assessment_type", sa.String(20), nullable=False),
        sa.Column("sequence_number", sa.SmallInteger, nullable=False),
        sa.Column("gross_tax_amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("credits_applied", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("total_payable", sa.Numeric(14, 2), nullable=False),
        sa.Column("remaining_credit", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="PEN"),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assessed_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("observations", sa.Text, nullable=True),
        sa.Column("is_current", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "tenant_id", name="uq_tax_assessments_id_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "declaration_id", "sequence_number",
            name="uq_tax_assessments_declaration_sequence",
        ),
        sa.CheckConstraint(_ASSESSMENT_TYPE_CHECK, name="ck_tax_assessments_type"),
        sa.CheckConstraint("sequence_number >= 1", name="ck_tax_assessments_sequence_positive"),
        sa.CheckConstraint(
            _TYPE_SEQUENCE_COHERENCE_CHECK, name="ck_tax_assessments_type_sequence_coherence"
        ),
        sa.CheckConstraint("gross_tax_amount >= 0", name="ck_tax_assessments_gross_amount_positive"),
        sa.CheckConstraint("credits_applied >= 0", name="ck_tax_assessments_credits_positive"),
        sa.CheckConstraint("currency = 'PEN'", name="ck_tax_assessments_currency_pen"),
        sa.CheckConstraint(_AMOUNT_FORMULA_CHECK, name="ck_tax_assessments_amount_formula"),
        sa.ForeignKeyConstraint(
            ["declaration_id", "tenant_id"], ["declarations.id", "declarations.tenant_id"],
            name="fk_tax_assessments_declaration_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["assessed_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_tax_assessments_assessed_by_tenant",
        ),
    )
    op.create_index(
        "ux_tax_assessments_one_current_per_declaration",
        "tax_assessments",
        ["tenant_id", "declaration_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )

    op.create_table(
        "tax_assessment_history",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
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
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("previous_assessment_snapshot", postgresql.JSONB, nullable=True),
        sa.Column("new_assessment_snapshot", postgresql.JSONB, nullable=False),
        sa.CheckConstraint(_SNAPSHOT_NOT_EMPTY_CHECK, name="ck_tax_assessment_history_snapshots_not_empty"),
        sa.ForeignKeyConstraint(
            ["assessment_id", "tenant_id"], ["tax_assessments.id", "tax_assessments.tenant_id"],
            name="fk_tax_assessment_history_assessment_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_tax_assessment_history_actor_tenant",
        ),
    )

    op.create_table(
        "tax_payments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column("declaration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paid_at", sa.Date, nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("payment_method", sa.String(30), nullable=False),
        sa.Column("operation_number", sa.String(50), nullable=True),
        sa.Column("reference", sa.String(100), nullable=True),
        sa.Column("observations", sa.Text, nullable=True),
        sa.Column("overpayment_confirmed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("overpayment_reason", sa.Text, nullable=True),
        sa.Column("overpayment_amount", sa.Numeric(14, 2), nullable=True),
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
        sa.UniqueConstraint("id", "tenant_id", name="uq_tax_payments_id_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_tax_payments_idempotency_key"
        ),
        sa.CheckConstraint("amount > 0", name="ck_tax_payments_amount_positive"),
        sa.CheckConstraint(
            "overpayment_amount IS NULL OR overpayment_amount > 0",
            name="ck_tax_payments_overpayment_amount_positive",
        ),
        sa.CheckConstraint(_OVERPAYMENT_COHERENCE_CHECK, name="ck_tax_payments_overpayment_coherence"),
        sa.CheckConstraint(_VOID_COHERENCE_CHECK, name="ck_tax_payments_void_coherence"),
        sa.ForeignKeyConstraint(
            ["declaration_id", "tenant_id"], ["declarations.id", "declarations.tenant_id"],
            name="fk_tax_payments_declaration_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id", "tenant_id"], ["tax_assessments.id", "tax_assessments.tenant_id"],
            name="fk_tax_payments_assessment_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_tax_payments_confirmed_by_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["voided_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_tax_payments_voided_by_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["created_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_tax_payments_created_by_tenant",
        ),
    )

    op.create_table(
        "tax_payment_history",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
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
            ["payment_id", "tenant_id"], ["tax_payments.id", "tax_payments.tenant_id"],
            name="fk_tax_payment_history_payment_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_tax_payment_history_actor_tenant",
        ),
    )


def downgrade() -> None:
    op.drop_table("tax_payment_history")
    op.drop_table("tax_payments")
    op.drop_table("tax_assessment_history")
    op.drop_index("ux_tax_assessments_one_current_per_declaration", table_name="tax_assessments")
    op.drop_table("tax_assessments")
