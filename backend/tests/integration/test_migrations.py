"""Prueba de migracion partiendo de una base en 0001 con una sesion activa
preexistente (ver ADR-0010, migracion 0002_auth_hardening). Usa una base de
datos Postgres desechable y Alembic real (no Base.metadata.create_all)."""

from alembic import command
from sqlalchemy import create_engine, text

from tests.integration._migration_helpers import make_alembic_config


def test_upgrade_from_0001_revokes_preexisting_active_session(redirected_database_url) -> None:
    url = redirected_database_url
    cfg = make_alembic_config()

    command.upgrade(cfg, "0001")

    engine = create_engine(url, future=True)
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
            tenant_id = conn.execute(
                text("INSERT INTO tenants (id, name) VALUES (gen_random_uuid(), 'T') RETURNING id")
            ).scalar()
            user_id = conn.execute(
                text(
                    "INSERT INTO users "
                    "(id, tenant_id, email, full_name, password_hash, role, is_active) "
                    "VALUES "
                    "(gen_random_uuid(), :t, 'legacy@dominio-ficticio.dev', 'Legacy', "
                    "'hash', 'admin', true) "
                    "RETURNING id"
                ),
                {"t": tenant_id},
            ).scalar()
            session_id = conn.execute(
                text(
                    "INSERT INTO sessions "
                    "(id, tenant_id, user_id, token_hash, created_at, last_seen_at, expires_at) "
                    "VALUES "
                    "(gen_random_uuid(), :t, :u, 'legacy-token-hash', now(), now(), "
                    "now() + interval '1 day') "
                    "RETURNING id"
                ),
                {"t": tenant_id, "u": user_id},
            ).scalar()
    finally:
        engine.dispose()

    command.upgrade(cfg, "head")

    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT revoked_at, csrf_token_hash FROM sessions WHERE id = :id"),
                {"id": session_id},
            ).one()
            table_count = conn.execute(
                text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
            ).scalar()
    finally:
        engine.dispose()

    assert row.revoked_at is not None, "La sesion preexistente debe quedar revocada"
    assert row.csrf_token_hash is not None, "Debe tener un csrf_token_hash de relleno (no NULL)"
    assert table_count >= 17  # todas las tablas hasta 0004
