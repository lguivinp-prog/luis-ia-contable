"""Matriz de migraciones para Bloque 3 (0005-0007): upgrade desde 0004,
downgrade x3 y re-upgrade, verificando ademas que las UNIQUE auxiliares
agregadas en 0005/0006 a tablas de Bloque 1/2 (users/clients/client_obligations/
tax_schedule_*) existen tras el upgrade y desaparecen tras el downgrade."""

from alembic import command
from sqlalchemy import create_engine, text

from tests.integration._migration_helpers import make_alembic_config


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


def test_upgrade_0004_to_head_then_downgrade_and_reupgrade(redirected_database_url) -> None:
    url = redirected_database_url
    cfg = make_alembic_config()

    command.upgrade(cfg, "0004")
    engine = create_engine(url, future=True)
    try:
        assert not _constraint_exists(engine, "users", "uq_users_id_tenant")
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")
    engine = create_engine(url, future=True)
    try:
        for table, constraint in [
            ("users", "uq_users_id_tenant"),
            ("clients", "uq_clients_id_tenant"),
            ("client_obligations", "uq_client_obligations_id_tenant_client"),
            ("tax_schedule_sources", "uq_tax_schedule_sources_id_tenant"),
            ("tax_schedule_versions", "uq_tax_schedule_versions_id_tenant_source"),
            ("tax_schedule_entries", "uq_tax_schedule_entries_id_tenant_version"),
            ("tax_schedule_exceptions", "uq_tax_schedule_exceptions_id_tenant_version"),
            ("declarations", "uq_declarations_id_tenant"),
            ("declaration_generation_runs", "uq_declaration_generation_runs_id_tenant"),
        ]:
            assert _constraint_exists(engine, table, constraint), f"falta {constraint} en {table}"

        table_count = conn_count(engine)
        assert table_count > 0
    finally:
        engine.dispose()

    # downgrade completo de las 3 migraciones nuevas, una por una
    command.downgrade(cfg, "0006")
    command.downgrade(cfg, "0005")
    command.downgrade(cfg, "0004")

    engine = create_engine(url, future=True)
    try:
        assert not _constraint_exists(engine, "users", "uq_users_id_tenant")
        assert not _constraint_exists(engine, "clients", "uq_clients_id_tenant")
    finally:
        engine.dispose()

    # re-upgrade completo, debe quedar identico
    command.upgrade(cfg, "head")
    engine = create_engine(url, future=True)
    try:
        assert _constraint_exists(engine, "users", "uq_users_id_tenant")
        assert _constraint_exists(engine, "declarations", "uq_declarations_id_tenant")
    finally:
        engine.dispose()


def conn_count(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
        ).scalar()
