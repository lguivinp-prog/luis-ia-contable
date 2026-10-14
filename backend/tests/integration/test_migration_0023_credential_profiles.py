"""Migracion 0023 (M3): perfiles de credencial SOL. upgrade -> downgrade a 0022 -> re-upgrade sobre PostgreSQL real."""

from alembic import command
from sqlalchemy import create_engine, text

from tests.integration._migration_helpers import make_alembic_config


def _state(url) -> dict:
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            cols = {r[0] for r in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='client_sol_credentials'"))}
            idx = {r[0] for r in conn.execute(text("SELECT indexname FROM pg_indexes WHERE tablename='client_sol_credentials'"))}
    finally:
        engine.dispose()
    return {"profile_name": "profile_name" in cols, "indexes": idx}


def test_0023_adds_profiles_and_is_reversible(redirected_database_url) -> None:
    url = redirected_database_url
    cfg = make_alembic_config()
    command.upgrade(cfg, "0022")
    before = _state(url)
    assert not before["profile_name"] and "ux_client_sol_credentials_active_per_client" in before["indexes"]
    command.upgrade(cfg, "0023")
    after = _state(url)
    assert after["profile_name"] and "ux_client_sol_credentials_active_per_client" not in after["indexes"]
    assert {"ux_client_sol_credentials_active_profile", "ux_client_sol_credentials_one_primary"} <= after["indexes"]
    command.downgrade(cfg, "0022")  # sin datos multiples el retroceso es posible y restaura el indice por cliente
    assert _state(url) == before
    command.upgrade(cfg, "head")
    assert _state(url) == after
