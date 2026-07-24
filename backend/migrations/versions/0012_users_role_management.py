"""RBAC real + gestion de usuarios (Bloque 4): roles fijos, desactivacion con
motivo, contrasena temporal.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-24

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KNOWN_ROLES = ("admin", "contador", "asistente")


def _run_preflight_checks() -> None:
    """Las 3 verificaciones corren ANTES de cualquier ALTER TABLE, sobre el
    esquema 0001-0011 tal cual (role/is_active/tenant_id ya existen). Ninguna
    inventa datos: si algo no es compatible, la migracion se detiene aqui,
    sin haber ejecutado DDL (ver ADR-0051 / Dominio A del plan)."""
    bind = op.get_bind()

    # 1. Roles desconocidos.
    unknown_roles = bind.execute(
        sa.text(
            "SELECT DISTINCT role FROM users WHERE role NOT IN :known_roles"
        ).bindparams(sa.bindparam("known_roles", expanding=True)),
        {"known_roles": list(_KNOWN_ROLES)},
    ).scalars().all()
    if unknown_roles:
        raise RuntimeError(
            "Migracion 0012 detenida: existen usuarios con un 'role' fuera de "
            f"{_KNOWN_ROLES}: {sorted(unknown_roles)}. Regulariza estos valores "
            "antes de reintentar la migracion."
        )

    # 2. Usuarios heredados inactivos (is_active=false ya en el esquema 0011).
    # Las columnas deactivated_at/deactivated_by/deactivation_reason todavia
    # NO existen en este punto -- no tiene sentido pedir que se "regularicen"
    # columnas inexistentes. La migracion nunca inventa esos valores.
    legacy_inactive = bind.execute(
        sa.text("SELECT id, email, tenant_id FROM users WHERE is_active = false")
    ).mappings().all()
    if legacy_inactive:
        detalle = "; ".join(
            f"id={row['id']} email={row['email']} tenant_id={row['tenant_id']}"
            for row in legacy_inactive
        )
        raise RuntimeError(
            "Migracion 0012 detenida: existen usuarios inactivos heredados sin "
            f"metadatos de desactivacion ({detalle}). Esta migracion no inventara "
            "deactivated_at/deactivated_by/deactivation_reason -- decide y aplica "
            "esos valores explicitamente en el esquema anterior (0011) antes de "
            "reintentar."
        )

    # 3. Cada tenant existente debe tener al menos un admin activo. El
    # universo de partida es `tenants` (no los tenant_id distintos en
    # `users`), para detectar tambien tenants sin ningun usuario.
    tenants_without_admin = bind.execute(
        sa.text(
            """
            SELECT t.id
            FROM tenants t
            WHERE NOT EXISTS (
                SELECT 1 FROM users u
                WHERE u.tenant_id = t.id AND u.role = 'admin' AND u.is_active = true
            )
            """
        )
    ).scalars().all()
    if tenants_without_admin:
        raise RuntimeError(
            "Migracion 0012 detenida: los siguientes tenants no tienen ningun "
            f"usuario admin activo: {sorted(str(t) for t in tenants_without_admin)}. "
            "Cada tenant debe conservar al menos un admin activo antes de aplicar "
            "el RBAC real."
        )


def upgrade() -> None:
    _run_preflight_checks()

    op.add_column("users", sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("deactivated_by", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("users", sa.Column("deactivation_reason", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_foreign_key(
        "fk_users_deactivated_by",
        "users",
        "users",
        ["deactivated_by", "tenant_id"],
        ["id", "tenant_id"],
    )
    op.create_check_constraint(
        "ck_users_role",
        "users",
        "role IN ('admin', 'contador', 'asistente')",
    )
    op.create_check_constraint(
        "ck_users_deactivation_coherence",
        "users",
        "(is_active = true AND deactivated_at IS NULL AND deactivated_by IS NULL "
        "AND deactivation_reason IS NULL) OR "
        "(is_active = false AND deactivated_at IS NOT NULL AND deactivated_by IS NOT NULL "
        "AND deactivation_reason IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_deactivation_coherence", "users", type_="check")
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.drop_constraint("fk_users_deactivated_by", "users", type_="foreignkey")
    op.drop_column("users", "must_change_password")
    op.drop_column("users", "deactivation_reason")
    op.drop_column("users", "deactivated_by")
    op.drop_column("users", "deactivated_at")
