"""Producto M2: navegacion supervisada a destinos de SOL validados + Ficha RUC + alta asistida.

- `sol_access_sessions`: columnas `destination`, `extract_ficha`, `human_control_at`; scope `NAVIGATE_TO_DESTINATION`,
  estado NO terminal `HUMAN_CONTROL` (la automatizacion termino y el navegador sigue abierto bajo control humano) y
  resultado `DESTINATION_REACHED`. CHECKs de enum y coherencia scope/destino/extraccion en la BD.
- `client_ficha_ruc_snapshots`: resultado ESTRUCTURADO de la Ficha (nunca HTML/DOM/capturas) + diff + revision humana.
- `client_onboardings`: alta asistida por SUNAT sobre un cliente PROVISIONAL (`prospecto`).

Reversible: el downgrade normaliza las filas nuevas a los valores previos (sesiones con destino pasan a `SUCCEEDED`/
`LANDING_REACHED`/scope de aterrizaje) y elimina las tablas nuevas.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-07

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_STATUS = (
    "CREATED", "WAITING_FOR_AGENT", "WAITING_FOR_USER", "IN_PROGRESS", "SUCCEEDED", "PARTIAL_FAILURE", "FAILED",
    "CANCELLED", "EXPIRED",
)
_NEW_STATUS = (
    "CREATED", "WAITING_FOR_AGENT", "WAITING_FOR_USER", "IN_PROGRESS", "HUMAN_CONTROL", "SUCCEEDED", "PARTIAL_FAILURE",
    "FAILED", "CANCELLED", "EXPIRED",
)
_NEW_SCOPE = ("AUTHENTICATE_TO_LANDING_ONLY", "NAVIGATE_TO_DESTINATION")
_NEW_RESULT = ("LANDING_REACHED", "DESTINATION_REACHED")
_DESTINATIONS = ("MENU_SOL", "FICHA_RUC")
_PURPOSE = ("ONBOARDING", "REFRESH")
_REVIEW = ("PENDING_REVIEW", "ACCEPTED", "REJECTED", "SUPERSEDED")
_ONBOARDING = ("IN_PROGRESS", "AWAITING_REVIEW", "COMPLETED", "CANCELLED", "FAILED")


def _in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.add_column("sol_access_sessions", sa.Column("destination", sa.String(30), nullable=True))
    op.add_column(
        "sol_access_sessions", sa.Column("extract_ficha", sa.Boolean, nullable=False, server_default=sa.false())
    )
    op.add_column("sol_access_sessions", sa.Column("human_control_at", sa.DateTime(timezone=True), nullable=True))

    for name in ("ck_sol_access_sessions_scope", "ck_sol_access_sessions_status", "ck_sol_access_sessions_operation_result"):
        op.drop_constraint(name, "sol_access_sessions", type_="check")
    op.create_check_constraint("ck_sol_access_sessions_scope", "sol_access_sessions", f"scope IN {_in(_NEW_SCOPE)}")
    op.create_check_constraint("ck_sol_access_sessions_status", "sol_access_sessions", f"status IN {_in(_NEW_STATUS)}")
    op.create_check_constraint(
        "ck_sol_access_sessions_operation_result",
        "sol_access_sessions",
        f"operation_result IS NULL OR operation_result IN {_in(_NEW_RESULT)}",
    )
    op.create_check_constraint(
        "ck_sol_access_sessions_destination", "sol_access_sessions", f"destination IS NULL OR destination IN {_in(_DESTINATIONS)}"
    )
    op.create_check_constraint(
        "ck_sol_access_sessions_scope_destination_coherence",
        "sol_access_sessions",
        "(scope = 'NAVIGATE_TO_DESTINATION') = (destination IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_sol_access_sessions_extract_ficha_destination",
        "sol_access_sessions",
        "NOT extract_ficha OR destination = 'FICHA_RUC'",
    )

    op.create_table(
        "client_ficha_ruc_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.String(12), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="SUNAT_FICHA_RUC"),
        sa.Column("schema_version", sa.Integer, nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fields", postgresql.JSONB, nullable=False),
        sa.Column("malformed", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("diff", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("review_status", sa.String(16), nullable=False, server_default="PENDING_REVIEW"),
        sa.Column("accepted_fields", postgresql.JSONB, nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_client_ficha_ruc_snapshots_id_tenant"),
        sa.UniqueConstraint("session_id", name="uq_client_ficha_ruc_snapshots_session"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(
            ["client_id", "tenant_id"], ["clients.id", "clients.tenant_id"], name="fk_client_ficha_ruc_snapshots_client_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["sol_access_sessions.id", "sol_access_sessions.tenant_id"],
            name="fk_client_ficha_ruc_snapshots_session_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by", "tenant_id"], ["users.id", "users.tenant_id"], name="fk_client_ficha_ruc_snapshots_reviewer_tenant"
        ),
        sa.CheckConstraint("source = 'SUNAT_FICHA_RUC'", name="ck_client_ficha_ruc_snapshots_source"),
        sa.CheckConstraint(f"purpose IN {_in(_PURPOSE)}", name="ck_client_ficha_ruc_snapshots_purpose"),
        sa.CheckConstraint(f"review_status IN {_in(_REVIEW)}", name="ck_client_ficha_ruc_snapshots_review_status"),
        sa.CheckConstraint("schema_version >= 1", name="ck_client_ficha_ruc_snapshots_schema_version"),
        sa.CheckConstraint("version >= 1", name="ck_client_ficha_ruc_snapshots_version_positive"),
        sa.CheckConstraint(
            "(review_status IN ('ACCEPTED', 'REJECTED')) = (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_client_ficha_ruc_snapshots_review_coherence",
        ),
    )
    op.create_index(
        "ix_client_ficha_ruc_snapshots_tenant_client", "client_ficha_ruc_snapshots", ["tenant_id", "client_id", "retrieved_at"]
    )
    op.create_index(
        "ux_client_ficha_ruc_snapshots_pending_per_client",
        "client_ficha_ruc_snapshots",
        ["tenant_id", "client_id"],
        unique=True,
        postgresql_where=sa.text("review_status = 'PENDING_REVIEW'"),
    )

    op.create_table(
        "client_onboardings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="IN_PROGRESS"),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(
            ["client_id", "tenant_id"], ["clients.id", "clients.tenant_id"], name="fk_client_onboardings_client_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["created_by", "tenant_id"], ["users.id", "users.tenant_id"], name="fk_client_onboardings_creator_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "tenant_id"],
            ["client_ficha_ruc_snapshots.id", "client_ficha_ruc_snapshots.tenant_id"],
            name="fk_client_onboardings_snapshot_tenant",
        ),
        sa.CheckConstraint(f"status IN {_in(_ONBOARDING)}", name="ck_client_onboardings_status"),
        sa.CheckConstraint("version >= 1", name="ck_client_onboardings_version_positive"),
    )
    op.create_index("ix_client_onboardings_tenant_client", "client_onboardings", ["tenant_id", "client_id"])
    op.create_index(
        "ux_client_onboardings_active_per_client",
        "client_onboardings",
        ["tenant_id", "client_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('IN_PROGRESS', 'AWAITING_REVIEW')"),
    )


def downgrade() -> None:
    op.drop_table("client_onboardings")
    op.drop_table("client_ficha_ruc_snapshots")

    for name in (
        "ck_sol_access_sessions_extract_ficha_destination",
        "ck_sol_access_sessions_scope_destination_coherence",
        "ck_sol_access_sessions_destination",
        "ck_sol_access_sessions_operation_result",
        "ck_sol_access_sessions_status",
        "ck_sol_access_sessions_scope",
    ):
        op.drop_constraint(name, "sol_access_sessions", type_="check")
    # Normaliza las filas nuevas a los valores previos antes de restaurar los CHECKs originales.
    op.execute("UPDATE sol_access_sessions SET status = 'SUCCEEDED' WHERE status = 'HUMAN_CONTROL'")
    op.execute("UPDATE sol_access_sessions SET operation_result = 'LANDING_REACHED' WHERE operation_result = 'DESTINATION_REACHED'")
    op.execute("UPDATE sol_access_sessions SET scope = 'AUTHENTICATE_TO_LANDING_ONLY'")
    op.drop_column("sol_access_sessions", "human_control_at")
    op.drop_column("sol_access_sessions", "extract_ficha")
    op.drop_column("sol_access_sessions", "destination")
    op.create_check_constraint(
        "ck_sol_access_sessions_scope", "sol_access_sessions", "scope = 'AUTHENTICATE_TO_LANDING_ONLY'"
    )
    op.create_check_constraint("ck_sol_access_sessions_status", "sol_access_sessions", f"status IN {_in(_OLD_STATUS)}")
    op.create_check_constraint(
        "ck_sol_access_sessions_operation_result",
        "sol_access_sessions",
        "operation_result IS NULL OR operation_result = 'LANDING_REACHED'",
    )
