"""Matriz de migracion 0012 (RBAC real + gestion de usuarios, Bloque 4):
preflight de 3 verificaciones ANTES de cualquier ALTER TABLE (roles
desconocidos, usuarios inactivos heredados sin metadatos, tenants sin admin
activo), upgrade/downgrade/re-upgrade, y ausencia de DDL parcial cuando el
preflight falla."""

import uuid

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from tests.integration._migration_helpers import make_alembic_config


def _insert_tenant(engine, name: str) -> uuid.UUID:
    with engine.begin() as conn:
        return conn.execute(
            text("INSERT INTO tenants (name) VALUES (:name) RETURNING id"), {"name": name}
        ).scalar_one()


def _insert_user(engine, tenant_id, *, email: str, role: str, is_active: bool) -> uuid.UUID:
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO users (tenant_id, email, full_name, password_hash, role, is_active) "
                "VALUES (:tenant_id, :email, 'Usuario de prueba', 'x', :role, :is_active) "
                "RETURNING id"
            ),
            {"tenant_id": tenant_id, "email": email, "role": role, "is_active": is_active},
        ).scalar_one()


def _constraint_exists(engine, table: str, constraint: str) -> bool:
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE table_name = :table AND constraint_name = :constraint"
            ),
            {"table": table, "constraint": constraint},
        ).scalar()
    return result is not None


def _column_exists(engine, table: str, column: str) -> bool:
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column"
            ),
            {"table": table, "column": column},
        ).scalar()
    return result is not None


def _assert_no_partial_ddl(engine) -> None:
    for column in ("deactivated_at", "deactivated_by", "deactivation_reason", "must_change_password"):
        assert not _column_exists(engine, "users", column), f"columna {column} no debio crearse"
    for constraint in ("ck_users_role", "ck_users_deactivation_coherence", "fk_users_deactivated_by"):
        assert not _constraint_exists(engine, "users", constraint), (
            f"constraint {constraint} no debio crearse"
        )


def test_upgrade_with_valid_data_then_downgrade_and_reupgrade(redirected_database_url) -> None:
    url = redirected_database_url
    cfg = make_alembic_config()

    command.upgrade(cfg, "0011")
    engine = create_engine(url, future=True)
    try:
        tenant_id = _insert_tenant(engine, "Tenant valido")
        _insert_user(engine, tenant_id, email="admin@valido.dev", role="admin", is_active=True)
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(url, future=True)
    try:
        assert _constraint_exists(engine, "users", "ck_users_role")
        assert _constraint_exists(engine, "users", "ck_users_deactivation_coherence")
        assert _constraint_exists(engine, "users", "fk_users_deactivated_by")
        assert _column_exists(engine, "users", "must_change_password")
    finally:
        engine.dispose()

    command.downgrade(cfg, "0011")
    engine = create_engine(url, future=True)
    try:
        _assert_no_partial_ddl(engine)
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(url, future=True)
    try:
        assert _constraint_exists(engine, "users", "ck_users_role")
    finally:
        engine.dispose()


def test_unknown_role_blocks_migration_before_any_ddl(redirected_database_url) -> None:
    url = redirected_database_url
    cfg = make_alembic_config()

    command.upgrade(cfg, "0011")
    engine = create_engine(url, future=True)
    try:
        tenant_id = _insert_tenant(engine, "Tenant rol desconocido")
        _insert_user(engine, tenant_id, email="raro@x.dev", role="superadmin", is_active=True)
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="role"):
        command.upgrade(cfg, "head")

    engine = create_engine(url, future=True)
    try:
        _assert_no_partial_ddl(engine)
    finally:
        engine.dispose()


def test_legacy_inactive_user_without_metadata_blocks_migration(redirected_database_url) -> None:
    url = redirected_database_url
    cfg = make_alembic_config()

    command.upgrade(cfg, "0011")
    engine = create_engine(url, future=True)
    try:
        tenant_id = _insert_tenant(engine, "Tenant con inactivo heredado")
        _insert_user(engine, tenant_id, email="admin@x.dev", role="admin", is_active=True)
        _insert_user(engine, tenant_id, email="inactivo@x.dev", role="contador", is_active=False)
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="inactivo@x.dev"):
        command.upgrade(cfg, "head")

    engine = create_engine(url, future=True)
    try:
        _assert_no_partial_ddl(engine)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "seed_users",
    [
        pytest.param([], id="tenant_sin_usuarios"),
        pytest.param([("solo_contador@x.dev", "contador", True)], id="tenant_sin_ningun_admin"),
        pytest.param([("admin_inactivo@x.dev", "admin", False)], id="tenant_con_admin_solo_inactivo"),
    ],
)
def test_tenant_without_active_admin_blocks_migration(redirected_database_url, seed_users) -> None:
    url = redirected_database_url
    cfg = make_alembic_config()

    command.upgrade(cfg, "0011")
    engine = create_engine(url, future=True)
    try:
        tenant_id = _insert_tenant(engine, "Tenant sin admin activo")
        for email, role, is_active in seed_users:
            _insert_user(engine, tenant_id, email=email, role=role, is_active=is_active)
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="admin"):
        command.upgrade(cfg, "head")

    engine = create_engine(url, future=True)
    try:
        _assert_no_partial_ddl(engine)
    finally:
        engine.dispose()


def test_tenant_with_active_admin_does_not_block_migration(redirected_database_url) -> None:
    url = redirected_database_url
    cfg = make_alembic_config()

    command.upgrade(cfg, "0011")
    engine = create_engine(url, future=True)
    try:
        tenant_id = _insert_tenant(engine, "Tenant con admin activo")
        _insert_user(engine, tenant_id, email="admin@ok.dev", role="admin", is_active=True)
        _insert_user(engine, tenant_id, email="contador@ok.dev", role="contador", is_active=True)
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")  # no debe lanzar
    engine = create_engine(url, future=True)
    try:
        assert _constraint_exists(engine, "users", "ck_users_role")
    finally:
        engine.dispose()
