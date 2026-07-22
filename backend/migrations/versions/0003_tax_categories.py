"""Tax categories: tax_category_catalog, client_tax_categories (EXCLUDE gist)

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-22

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Requerida por la restriccion de exclusion (igualdad + rango en el mismo
    # indice GIST). No se asume que ya exista.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "tax_category_catalog",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(50), nullable=False, unique=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "client_tax_categories",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column(
            "client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id"), nullable=False
        ),
        sa.Column(
            "category_code",
            sa.String(50),
            sa.ForeignKey("tax_category_catalog.code"),
            nullable=False,
        ),
        sa.Column("valid_from", sa.Date, nullable=False),
        sa.Column("valid_until", sa.Date, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_until >= valid_from",
            name="ck_client_tax_categories_valid_range",
        ),
    )
    # Columna generada (solo PostgreSQL, no mapeada como escribible en el ORM)
    op.execute(
        "ALTER TABLE client_tax_categories "
        "ADD COLUMN validity_range daterange "
        "GENERATED ALWAYS AS (daterange(valid_from, valid_until, '[]')) STORED"
    )
    op.execute(
        "ALTER TABLE client_tax_categories "
        "ADD CONSTRAINT ex_client_tax_categories_no_overlap "
        "EXCLUDE USING gist (client_id WITH =, category_code WITH =, validity_range WITH &&)"
    )


def downgrade() -> None:
    op.drop_table("client_tax_categories")
    op.drop_table("tax_category_catalog")
    # No se elimina la extension btree_gist: podria ser usada por otros
    # componentes.
