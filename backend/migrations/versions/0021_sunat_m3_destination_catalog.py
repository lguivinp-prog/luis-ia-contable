"""Producto M3: el CHECK de `sol_access_sessions.destination` admite el catalogo completo de destinos CONOCIDOS.

Los destinos nuevos (SIRE, DECLARACIONES_Y_PAGOS, DECLARA_FACIL, TRAMITES_Y_CONSULTAS) siguen DESHABILITADOS por el backend
(`app.core.sunat_destinations`) hasta contar con evidencia estructural real; la base de datos solo deja de ser un segundo catalogo
desalineado. Ninguna fila existente cambia. `extract_ficha` sigue restringido a FICHA_RUC.

Revision ID: 0021
Revises: 0020
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels = None
depends_on = None

_OLD = ("MENU_SOL", "FICHA_RUC")
_NEW = _OLD + ("SIRE", "DECLARACIONES_Y_PAGOS", "DECLARA_FACIL", "TRAMITES_Y_CONSULTAS")


def _in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.drop_constraint("ck_sol_access_sessions_destination", "sol_access_sessions", type_="check")
    op.create_check_constraint(
        "ck_sol_access_sessions_destination", "sol_access_sessions", f"destination IS NULL OR destination IN {_in(_NEW)}"
    )


def downgrade() -> None:
    bind = op.get_bind()
    extra = bind.execute(text(f"SELECT count(*) FROM sol_access_sessions WHERE destination NOT IN {_in(_OLD)}")).scalar_one()
    if extra:
        # No se destruyen sesiones historicas: el retroceso exige resolverlas explicitamente antes.
        raise RuntimeError(f"downgrade 0021 bloqueado: {extra} sesion(es) usan destinos posteriores a M2")
    op.drop_constraint("ck_sol_access_sessions_destination", "sol_access_sessions", type_="check")
    op.create_check_constraint(
        "ck_sol_access_sessions_destination", "sol_access_sessions", f"destination IS NULL OR destination IN {_in(_OLD)}"
    )
