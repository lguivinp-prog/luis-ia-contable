"""Producto M1: estados operativos MANUALES por cliente y periodo (ver docs/COCKPIT_MULTI_RUC.md).
2 tablas nuevas: `client_period_operations` (una fila por tenant/cliente/periodo, creada de forma perezosa) y
`client_period_operation_history` (historial compacto append-only de cada cambio manual).

Solo se persiste lo que NO se puede derivar: Buzon, Contabilidad y Comunicacion al cliente. Documentos, SIRE, declaraciones,
impuesto, honorarios, vencimientos, riesgo y fase siguen siendo DERIVADOS.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-07

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BUZON = ("NOT_REVIEWED", "REVIEWED_CLEAR", "REVIEWED_ACTION_REQUIRED", "NOT_APPLICABLE")
_ACCOUNTING = ("NOT_STARTED", "IN_PROGRESS", "REVIEWED", "COMPLETED", "NOT_APPLICABLE")
_COMMUNICATION = ("NOT_STARTED", "PREPARED", "SENT", "NOT_APPLICABLE")
_FIELDS = ("buzon_status", "accounting_status", "client_communication_status", "notes")


def _in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_table(
        "client_period_operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tax_period_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("buzon_status", sa.String(30), nullable=False, server_default="NOT_REVIEWED"),
        sa.Column("accounting_status", sa.String(20), nullable=False, server_default="NOT_STARTED"),
        sa.Column("client_communication_status", sa.String(20), nullable=False, server_default="NOT_STARTED"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("row_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "client_id", "tax_period_id", name="uq_client_period_operations_scope"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_client_period_operations_id_tenant"),
        sa.UniqueConstraint(
            "id", "tenant_id", "client_id", "tax_period_id", name="uq_client_period_operations_id_scope"
        ),
        sa.CheckConstraint(f"buzon_status IN {_in(_BUZON)}", name="ck_client_period_operations_buzon_status"),
        sa.CheckConstraint(
            f"accounting_status IN {_in(_ACCOUNTING)}", name="ck_client_period_operations_accounting_status"
        ),
        sa.CheckConstraint(
            f"client_communication_status IN {_in(_COMMUNICATION)}",
            name="ck_client_period_operations_communication_status",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_client_period_operations_row_version_positive"),
        sa.CheckConstraint(
            "notes IS NULL OR length(notes) <= 2000", name="ck_client_period_operations_notes_length"
        ),
        sa.ForeignKeyConstraint(
            ["client_id", "tenant_id"], ["clients.id", "clients.tenant_id"],
            name="fk_client_period_operations_client_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tax_period_id", "tenant_id"], ["tax_periods.id", "tax_periods.tenant_id"],
            name="fk_client_period_operations_period_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_client_period_operations_updated_by_tenant",
        ),
    )
    op.create_index(
        "ix_client_period_operations_tenant_period", "client_period_operations", ["tenant_id", "tax_period_id"]
    )

    op.create_table(
        "client_period_operation_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tax_period_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_name", sa.String(40), nullable=False),
        sa.Column("from_value", sa.Text, nullable=True),
        sa.Column("to_value", sa.Text, nullable=True),
        sa.Column("row_version_after", sa.Integer, nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(f"field_name IN {_in(_FIELDS)}", name="ck_client_period_operation_history_field"),
        sa.CheckConstraint("row_version_after >= 1", name="ck_client_period_operation_history_version_positive"),
        sa.ForeignKeyConstraint(
            ["operation_id", "tenant_id", "client_id", "tax_period_id"],
            [
                "client_period_operations.id",
                "client_period_operations.tenant_id",
                "client_period_operations.client_id",
                "client_period_operations.tax_period_id",
            ],
            name="fk_client_period_operation_history_scope",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_client_period_operation_history_actor_tenant",
        ),
    )
    op.create_index(
        "ix_client_period_operation_history_scope_created",
        "client_period_operation_history",
        ["tenant_id", "client_id", "tax_period_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_client_period_operation_history_scope_created", table_name="client_period_operation_history")
    op.drop_table("client_period_operation_history")
    op.drop_index("ix_client_period_operations_tenant_period", table_name="client_period_operations")
    op.drop_table("client_period_operations")
