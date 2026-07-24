"""Filing reference tracked in history: filing_history.filing_reference

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-24

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `declarations.filing_reference` existe desde 3.1 pero nunca se
    # reflejaba en el historial de presentacion (a diferencia de
    # presentation_date/order_number, que si se mirroreaban desde el
    # inicio). Se agrega aqui para que el flujo completo de presentacion
    # (Bloque 3.2, correccion) pueda auditar tambien este campo.
    op.add_column("filing_history", sa.Column("filing_reference", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("filing_history", "filing_reference")
