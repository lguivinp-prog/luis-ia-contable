"""Migracion 0018 (Fase 5, Bloque 5.2, ver docs/phase5/block5_2/): ejercita
Alembic real (no Base.metadata.create_all) contra una base de datos Postgres
nueva y desechable -- mismo patron que test_migrations.py. Verifica el
round-trip completo 0017 -> 0018 -> 0017 -> 0018 sin romper datos ni dejar
residuos, y que 0018 crea exactamente las 4 tablas nuevas congeladas."""

from alembic import command
from sqlalchemy import create_engine, text

from tests.integration._migration_helpers import make_alembic_config

_NEW_TABLES = {"sol_agents", "sol_access_sessions", "sol_agent_jobs", "sol_agent_pairings"}


def _existing_sol_tables(url: str) -> set[str]:
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                    "AND (tablename LIKE 'sol_agent%' OR tablename = 'sol_access_sessions')"
                )
            ).fetchall()
            return {row[0] for row in rows}
    finally:
        engine.dispose()


def test_migration_0018_upgrade_downgrade_roundtrip(redirected_database_url) -> None:
    url = redirected_database_url
    cfg = make_alembic_config()

    command.upgrade(cfg, "0017")
    assert _existing_sol_tables(url) == set(), "0017 no debe crear ninguna de las 4 tablas de 0018"

    command.upgrade(cfg, "0018")
    assert _existing_sol_tables(url) == _NEW_TABLES, "0018 debe crear exactamente 4 tablas nuevas"

    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()
    assert head == "0018"

    command.downgrade(cfg, "0017")
    assert _existing_sol_tables(url) == set(), "el downgrade debe eliminar las 4 tablas sin residuos"

    # Confirma que 0017 sigue integro tras el ciclo completo (no se rompieron
    # datos/tablas previas, ver seccion 49 del encargo de Domain B).
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            client_sol_credentials_exists = conn.execute(
                text(
                    "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename = 'client_sol_credentials'"
                )
            ).scalar()
    finally:
        engine.dispose()
    assert client_sol_credentials_exists == 1

    command.upgrade(cfg, "0018")
    assert _existing_sol_tables(url) == _NEW_TABLES, "el segundo upgrade debe volver a crear las 4 tablas"
