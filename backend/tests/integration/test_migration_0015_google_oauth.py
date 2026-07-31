"""Matriz de migracion 0015 (Fase 3 / Bloque 3.1, ver ADR-0062/ADR-0063):
2 tablas nuevas -- google_oauth_connections (1 FK simple + 2 FK compuestas +
4 UNIQUE + 7 CHECK) y google_oauth_authorization_attempts (1 FK simple + 1
FK compuesta + 1 UNIQUE + 7 CHECK) -- upgrade/downgrade/re-upgrade limpio +
inspeccion de esquema completa (mismo patron que
test_migration_0014_operational_tasks.py)."""

from alembic import command
from sqlalchemy import create_engine, text

from tests.integration._migration_helpers import make_alembic_config

_CONNECTIONS_UNIQUE = [
    "uq_google_oauth_connections_id_tenant",
    "uq_google_oauth_connections_id_tenant_user",
    "uq_google_oauth_connections_tenant_user",
    "uq_google_oauth_connections_tenant_account",
]

_CONNECTIONS_CHECKS = [
    "ck_google_oauth_connections_provider",
    "ck_google_oauth_connections_status",
    "ck_google_oauth_connections_disconnection_origin",
    "ck_google_oauth_connections_reauth_reason",
    "ck_google_oauth_connections_version_positive",
    "ck_google_oauth_connections_scopes_array",
    "ck_google_oauth_connections_status_coherence",
]

_CONNECTIONS_FKS = [
    "fk_google_oauth_connections_user_tenant",
    "fk_google_oauth_connections_disconnected_by_tenant",
]

_ATTEMPTS_UNIQUE = ["uq_google_oauth_authorization_attempts_state_hash"]

_ATTEMPTS_CHECKS = [
    "ck_google_oauth_attempts_capability",
    "ck_google_oauth_attempts_status",
    "ck_google_oauth_attempts_scopes_array",
    "ck_google_oauth_attempts_state_hash_format",
    "ck_google_oauth_attempts_expiry_after_creation",
    "ck_google_oauth_attempts_error_code",
    "ck_google_oauth_attempts_status_coherence",
]

_ATTEMPTS_FKS = ["fk_google_oauth_authorization_attempts_user_tenant"]


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


def _table_exists(engine, table: str) -> bool:
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT 1 FROM information_schema.tables WHERE table_name = :table"),
            {"table": table},
        ).scalar()
    return result is not None


def test_upgrade_creates_all_tables_and_constraints_then_downgrade_and_reupgrade(
    redirected_database_url,
) -> None:
    url = redirected_database_url
    cfg = make_alembic_config()

    command.upgrade(cfg, "0014")
    command.upgrade(cfg, "head")

    engine = create_engine(url, future=True)
    try:
        for table in ("google_oauth_connections", "google_oauth_authorization_attempts"):
            assert _table_exists(engine, table), f"tabla faltante: {table}"

        for name in _CONNECTIONS_UNIQUE:
            assert _constraint_exists(engine, "google_oauth_connections", name), f"falta UNIQUE {name}"
        for name in _CONNECTIONS_CHECKS:
            assert _constraint_exists(engine, "google_oauth_connections", name), f"falta CHECK {name}"
        for name in _CONNECTIONS_FKS:
            assert _constraint_exists(engine, "google_oauth_connections", name), f"falta FK {name}"

        for name in _ATTEMPTS_UNIQUE:
            assert _constraint_exists(
                engine, "google_oauth_authorization_attempts", name
            ), f"falta UNIQUE {name}"
        for name in _ATTEMPTS_CHECKS:
            assert _constraint_exists(
                engine, "google_oauth_authorization_attempts", name
            ), f"falta CHECK {name}"
        for name in _ATTEMPTS_FKS:
            assert _constraint_exists(
                engine, "google_oauth_authorization_attempts", name
            ), f"falta FK {name}"
    finally:
        engine.dispose()

    command.downgrade(cfg, "0014")
    engine = create_engine(url, future=True)
    try:
        assert not _table_exists(engine, "google_oauth_connections")
        assert not _table_exists(engine, "google_oauth_authorization_attempts")
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(url, future=True)
    try:
        assert _table_exists(engine, "google_oauth_connections")
        assert _table_exists(engine, "google_oauth_authorization_attempts")
    finally:
        engine.dispose()
