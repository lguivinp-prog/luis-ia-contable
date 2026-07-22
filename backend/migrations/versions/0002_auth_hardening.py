"""Auth hardening: login_attempts, security_events, sessions.csrf_token_hash

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-22

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Defensivo: no se asume que 0001 ya la creo (ver ADR-0010/ADR-0009).
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "login_attempts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=True,
        ),
        sa.Column("identifier", sa.String(255), nullable=False),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_login_attempts_identifier_attempted_at",
        "login_attempts",
        ["identifier", "attempted_at"],
    )

    op.create_table(
        "security_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("identifier", sa.String(255), nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- sessions.csrf_token_hash: procedimiento seguro (ver ADR-0010) ---
    # 1) columna nullable
    op.add_column("sessions", sa.Column("csrf_token_hash", sa.String(64), nullable=True))
    # 2) revocar sesiones preexistentes (creadas antes de CSRF, sin hash)
    op.execute(
        "UPDATE sessions SET revoked_at = now() "
        "WHERE csrf_token_hash IS NULL AND revoked_at IS NULL"
    )
    # 3) backfill con un valor aleatorio de relleno (las filas ya estan revocadas;
    #    el valor no protege nada, solo satisface la futura restriccion NOT NULL)
    op.execute(
        "UPDATE sessions SET csrf_token_hash = encode(gen_random_bytes(32), 'hex') "
        "WHERE csrf_token_hash IS NULL"
    )
    # 4) asercion de integridad: no deben quedar filas activas sin hash
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM sessions
                WHERE csrf_token_hash IS NULL AND revoked_at IS NULL
            ) THEN
                RAISE EXCEPTION 'Existen sesiones activas sin csrf_token_hash tras la migracion';
            END IF;
        END $$;
        """
    )
    # 5) NOT NULL
    op.alter_column("sessions", "csrf_token_hash", nullable=False)
    # 6) indice unico
    op.create_unique_constraint("uq_sessions_csrf_token_hash", "sessions", ["csrf_token_hash"])


def downgrade() -> None:
    op.drop_constraint("uq_sessions_csrf_token_hash", "sessions", type_="unique")
    op.drop_column("sessions", "csrf_token_hash")
    op.drop_table("security_events")
    op.drop_index("ix_login_attempts_identifier_attempted_at", table_name="login_attempts")
    op.drop_table("login_attempts")
    # No se elimina la extension pgcrypto: podria ser usada por otros
    # componentes (p. ej. gen_random_uuid() en la migracion 0001).
