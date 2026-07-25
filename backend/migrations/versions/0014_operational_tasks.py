"""Gestion operativa de tareas, responsables, agenda interna y alertas
(Fase 2, Bloque 1): 4 tablas nuevas (task_generation_runs, operational_tasks,
task_change_history, task_comments), 11 FK compuestas + 7 CHECK en
operational_tasks, 4 constraints auxiliares en tablas preexistentes, 2
UNIQUE(id, tenant_id) internas (ver ADR-0057 a ADR-0061).

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-24

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TASK_TYPE_VALUES = (
    "DECLARATION_FILING",
    "DOCUMENT_COLLECTION",
    "TAX_PAYMENT_FOLLOWUP",
    "FEE_COLLECTION",
    "CLIENT_FOLLOWUP",
    "INTERNAL_REVIEW",
    "GENERAL",
)
_TASK_STATUS_VALUES = ("PENDING", "IN_PROGRESS", "COMPLETED", "CANCELLED")
_TASK_PRIORITY_VALUES = ("LOW", "NORMAL", "HIGH", "URGENT")
_TRIGGER_SOURCE_VALUES = ("MANUAL", "LOGIN", "AGENDA_OPEN", "DASHBOARD_OPEN", "CLI")
_EVENT_TYPE_VALUES = (
    "CREATED",
    "UPDATED",
    "STARTED",
    "COMPLETED",
    "CANCELLED",
    "ASSIGNED",
    "RESCHEDULED",
    "SOURCE_DUE_UPDATED",
    "SOURCE_COMPLETED",
    "SOURCE_CANCELLED",
)

_FEE_PAIR_COHERENCE_CHECK = (
    "(professional_fee_charge_id IS NULL AND professional_fee_agreement_id IS NULL) "
    "OR (professional_fee_charge_id IS NOT NULL AND professional_fee_agreement_id IS NOT NULL)"
)

_SOURCE_SHAPE_CHECK = """
(
  (declaration_id IS NULL AND declaration_document_item_id IS NULL
   AND professional_fee_charge_id IS NULL AND professional_fee_agreement_id IS NULL
   AND tax_payment_id IS NULL)
  OR (declaration_id IS NOT NULL AND client_id IS NOT NULL
      AND declaration_document_item_id IS NULL AND professional_fee_charge_id IS NULL
      AND professional_fee_agreement_id IS NULL AND tax_payment_id IS NULL)
  OR (declaration_document_item_id IS NOT NULL AND declaration_id IS NOT NULL
      AND client_id IS NOT NULL AND professional_fee_charge_id IS NULL
      AND professional_fee_agreement_id IS NULL AND tax_payment_id IS NULL)
  OR (professional_fee_charge_id IS NOT NULL AND professional_fee_agreement_id IS NOT NULL
      AND client_id IS NOT NULL AND declaration_id IS NULL
      AND declaration_document_item_id IS NULL AND tax_payment_id IS NULL)
  OR (tax_payment_id IS NOT NULL AND declaration_id IS NOT NULL AND client_id IS NOT NULL
      AND declaration_document_item_id IS NULL AND professional_fee_charge_id IS NULL
      AND professional_fee_agreement_id IS NULL)
)
""".strip()

_CLIENT_REQUIRED_WITH_ANCHOR_CHECK = (
    "(declaration_id IS NULL AND declaration_document_item_id IS NULL AND tax_payment_id IS NULL) "
    "OR client_id IS NOT NULL"
)

_GENERATED_SOURCE_MATCHES_TYPE_CHECK = """
(
  is_system_generated = false
  OR (task_type = 'DECLARATION_FILING' AND declaration_id IS NOT NULL AND client_id IS NOT NULL
      AND declaration_document_item_id IS NULL AND professional_fee_charge_id IS NULL
      AND professional_fee_agreement_id IS NULL AND tax_payment_id IS NULL)
  OR (task_type = 'DOCUMENT_COLLECTION' AND declaration_document_item_id IS NOT NULL
      AND declaration_id IS NOT NULL AND client_id IS NOT NULL
      AND professional_fee_charge_id IS NULL AND professional_fee_agreement_id IS NULL
      AND tax_payment_id IS NULL)
  OR (task_type = 'FEE_COLLECTION' AND professional_fee_charge_id IS NOT NULL
      AND professional_fee_agreement_id IS NOT NULL AND client_id IS NOT NULL
      AND declaration_id IS NULL AND declaration_document_item_id IS NULL
      AND tax_payment_id IS NULL)
)
""".strip()

_GENERATION_COHERENCE_CHECK = """
(is_system_generated = true AND generation_key IS NOT NULL AND generation_run_id IS NOT NULL
 AND created_by IS NULL)
OR (is_system_generated = false AND generation_key IS NULL AND generation_run_id IS NULL
    AND created_by IS NOT NULL)
""".strip()

_SOURCE_DUE_COHERENCE_CHECK = """
(
  (is_system_generated = false AND source_due_at IS NULL AND due_is_overridden = false)
  OR
  (is_system_generated = true AND source_due_at IS NOT NULL
   AND (
     (due_is_overridden = false AND due_at = source_due_at AND due_has_time = false)
     OR (due_is_overridden = true)
   ))
)
""".strip()

_STATUS_RESOLUTION_COHERENCE_CHECK = """
(
  (status IN ('PENDING','IN_PROGRESS') AND completed_at IS NULL AND completed_by IS NULL
   AND cancelled_at IS NULL AND cancelled_by IS NULL AND cancellation_reason IS NULL
   AND resolution_origin IS NULL)
  OR (status = 'COMPLETED' AND completed_at IS NOT NULL AND completed_by IS NOT NULL
      AND resolution_origin = 'MANUAL' AND cancelled_at IS NULL AND cancelled_by IS NULL
      AND cancellation_reason IS NULL)
  OR (status = 'COMPLETED' AND completed_at IS NOT NULL AND completed_by IS NULL
      AND resolution_origin = 'SYSTEM' AND is_system_generated = true
      AND cancelled_at IS NULL AND cancelled_by IS NULL AND cancellation_reason IS NULL)
  OR (status = 'CANCELLED' AND cancelled_at IS NOT NULL AND cancelled_by IS NOT NULL
      AND cancellation_reason IS NOT NULL AND resolution_origin = 'MANUAL'
      AND completed_at IS NULL AND completed_by IS NULL)
  OR (status = 'CANCELLED' AND cancelled_at IS NOT NULL AND cancelled_by IS NULL
      AND cancellation_reason IS NOT NULL AND resolution_origin = 'SYSTEM'
      AND is_system_generated = true AND completed_at IS NULL AND completed_by IS NULL)
)
""".strip()


def upgrade() -> None:
    # --- task_generation_runs ---
    op.create_table(
        "task_generation_runs",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("triggered_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trigger_source", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", postgresql.JSONB, nullable=False),
        sa.UniqueConstraint("id", "tenant_id", name="uq_task_generation_runs_id_tenant"),
        sa.CheckConstraint(
            f"trigger_source IN {_TRIGGER_SOURCE_VALUES}",
            name="ck_task_generation_runs_trigger_source",
        ),
        sa.CheckConstraint(
            "(trigger_source = 'CLI' AND triggered_by IS NULL) "
            "OR (trigger_source IN ('MANUAL','LOGIN','AGENDA_OPEN','DASHBOARD_OPEN') "
            "AND triggered_by IS NOT NULL)",
            name="ck_task_generation_runs_trigger_coherence",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="ck_task_generation_runs_completed_after_started",
        ),
        sa.ForeignKeyConstraint(
            ["triggered_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_task_generation_runs_triggered_by_tenant",
        ),
    )
    op.create_index(
        "ix_task_generation_runs_tenant_started",
        "task_generation_runs",
        ["tenant_id", "started_at"],
    )

    # --- constraints auxiliares en tablas preexistentes (no destructivas) ---
    op.create_unique_constraint(
        "uq_declaration_document_items_id_tenant_client_declaration",
        "declaration_document_items",
        ["id", "tenant_id", "client_id", "declaration_id"],
    )
    op.create_unique_constraint(
        "uq_tax_payments_id_tenant_declaration",
        "tax_payments",
        ["id", "tenant_id", "declaration_id"],
    )
    op.create_unique_constraint(
        "uq_professional_fee_charges_id_tenant_agreement",
        "professional_fee_charges",
        ["id", "tenant_id", "agreement_id"],
    )
    op.create_unique_constraint(
        "uq_professional_fee_agreements_id_tenant_client",
        "professional_fee_agreements",
        ["id", "tenant_id", "client_id"],
    )

    # --- operational_tasks ---
    op.create_table(
        "operational_tasks",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("declaration_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("declaration_document_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("professional_fee_charge_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("professional_fee_agreement_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tax_payment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("task_type", sa.String(30), nullable=False),
        sa.Column("priority", sa.String(10), nullable=False, server_default="NORMAL"),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("assigned_to", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_has_time", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("source_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_is_overridden", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cancellation_reason", sa.Text, nullable=True),
        sa.Column("resolution_origin", sa.String(10), nullable=True),
        sa.Column("generation_key", sa.String(255), nullable=True),
        sa.Column("is_system_generated", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("generation_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("id", "tenant_id", name="uq_operational_tasks_id_tenant"),
        sa.CheckConstraint(f"task_type IN {_TASK_TYPE_VALUES}", name="ck_operational_tasks_task_type"),
        sa.CheckConstraint(f"status IN {_TASK_STATUS_VALUES}", name="ck_operational_tasks_status"),
        sa.CheckConstraint(f"priority IN {_TASK_PRIORITY_VALUES}", name="ck_operational_tasks_priority"),
        sa.CheckConstraint("version >= 1", name="ck_operational_tasks_version_positive"),
        sa.CheckConstraint(_FEE_PAIR_COHERENCE_CHECK, name="ck_operational_tasks_fee_pair_coherence"),
        sa.CheckConstraint(_SOURCE_SHAPE_CHECK, name="ck_operational_tasks_source_shape"),
        sa.CheckConstraint(
            _CLIENT_REQUIRED_WITH_ANCHOR_CHECK,
            name="ck_operational_tasks_client_required_with_anchor",
        ),
        sa.CheckConstraint(
            _GENERATED_SOURCE_MATCHES_TYPE_CHECK,
            name="ck_operational_tasks_generated_source_matches_type",
        ),
        sa.CheckConstraint(
            _GENERATION_COHERENCE_CHECK, name="ck_operational_tasks_generation_coherence"
        ),
        sa.CheckConstraint(
            _SOURCE_DUE_COHERENCE_CHECK, name="ck_operational_tasks_source_due_coherence"
        ),
        sa.CheckConstraint(
            _STATUS_RESOLUTION_COHERENCE_CHECK,
            name="ck_operational_tasks_status_resolution_coherence",
        ),
        sa.CheckConstraint(
            "starts_at IS NULL OR starts_at <= due_at",
            name="ck_operational_tasks_starts_before_due",
        ),
        sa.ForeignKeyConstraint(
            ["client_id", "tenant_id"], ["clients.id", "clients.tenant_id"],
            name="fk_operational_tasks_client_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["declaration_id", "tenant_id", "client_id"],
            ["declarations.id", "declarations.tenant_id", "declarations.client_id"],
            name="fk_operational_tasks_declaration_tenant_client",
        ),
        sa.ForeignKeyConstraint(
            ["declaration_document_item_id", "tenant_id", "client_id", "declaration_id"],
            [
                "declaration_document_items.id",
                "declaration_document_items.tenant_id",
                "declaration_document_items.client_id",
                "declaration_document_items.declaration_id",
            ],
            name="fk_operational_tasks_document_item_tenant_client_declaration",
        ),
        sa.ForeignKeyConstraint(
            ["professional_fee_charge_id", "tenant_id", "professional_fee_agreement_id"],
            [
                "professional_fee_charges.id",
                "professional_fee_charges.tenant_id",
                "professional_fee_charges.agreement_id",
            ],
            name="fk_operational_tasks_fee_charge_tenant_agreement",
        ),
        sa.ForeignKeyConstraint(
            ["professional_fee_agreement_id", "tenant_id", "client_id"],
            [
                "professional_fee_agreements.id",
                "professional_fee_agreements.tenant_id",
                "professional_fee_agreements.client_id",
            ],
            name="fk_operational_tasks_fee_agreement_tenant_client",
        ),
        sa.ForeignKeyConstraint(
            ["tax_payment_id", "tenant_id", "declaration_id"],
            ["tax_payments.id", "tax_payments.tenant_id", "tax_payments.declaration_id"],
            name="fk_operational_tasks_tax_payment_tenant_declaration",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_operational_tasks_assigned_to_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["created_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_operational_tasks_created_by_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["completed_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_operational_tasks_completed_by_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_operational_tasks_cancelled_by_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["generation_run_id", "tenant_id"],
            ["task_generation_runs.id", "task_generation_runs.tenant_id"],
            name="fk_operational_tasks_generation_run_tenant",
        ),
    )
    op.create_index(
        "uq_operational_tasks_tenant_generation_key",
        "operational_tasks",
        ["tenant_id", "generation_key"],
        unique=True,
        postgresql_where=sa.text("generation_key IS NOT NULL"),
    )
    op.create_index("ix_operational_tasks_tenant_status", "operational_tasks", ["tenant_id", "status"])
    op.create_index("ix_operational_tasks_tenant_due_at", "operational_tasks", ["tenant_id", "due_at"])
    op.create_index(
        "ix_operational_tasks_tenant_assigned_to", "operational_tasks", ["tenant_id", "assigned_to"]
    )
    op.create_index("ix_operational_tasks_tenant_client", "operational_tasks", ["tenant_id", "client_id"])
    op.create_index(
        "ix_operational_tasks_tenant_priority", "operational_tasks", ["tenant_id", "priority"]
    )

    # --- task_change_history ---
    op.create_table(
        "task_change_history",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("origin", sa.String(10), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=True),
        sa.Column("from_assigned_to", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("to_assigned_to", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("from_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("to_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("from_priority", sa.String(10), nullable=True),
        sa.Column("to_priority", sa.String(10), nullable=True),
        sa.Column("from_source_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("to_source_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("from_due_is_overridden", sa.Boolean, nullable=True),
        sa.Column("to_due_is_overridden", sa.Boolean, nullable=True),
        sa.Column("from_due_has_time", sa.Boolean, nullable=True),
        sa.Column("to_due_has_time", sa.Boolean, nullable=True),
        sa.Column("changes_json", postgresql.JSONB, nullable=True),
        sa.CheckConstraint(
            f"event_type IN {_EVENT_TYPE_VALUES}", name="ck_task_change_history_event_type"
        ),
        sa.CheckConstraint(
            "(origin = 'SYSTEM' AND actor_user_id IS NULL) "
            "OR (origin = 'MANUAL' AND actor_user_id IS NOT NULL)",
            name="ck_task_change_history_origin_actor_coherence",
        ),
        sa.CheckConstraint(
            "from_status IS NOT NULL OR to_status IS NOT NULL OR "
            "from_assigned_to IS NOT NULL OR to_assigned_to IS NOT NULL OR "
            "from_due_at IS NOT NULL OR to_due_at IS NOT NULL OR "
            "from_priority IS NOT NULL OR to_priority IS NOT NULL OR "
            "from_source_due_at IS NOT NULL OR to_source_due_at IS NOT NULL OR "
            "from_due_is_overridden IS NOT NULL OR to_due_is_overridden IS NOT NULL OR "
            "from_due_has_time IS NOT NULL OR to_due_has_time IS NOT NULL OR "
            "(changes_json IS NOT NULL AND changes_json <> '{}'::jsonb)",
            name="ck_task_change_history_non_empty",
        ),
        sa.CheckConstraint(
            "event_type <> 'CREATED' OR "
            "(to_status IS NOT NULL AND to_due_at IS NOT NULL AND to_priority IS NOT NULL)",
            name="ck_tch_created_complete",
        ),
        sa.CheckConstraint(
            "event_type NOT IN ('STARTED','COMPLETED','CANCELLED') OR "
            "(from_status IS NOT NULL AND to_status IS NOT NULL AND from_status IS DISTINCT FROM to_status)",
            name="ck_tch_status_events_have_status",
        ),
        sa.CheckConstraint(
            "event_type <> 'ASSIGNED' OR from_assigned_to IS DISTINCT FROM to_assigned_to",
            name="ck_tch_assigned_has_change",
        ),
        sa.CheckConstraint(
            "event_type <> 'RESCHEDULED' OR (from_due_at IS DISTINCT FROM to_due_at "
            "OR from_due_is_overridden IS DISTINCT FROM to_due_is_overridden "
            "OR from_due_has_time IS DISTINCT FROM to_due_has_time)",
            name="ck_tch_rescheduled_has_change",
        ),
        sa.CheckConstraint(
            "event_type <> 'SOURCE_DUE_UPDATED' OR from_source_due_at IS DISTINCT FROM to_source_due_at",
            name="ck_tch_source_due_has_change",
        ),
        sa.CheckConstraint(
            "event_type NOT IN ('SOURCE_COMPLETED','SOURCE_CANCELLED') OR "
            "(from_status IS NOT NULL AND to_status IS NOT NULL AND from_status IS DISTINCT FROM to_status)",
            name="ck_tch_source_resolution_has_status",
        ),
        sa.CheckConstraint(
            "event_type <> 'UPDATED' OR from_priority IS DISTINCT FROM to_priority "
            "OR (changes_json IS NOT NULL AND changes_json <> '{}'::jsonb)",
            name="ck_tch_updated_has_content_change",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "tenant_id"], ["operational_tasks.id", "operational_tasks.tenant_id"],
            name="fk_task_change_history_task_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_task_change_history_actor_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["from_assigned_to", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_task_change_history_from_assigned_to_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["to_assigned_to", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_task_change_history_to_assigned_to_tenant",
        ),
    )
    op.create_index(
        "ix_task_change_history_tenant_task", "task_change_history", ["tenant_id", "task_id"]
    )
    op.create_index(
        "ix_task_change_history_tenant_created", "task_change_history", ["tenant_id", "created_at"]
    )

    # --- task_comments ---
    op.create_table(
        "task_comments",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("author_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(body)) BETWEEN 1 AND 4000", name="ck_task_comments_body_length"),
        sa.ForeignKeyConstraint(
            ["task_id", "tenant_id"], ["operational_tasks.id", "operational_tasks.tenant_id"],
            name="fk_task_comments_task_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["author_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_task_comments_author_tenant",
        ),
    )
    op.create_index("ix_task_comments_tenant_task", "task_comments", ["tenant_id", "task_id"])


def downgrade() -> None:
    op.drop_index("ix_task_comments_tenant_task", table_name="task_comments")
    op.drop_table("task_comments")

    op.drop_index("ix_task_change_history_tenant_created", table_name="task_change_history")
    op.drop_index("ix_task_change_history_tenant_task", table_name="task_change_history")
    op.drop_table("task_change_history")

    op.drop_index("ix_operational_tasks_tenant_priority", table_name="operational_tasks")
    op.drop_index("ix_operational_tasks_tenant_client", table_name="operational_tasks")
    op.drop_index("ix_operational_tasks_tenant_assigned_to", table_name="operational_tasks")
    op.drop_index("ix_operational_tasks_tenant_due_at", table_name="operational_tasks")
    op.drop_index("ix_operational_tasks_tenant_status", table_name="operational_tasks")
    op.drop_index(
        "uq_operational_tasks_tenant_generation_key",
        table_name="operational_tasks",
    )
    op.drop_table("operational_tasks")

    op.drop_constraint(
        "uq_professional_fee_agreements_id_tenant_client", "professional_fee_agreements", type_="unique"
    )
    op.drop_constraint(
        "uq_professional_fee_charges_id_tenant_agreement", "professional_fee_charges", type_="unique"
    )
    op.drop_constraint("uq_tax_payments_id_tenant_declaration", "tax_payments", type_="unique")
    op.drop_constraint(
        "uq_declaration_document_items_id_tenant_client_declaration",
        "declaration_document_items",
        type_="unique",
    )

    op.drop_index("ix_task_generation_runs_tenant_started", table_name="task_generation_runs")
    op.drop_table("task_generation_runs")
