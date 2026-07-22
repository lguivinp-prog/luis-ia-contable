"""Initial schema: tenants, users, sessions, audit_logs, clients, obligation_catalog, client_obligations

Revision ID: 0001
Revises:
Create Date: 2026-07-22

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "tenants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(30), nullable=False, server_default="admin"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
    )

    op.create_table(
        "audit_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("entity_type", sa.String(60), nullable=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "clients",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column("ruc", sa.String(11), nullable=False),
        sa.Column(
            "ruc_last_digit",
            sa.SmallInteger,
            sa.Computed("CAST(SUBSTRING(ruc FROM 11 FOR 1) AS SMALLINT)", persisted=True),
            nullable=True,
        ),
        sa.Column("business_name", sa.String(255), nullable=False),
        sa.Column("trade_name", sa.String(255), nullable=True),
        sa.Column("person_type", sa.String(20), nullable=False),
        sa.Column("taxpayer_type", sa.String(100), nullable=True),
        sa.Column("ruc_status", sa.String(30), nullable=False),
        sa.Column("fiscal_address_condition", sa.String(30), nullable=False),
        sa.Column("fiscal_address", sa.Text, nullable=True),
        sa.Column("ubigeo_code", sa.String(6), nullable=True),
        sa.Column("department", sa.String(60), nullable=True),
        sa.Column("province", sa.String(60), nullable=True),
        sa.Column("district", sa.String(60), nullable=True),
        sa.Column("main_economic_activity", sa.String(255), nullable=True),
        sa.Column("secondary_economic_activities", postgresql.JSONB, nullable=True),
        sa.Column("tax_regime", sa.String(20), nullable=False),
        sa.Column("activities_start_date", sa.Date, nullable=True),
        sa.Column("issuance_system", sa.String(20), nullable=True),
        sa.Column("phone", sa.String(30), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("legal_representative_name", sa.String(255), nullable=True),
        sa.Column("legal_representative_doc", sa.String(20), nullable=True),
        sa.Column("contact_person", sa.String(255), nullable=True),
        sa.Column("whatsapp_number", sa.String(30), nullable=True),
        sa.Column("is_good_taxpayer", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("monthly_fee", sa.Numeric(10, 2), nullable=True),
        sa.Column("payment_day", sa.SmallInteger, nullable=True),
        sa.Column("onboarding_date", sa.Date, nullable=True),
        sa.Column(
            "service_status", sa.String(20), nullable=False, server_default="prospecto"
        ),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("tags", postgresql.JSONB, nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sol_username_encrypted", sa.LargeBinary, nullable=True),
        sa.Column("sol_password_encrypted", sa.LargeBinary, nullable=True),
        sa.Column("sol_cipher_version", sa.SmallInteger, nullable=True),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column(
            "updated_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "ruc", name="uq_clients_tenant_ruc"),
        sa.CheckConstraint(
            "payment_day IS NULL OR (payment_day BETWEEN 1 AND 31)",
            name="ck_clients_payment_day_range",
        ),
    )

    op.create_table(
        "obligation_catalog",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(30), nullable=False, unique=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("periodicity", sa.String(20), nullable=False),
        sa.Column("applies_to_regime", postgresql.JSONB, nullable=True),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("source_version", sa.String(50), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "client_obligations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column(
            "client_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clients.id"),
            nullable=False,
        ),
        sa.Column(
            "obligation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("obligation_catalog.id"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("client_id", "obligation_id", name="uq_client_obligation"),
    )


def downgrade() -> None:
    op.drop_table("client_obligations")
    op.drop_table("obligation_catalog")
    op.drop_table("clients")
    op.drop_table("audit_logs")
    op.drop_table("sessions")
    op.drop_table("users")
    op.drop_table("tenants")
