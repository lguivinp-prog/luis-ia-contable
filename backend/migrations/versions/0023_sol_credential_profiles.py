"""Producto M3: perfiles de credencial SOL (varias credenciales cifradas por cliente: principal y secundarias con autorizaciones distintas).

Hasta 0022 el indice parcial `ux_client_sol_credentials_active_per_client` limitaba a UNA credencial no deshabilitada por cliente. Ahora:
* `profile_name` (nombre interno del perfil; NO es secreto) con las filas existentes conservadas (la credencial secundaria actual NO se toca: mismo
  id, mismo cifrado, mismo estado; solo recibe el nombre de perfil por defecto segun su tipo).
* unicidad por (tenant, cliente, nombre de perfil en minusculas) entre las no deshabilitadas;
* a lo sumo UNA credencial principal (PRIMARY_*) no deshabilitada por cliente; las secundarias pueden ser varias.
El retroceso se bloquea si algun cliente tiene mas de una credencial no deshabilitada (no se destruye ningun perfil).

Revision ID: 0023
Revises: 0022
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("client_sol_credentials", sa.Column("profile_name", sa.String(length=60), nullable=True))
    op.execute(text(
        "UPDATE client_sol_credentials SET profile_name = CASE WHEN credential_kind = 'SECONDARY' THEN 'Credencial secundaria' ELSE 'Credencial principal' END "
        "WHERE profile_name IS NULL"
    ))
    op.alter_column("client_sol_credentials", "profile_name", nullable=False)
    op.create_check_constraint("ck_client_sol_credentials_profile_name_not_blank", "client_sol_credentials", "length(btrim(profile_name)) > 0")
    op.drop_index("ux_client_sol_credentials_active_per_client", table_name="client_sol_credentials")
    op.create_index(
        "ux_client_sol_credentials_active_profile", "client_sol_credentials", ["tenant_id", "client_id", sa.text("lower(profile_name)")],
        unique=True, postgresql_where=sa.text("status <> 'DISABLED'"),
    )
    op.create_index(
        "ux_client_sol_credentials_one_primary", "client_sol_credentials", ["tenant_id", "client_id"],
        unique=True, postgresql_where=sa.text("status <> 'DISABLED' AND credential_kind <> 'SECONDARY'"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    many = bind.execute(text(
        "SELECT count(*) FROM (SELECT 1 FROM client_sol_credentials WHERE status <> 'DISABLED' GROUP BY tenant_id, client_id HAVING count(*) > 1) t"
    )).scalar_one()
    if many:
        raise RuntimeError(f"downgrade 0023 bloqueado: {many} cliente(s) tienen mas de una credencial no deshabilitada")
    op.drop_index("ux_client_sol_credentials_one_primary", table_name="client_sol_credentials")
    op.drop_index("ux_client_sol_credentials_active_profile", table_name="client_sol_credentials")
    op.create_index(
        "ux_client_sol_credentials_active_per_client", "client_sol_credentials", ["tenant_id", "client_id"],
        unique=True, postgresql_where=sa.text("status <> 'DISABLED'"),
    )
    op.drop_constraint("ck_client_sol_credentials_profile_name_not_blank", "client_sol_credentials", type_="check")
    op.drop_column("client_sol_credentials", "profile_name")
