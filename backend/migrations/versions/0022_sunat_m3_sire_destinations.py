"""Producto M3 (correccion de alcance): el CHECK de `sol_access_sessions.destination` admite los tres destinos SIRE por aplicacion.

SIRE_RVIE (Registro de Ventas), SIRE_RCE (Registro de Compras) y SIRE_GENERACION_REGISTROS (Generacion de Registros Electronicos, solo apertura
supervisada) se CONOCEN pero permanecen DESHABILITADOS en el backend (`app.core.sunat_destinations`) hasta contar con la entrada de menu real.
Ninguna fila existente cambia. El identificador historico `SIRE` se conserva en el CHECK para no invalidar sesiones previas.

Revision ID: 0022
Revises: 0021
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels = None
depends_on = None

_OLD = ("MENU_SOL", "FICHA_RUC", "SIRE", "DECLARACIONES_Y_PAGOS", "DECLARA_FACIL", "TRAMITES_Y_CONSULTAS")
_NEW = _OLD + ("SIRE_RVIE", "SIRE_RCE", "SIRE_GENERACION_REGISTROS")


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
        raise RuntimeError(f"downgrade 0022 bloqueado: {extra} sesion(es) usan destinos SIRE por aplicacion")
    op.drop_constraint("ck_sol_access_sessions_destination", "sol_access_sessions", type_="check")
    op.create_check_constraint(
        "ck_sol_access_sessions_destination", "sol_access_sessions", f"destination IS NULL OR destination IN {_in(_OLD)}"
    )
