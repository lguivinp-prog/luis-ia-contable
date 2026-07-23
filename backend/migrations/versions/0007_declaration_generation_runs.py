"""Declaration generation runs: idempotent bulk generation with persisted
preview resolution per item

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-22

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

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


def upgrade() -> None:
    op.create_table(
        "declaration_generation_runs",
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
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filters", postgresql.JSONB, nullable=True),
        sa.Column("preview_checksum", sa.String(64), nullable=True),
        sa.Column("stale_diff", postgresql.JSONB, nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="PENDING"),
        sa.Column("total_analyzed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_created", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_skipped", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_failed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("errors", postgresql.JSONB, nullable=True),
        sa.Column(
            "correlation_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "tenant_id", name="uq_declaration_generation_runs_id_tenant"),
        sa.ForeignKeyConstraint(
            ["tax_period_id", "tenant_id"], ["tax_periods.id", "tax_periods.tenant_id"],
            name="fk_declaration_generation_runs_period_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_declaration_generation_runs_requested_by_tenant",
        ),
    )

    op.create_table(
        "declaration_generation_run_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_obligation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("classification", sa.String(30), nullable=False),
        sa.Column("declaration_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_detail", sa.Text, nullable=True),
        sa.Column("excluded_by_user", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "preview_schedule_source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tax_schedule_sources.id"),
            nullable=True,
        ),
        sa.Column("preview_schedule_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("preview_schedule_rule_type", sa.String(20), nullable=True),
        sa.Column("preview_schedule_entry_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("preview_schedule_exception_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("preview_original_due_date", sa.Date, nullable=True),
        sa.Column("preview_effective_due_date", sa.Date, nullable=True),
        sa.Column("preview_snapshot_schema_version", sa.SmallInteger, nullable=True),
        sa.Column("preview_resolution_snapshot", postgresql.JSONB, nullable=True),
        sa.Column("preview_resolution_hash", sa.String(64), nullable=True),
        sa.UniqueConstraint(
            "run_id", "client_obligation_id", name="uq_declaration_generation_run_items_run_obligation"
        ),
        sa.CheckConstraint(
            _ITEM_RULE_TYPE_CHECK, name="ck_declaration_generation_run_items_rule_type"
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "tenant_id"],
            ["declaration_generation_runs.id", "declaration_generation_runs.tenant_id"],
            name="fk_declaration_generation_run_items_run_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["client_id", "tenant_id"], ["clients.id", "clients.tenant_id"],
            name="fk_declaration_generation_run_items_client_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["client_obligation_id", "tenant_id", "client_id"],
            ["client_obligations.id", "client_obligations.tenant_id", "client_obligations.client_id"],
            name="fk_declaration_generation_run_items_obligation_tenant_client",
        ),
        sa.ForeignKeyConstraint(
            ["declaration_id", "tenant_id"], ["declarations.id", "declarations.tenant_id"],
            name="fk_declaration_generation_run_items_declaration_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["preview_schedule_version_id", "tenant_id", "preview_schedule_source_id"],
            [
                "tax_schedule_versions.id",
                "tax_schedule_versions.tenant_id",
                "tax_schedule_versions.source_id",
            ],
            name="fk_declaration_generation_run_items_preview_version",
        ),
        sa.ForeignKeyConstraint(
            ["preview_schedule_entry_id", "tenant_id", "preview_schedule_version_id"],
            ["tax_schedule_entries.id", "tax_schedule_entries.tenant_id", "tax_schedule_entries.version_id"],
            name="fk_declaration_generation_run_items_preview_entry",
        ),
        sa.ForeignKeyConstraint(
            ["preview_schedule_exception_id", "tenant_id", "preview_schedule_version_id"],
            [
                "tax_schedule_exceptions.id",
                "tax_schedule_exceptions.tenant_id",
                "tax_schedule_exceptions.version_id",
            ],
            name="fk_declaration_generation_run_items_preview_exception",
        ),
    )


def downgrade() -> None:
    op.drop_table("declaration_generation_run_items")
    op.drop_table("declaration_generation_runs")
