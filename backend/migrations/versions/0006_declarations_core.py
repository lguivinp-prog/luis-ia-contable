"""Declarations core: auxiliary UNIQUE on Bloque 1/2 tables + declarations
+ 5 history tables (status, due_date, reopen, assignment, filing)

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-22

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RULE_TYPE_CHECK = (
    "(schedule_rule_type = 'ENTRY' AND schedule_entry_id IS NOT NULL "
    "AND schedule_exception_id IS NULL) OR "
    "(schedule_rule_type = 'EXCEPTION' AND schedule_exception_id IS NOT NULL "
    "AND schedule_entry_id IS NULL)"
)

_DUE_DATE_HISTORY_RULE_TYPE_CHECK = """
(
  (from_rule_type IS NULL AND from_entry_id IS NULL AND from_exception_id IS NULL)
  OR (from_rule_type = 'ENTRY' AND from_entry_id IS NOT NULL AND from_exception_id IS NULL)
  OR (from_rule_type = 'EXCEPTION' AND from_exception_id IS NOT NULL AND from_entry_id IS NULL)
)
AND
(
  (to_rule_type = 'ENTRY' AND to_entry_id IS NOT NULL AND to_exception_id IS NULL)
  OR (to_rule_type = 'EXCEPTION' AND to_exception_id IS NOT NULL AND to_entry_id IS NULL)
)
""".strip()


def _history_columns() -> list[sa.Column]:
    """Columnas comunes del mixin de historial (ver app/db/base.py
    DeclarationHistoryMixin)."""
    return [
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
    ]


def upgrade() -> None:
    # --- Integridad multitenant extendida a Bloque 1/2 (ver ADR-0025) ---
    # "users" ya recibio su UNIQUE(id, tenant_id) en 0005 (la necesitaba
    # tax_period_status_history). Las 6 restantes se agregan aqui, previas
    # a "declarations", que las usa en FKs compuestas.
    op.create_unique_constraint("uq_clients_id_tenant", "clients", ["id", "tenant_id"])
    op.create_unique_constraint(
        "uq_client_obligations_id_tenant_client", "client_obligations", ["id", "tenant_id", "client_id"]
    )
    op.create_unique_constraint(
        "uq_tax_schedule_sources_id_tenant", "tax_schedule_sources", ["id", "tenant_id"]
    )
    op.create_unique_constraint(
        "uq_tax_schedule_versions_id_tenant_source",
        "tax_schedule_versions",
        ["id", "tenant_id", "source_id"],
    )
    op.create_unique_constraint(
        "uq_tax_schedule_entries_id_tenant_version",
        "tax_schedule_entries",
        ["id", "tenant_id", "version_id"],
    )
    op.create_unique_constraint(
        "uq_tax_schedule_exceptions_id_tenant_version",
        "tax_schedule_exceptions",
        ["id", "tenant_id", "version_id"],
    )

    # --- declarations ---
    op.create_table(
        "declarations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_obligation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tax_period_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("workflow_status", sa.String(30), nullable=False, server_default="PENDING"),
        sa.Column("filing_status", sa.String(20), nullable=False, server_default="NOT_FILED"),
        sa.Column(
            "tax_payment_status", sa.String(20), nullable=False, server_default="NOT_DETERMINED"
        ),
        sa.Column("document_status", sa.String(20), nullable=False, server_default="NOT_CONFIGURED"),
        sa.Column("initial_effective_due_date", sa.Date, nullable=False),
        sa.Column("current_original_due_date", sa.Date, nullable=False),
        sa.Column("current_effective_due_date", sa.Date, nullable=False),
        sa.Column(
            "schedule_source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tax_schedule_sources.id"),
            nullable=False,
        ),
        sa.Column("schedule_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schedule_rule_type", sa.String(20), nullable=False),
        sa.Column("schedule_entry_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("schedule_exception_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("schedule_snapshot", postgresql.JSONB, nullable=False),
        sa.Column(
            "schedule_snapshot_schema_version", sa.SmallInteger, nullable=False, server_default="1"
        ),
        sa.Column("presentation_date", sa.Date, nullable=True),
        sa.Column("order_number", sa.String(50), nullable=True),
        sa.Column("filing_reference", sa.String(100), nullable=True),
        sa.Column("ready_to_file_override_reason", sa.Text, nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("closed_with_pending", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("close_override_reason", sa.Text, nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.Text, nullable=True),
        sa.Column("observations", sa.Text, nullable=True),
        sa.Column("row_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "tenant_id", name="uq_declarations_id_tenant"),
        sa.UniqueConstraint(
            "tenant_id",
            "client_id",
            "client_obligation_id",
            "tax_period_id",
            name="uq_declarations_tenant_client_obligation_period",
        ),
        sa.CheckConstraint(_RULE_TYPE_CHECK, name="ck_declarations_rule_type_coherence"),
        sa.ForeignKeyConstraint(
            ["client_id", "tenant_id"], ["clients.id", "clients.tenant_id"],
            name="fk_declarations_client_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["client_obligation_id", "tenant_id", "client_id"],
            ["client_obligations.id", "client_obligations.tenant_id", "client_obligations.client_id"],
            name="fk_declarations_obligation_tenant_client",
        ),
        sa.ForeignKeyConstraint(
            ["tax_period_id", "tenant_id"], ["tax_periods.id", "tax_periods.tenant_id"],
            name="fk_declarations_period_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_declarations_assigned_user_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["closed_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_declarations_closed_by_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_version_id", "tenant_id", "schedule_source_id"],
            [
                "tax_schedule_versions.id",
                "tax_schedule_versions.tenant_id",
                "tax_schedule_versions.source_id",
            ],
            name="fk_declarations_schedule_version_tenant_source",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_entry_id", "tenant_id", "schedule_version_id"],
            ["tax_schedule_entries.id", "tax_schedule_entries.tenant_id", "tax_schedule_entries.version_id"],
            name="fk_declarations_schedule_entry_tenant_version",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_exception_id", "tenant_id", "schedule_version_id"],
            [
                "tax_schedule_exceptions.id",
                "tax_schedule_exceptions.tenant_id",
                "tax_schedule_exceptions.version_id",
            ],
            name="fk_declarations_schedule_exception_tenant_version",
        ),
    )

    # --- declaration_status_history ---
    op.create_table(
        "declaration_status_history",
        *_history_columns(),
        sa.Column("declaration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status_field", sa.String(20), nullable=False),
        sa.Column("from_value", sa.String(30), nullable=True),
        sa.Column("to_value", sa.String(30), nullable=False),
        sa.ForeignKeyConstraint(
            ["declaration_id", "tenant_id"], ["declarations.id", "declarations.tenant_id"],
            name="fk_declaration_status_history_declaration_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_declaration_status_history_actor_tenant",
        ),
    )

    # --- declaration_due_date_history ---
    op.create_table(
        "declaration_due_date_history",
        *_history_columns(),
        sa.Column("declaration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_original_due_date", sa.Date, nullable=True),
        sa.Column("to_original_due_date", sa.Date, nullable=False),
        sa.Column("from_effective_due_date", sa.Date, nullable=True),
        sa.Column("to_effective_due_date", sa.Date, nullable=False),
        sa.Column("from_source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("to_source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("from_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("to_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("from_rule_type", sa.String(20), nullable=True),
        sa.Column("to_rule_type", sa.String(20), nullable=False),
        sa.Column("from_entry_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("to_entry_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("from_exception_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("to_exception_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("previous_snapshot_schema_version", sa.SmallInteger, nullable=True),
        sa.Column("new_snapshot_schema_version", sa.SmallInteger, nullable=False),
        sa.Column("previous_schedule_snapshot", postgresql.JSONB, nullable=True),
        sa.Column("new_schedule_snapshot", postgresql.JSONB, nullable=False),
        sa.CheckConstraint(
            _DUE_DATE_HISTORY_RULE_TYPE_CHECK, name="ck_declaration_due_date_history_rule_type"
        ),
        sa.ForeignKeyConstraint(
            ["declaration_id", "tenant_id"], ["declarations.id", "declarations.tenant_id"],
            name="fk_declaration_due_date_history_declaration_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_declaration_due_date_history_actor_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["from_version_id", "tenant_id", "from_source_id"],
            [
                "tax_schedule_versions.id",
                "tax_schedule_versions.tenant_id",
                "tax_schedule_versions.source_id",
            ],
            name="fk_declaration_due_date_history_from_version",
        ),
        sa.ForeignKeyConstraint(
            ["to_version_id", "tenant_id", "to_source_id"],
            [
                "tax_schedule_versions.id",
                "tax_schedule_versions.tenant_id",
                "tax_schedule_versions.source_id",
            ],
            name="fk_declaration_due_date_history_to_version",
        ),
        sa.ForeignKeyConstraint(
            ["from_entry_id", "tenant_id", "from_version_id"],
            ["tax_schedule_entries.id", "tax_schedule_entries.tenant_id", "tax_schedule_entries.version_id"],
            name="fk_declaration_due_date_history_from_entry",
        ),
        sa.ForeignKeyConstraint(
            ["to_entry_id", "tenant_id", "to_version_id"],
            ["tax_schedule_entries.id", "tax_schedule_entries.tenant_id", "tax_schedule_entries.version_id"],
            name="fk_declaration_due_date_history_to_entry",
        ),
        sa.ForeignKeyConstraint(
            ["from_exception_id", "tenant_id", "from_version_id"],
            [
                "tax_schedule_exceptions.id",
                "tax_schedule_exceptions.tenant_id",
                "tax_schedule_exceptions.version_id",
            ],
            name="fk_declaration_due_date_history_from_exception",
        ),
        sa.ForeignKeyConstraint(
            ["to_exception_id", "tenant_id", "to_version_id"],
            [
                "tax_schedule_exceptions.id",
                "tax_schedule_exceptions.tenant_id",
                "tax_schedule_exceptions.version_id",
            ],
            name="fk_declaration_due_date_history_to_exception",
        ),
    )

    # --- declaration_reopen_history ---
    op.create_table(
        "declaration_reopen_history",
        *_history_columns(),
        sa.Column("declaration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_workflow_status", sa.String(30), nullable=False),
        sa.Column("to_workflow_status", sa.String(30), nullable=False),
        sa.ForeignKeyConstraint(
            ["declaration_id", "tenant_id"], ["declarations.id", "declarations.tenant_id"],
            name="fk_declaration_reopen_history_declaration_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_declaration_reopen_history_actor_tenant",
        ),
    )

    # --- declaration_assignment_history ---
    op.create_table(
        "declaration_assignment_history",
        *_history_columns(),
        sa.Column("declaration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("to_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["declaration_id", "tenant_id"], ["declarations.id", "declarations.tenant_id"],
            name="fk_declaration_assignment_history_declaration_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_declaration_assignment_history_actor_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["from_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_declaration_assignment_history_from_user_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["to_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_declaration_assignment_history_to_user_tenant",
        ),
    )

    # --- filing_history ---
    op.create_table(
        "filing_history",
        *_history_columns(),
        sa.Column("declaration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_filing_status", sa.String(20), nullable=True),
        sa.Column("to_filing_status", sa.String(20), nullable=False),
        sa.Column("presentation_date", sa.Date, nullable=True),
        sa.Column("order_number", sa.String(50), nullable=True),
        sa.ForeignKeyConstraint(
            ["declaration_id", "tenant_id"], ["declarations.id", "declarations.tenant_id"],
            name="fk_filing_history_declaration_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_filing_history_actor_tenant",
        ),
    )


def downgrade() -> None:
    op.drop_table("filing_history")
    op.drop_table("declaration_assignment_history")
    op.drop_table("declaration_reopen_history")
    op.drop_table("declaration_due_date_history")
    op.drop_table("declaration_status_history")
    op.drop_table("declarations")

    op.drop_constraint(
        "uq_tax_schedule_exceptions_id_tenant_version", "tax_schedule_exceptions", type_="unique"
    )
    op.drop_constraint(
        "uq_tax_schedule_entries_id_tenant_version", "tax_schedule_entries", type_="unique"
    )
    op.drop_constraint(
        "uq_tax_schedule_versions_id_tenant_source", "tax_schedule_versions", type_="unique"
    )
    op.drop_constraint("uq_tax_schedule_sources_id_tenant", "tax_schedule_sources", type_="unique")
    op.drop_constraint(
        "uq_client_obligations_id_tenant_client", "client_obligations", type_="unique"
    )
    op.drop_constraint("uq_clients_id_tenant", "clients", type_="unique")
