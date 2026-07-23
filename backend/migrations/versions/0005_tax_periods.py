"""Tax periods: canonical tax_periods + tax_period_status_history

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-22

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEDULE_TAX_PERIOD_CODE_CHECK = (
    "schedule_tax_period_code IS NULL OR "
    "schedule_tax_period_code ~ '^[0-9]{4}(-(0[1-9]|1[0-2]))?$'"
)

_PERIOD_CANONICITY_CHECK = """
(
  (period_type = 'MONTHLY' AND
   fiscal_year BETWEEN 2000 AND 2100 AND
   canonical_code = fiscal_year::text || '-' || LPAD(month::text, 2, '0') AND
   schedule_tax_period_code = canonical_code AND
   start_date = make_date(fiscal_year, month, 1) AND
   end_date = (make_date(fiscal_year, month, 1) + INTERVAL '1 month' - INTERVAL '1 day')::date)
  OR
  (period_type = 'ANNUAL' AND
   fiscal_year BETWEEN 2000 AND 2100 AND
   month IS NULL AND
   canonical_code = fiscal_year::text AND
   schedule_tax_period_code = canonical_code AND
   start_date = make_date(fiscal_year, 1, 1) AND
   end_date = make_date(fiscal_year, 12, 31))
  OR
  (period_type = 'SPECIAL' AND
   canonical_code <> '' AND
   end_date >= start_date AND
   (schedule_tax_period_code IS NULL OR LEFT(schedule_tax_period_code, 4)::int = fiscal_year))
)
""".strip()


def upgrade() -> None:
    # tax_period_status_history.actor_user_id usa una FK compuesta hacia
    # users(id, tenant_id) (ver ADR-0025); esa es la primera tabla de este
    # bloque que la necesita, asi que la UNIQUE auxiliar de "users" se agrega
    # aqui en vez de en 0006 (las otras 6 auxiliares de Bloque 1/2 -- clients,
    # client_obligations, tax_schedule_sources/versions/entries/exceptions --
    # solo las necesita "declarations" y se agregan en 0006).
    op.create_unique_constraint("uq_users_id_tenant", "users", ["id", "tenant_id"])

    op.create_table(
        "tax_periods",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column("period_type", sa.String(20), nullable=False),
        sa.Column("fiscal_year", sa.SmallInteger, nullable=False),
        sa.Column("month", sa.SmallInteger, nullable=True),
        sa.Column("canonical_code", sa.String(50), nullable=False),
        sa.Column("schedule_tax_period_code", sa.String(7), nullable=True),
        sa.Column("display_name", sa.String(100), nullable=True),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date, nullable=False),
        sa.Column("is_open", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "tenant_id", name="uq_tax_periods_id_tenant"),
        sa.CheckConstraint(
            _SCHEDULE_TAX_PERIOD_CODE_CHECK, name="ck_tax_periods_schedule_code_format"
        ),
        sa.CheckConstraint(_PERIOD_CANONICITY_CHECK, name="ck_tax_periods_canonicity"),
    )

    op.create_index(
        "ux_tax_periods_monthly_identity",
        "tax_periods",
        ["tenant_id", "fiscal_year", "month"],
        unique=True,
        postgresql_where=sa.text("period_type = 'MONTHLY'"),
    )
    op.create_index(
        "ux_tax_periods_annual_identity",
        "tax_periods",
        ["tenant_id", "fiscal_year"],
        unique=True,
        postgresql_where=sa.text("period_type = 'ANNUAL'"),
    )
    op.create_index(
        "ux_tax_periods_special_identity",
        "tax_periods",
        ["tenant_id", "canonical_code"],
        unique=True,
        postgresql_where=sa.text("period_type = 'SPECIAL'"),
    )

    op.create_table(
        "tax_period_status_history",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column("tax_period_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column("audit_correlation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("from_is_open", sa.Boolean, nullable=False),
        sa.Column("to_is_open", sa.Boolean, nullable=False),
        sa.ForeignKeyConstraint(
            ["tax_period_id", "tenant_id"],
            ["tax_periods.id", "tax_periods.tenant_id"],
            name="fk_tax_period_status_history_period_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_tax_period_status_history_actor_tenant",
        ),
    )


def downgrade() -> None:
    op.drop_table("tax_period_status_history")
    op.drop_index("ux_tax_periods_special_identity", table_name="tax_periods")
    op.drop_index("ux_tax_periods_annual_identity", table_name="tax_periods")
    op.drop_index("ux_tax_periods_monthly_identity", table_name="tax_periods")
    op.drop_table("tax_periods")
    op.drop_constraint("uq_users_id_tenant", "users", type_="unique")
