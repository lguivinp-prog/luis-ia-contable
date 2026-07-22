"""Verifica que las migraciones funcionan desde una base de datos Postgres
limpia donde la extension pgcrypto AUN NO esta instalada (ver correccion de
seguridad sobre gen_random_bytes en la migracion 0002_auth_hardening)."""

from alembic import command
from sqlalchemy import create_engine, text

from tests.integration._migration_helpers import make_alembic_config


def test_migrations_bootstrap_from_database_without_pgcrypto(redirected_database_url) -> None:
    url = redirected_database_url

    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            has_pgcrypto_before = conn.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto'")
            ).scalar_one_or_none()
    finally:
        engine.dispose()

    assert has_pgcrypto_before is None, "La base de prueba no debe tener pgcrypto preinstalado"

    cfg = make_alembic_config()
    command.upgrade(cfg, "head")

    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            has_pgcrypto_after = conn.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto'")
            ).scalar_one_or_none()
            has_btree_gist = conn.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'btree_gist'")
            ).scalar_one_or_none()
            tenants_table_exists = conn.execute(
                text("SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = 'tenants'")
            ).scalar_one_or_none()
    finally:
        engine.dispose()

    assert has_pgcrypto_after is not None, "pgcrypto debe quedar instalado tras las migraciones"
    assert has_btree_gist is not None, "btree_gist debe quedar instalado tras las migraciones"
    assert tenants_table_exists is not None
