"""Producto M3: aceptacion real controlada de destinos SOL (autorizaciones acotadas, sin habilitacion global).

* `sol_destination_acceptances`: una autorizacion = un propietario, un cliente, un perfil de credencial, un destino, un solo uso y un plazo corto.
* `sol_access_sessions.acceptance_grant_id`: la sesion que consumio la autorizacion (NULL para el flujo normal con destinos ya validados).
Ninguna fila existente se modifica. El retroceso se bloquea si existen autorizaciones o sesiones de aceptacion (no se destruye evidencia).

Revision ID: 0024
Revises: 0023
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sol_destination_acceptances",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("destination", sa.String(length=30), nullable=False),
        sa.Column("authorized_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("result", sa.String(length=4), nullable=True),
        sa.Column("result_recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_note", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["client_id", "tenant_id"], ["clients.id", "clients.tenant_id"], name="fk_sol_dest_acceptance_client_tenant"),
        sa.ForeignKeyConstraint(
            ["credential_id", "tenant_id", "client_id"],
            ["client_sol_credentials.id", "client_sol_credentials.tenant_id", "client_sol_credentials.client_id"],
            name="fk_sol_dest_acceptance_credential_client_tenant",
        ),
        sa.ForeignKeyConstraint(["authorized_by_user_id", "tenant_id"], ["users.id", "users.tenant_id"], name="fk_sol_dest_acceptance_user_tenant"),
        sa.CheckConstraint("status IN ('AUTHORIZED', 'CONSUMED', 'REVOKED', 'EXPIRED')", name="ck_sol_dest_acceptance_status"),
        sa.CheckConstraint("result IS NULL OR result IN ('PASS', 'FAIL')", name="ck_sol_dest_acceptance_result"),
        sa.CheckConstraint("result IS NULL OR status = 'CONSUMED'", name="ck_sol_dest_acceptance_result_after_consumption"),
        sa.CheckConstraint("(status = 'CONSUMED') = (consumed_at IS NOT NULL AND session_id IS NOT NULL)", name="ck_sol_dest_acceptance_consumption_coherence"),
        sa.CheckConstraint("version >= 1", name="ck_sol_dest_acceptance_version_positive"),
    )
    op.create_index(
        "ux_sol_dest_acceptance_one_authorized", "sol_destination_acceptances", ["tenant_id", "client_id", "credential_id", "destination"],
        unique=True, postgresql_where=sa.text("status = 'AUTHORIZED'"),
    )
    op.create_index("ix_sol_dest_acceptance_client", "sol_destination_acceptances", ["tenant_id", "client_id", "created_at"])
    op.add_column("sol_access_sessions", sa.Column("acceptance_grant_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_sol_access_sessions_acceptance_grant", "sol_access_sessions", "sol_destination_acceptances", ["acceptance_grant_id"], ["id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(text("SELECT (SELECT count(*) FROM sol_destination_acceptances) + (SELECT count(*) FROM sol_access_sessions WHERE acceptance_grant_id IS NOT NULL)")).scalar_one()
    if rows:
        raise RuntimeError(f"downgrade 0024 bloqueado: existen {rows} registros de aceptacion (evidencia que no se destruye)")
    op.drop_constraint("fk_sol_access_sessions_acceptance_grant", "sol_access_sessions", type_="foreignkey")
    op.drop_column("sol_access_sessions", "acceptance_grant_id")
    op.drop_index("ix_sol_dest_acceptance_client", table_name="sol_destination_acceptances")
    op.drop_index("ux_sol_dest_acceptance_one_authorized", table_name="sol_destination_acceptances")
    op.drop_table("sol_destination_acceptances")
