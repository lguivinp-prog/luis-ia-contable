"""Migracion 0025 (M4): expediente de control SIRE. upgrade -> downgrade a 0024 -> re-upgrade sobre PostgreSQL real, en una base AISLADA; el retroceso se
bloquea si existen expedientes (no se destruye evidencia)."""

import uuid

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from tests.integration._migration_helpers import make_alembic_config

TABLES = {"sire_controls", "sire_control_sources", "sire_control_files", "sire_control_reviews"}


def _tables(url) -> set[str]:
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            return {r[0] for r in conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'"))} & TABLES
    finally:
        engine.dispose()


def test_0025_adds_the_control_tables_and_is_reversible(redirected_database_url) -> None:
    url = redirected_database_url
    cfg = make_alembic_config()
    command.upgrade(cfg, "0024")
    assert _tables(url) == set()
    command.upgrade(cfg, "0025")
    assert _tables(url) == TABLES
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            indexes = {r[0] for r in conn.execute(text("SELECT indexname FROM pg_indexes WHERE tablename LIKE 'sire_control%'"))}
            constraints = {r[0] for r in conn.execute(text("SELECT conname FROM pg_constraint WHERE conrelid::regclass::text LIKE 'sire_control%'"))}
    finally:
        engine.dispose()
    assert {"ux_sire_control_sources_one_active", "ux_sire_control_files_dedupe", "ux_sire_control_reviews_one_current"} <= indexes
    assert {"uq_sire_controls_scope", "ck_sire_control_sources_has_data", "ck_sire_control_reviews_notes_required", "fk_sire_control_sources_scope"} <= constraints
    command.downgrade(cfg, "0024")  # sin expedientes el retroceso es posible
    assert _tables(url) == set()
    command.upgrade(cfg, "head")
    assert _tables(url) == TABLES


def test_0025_downgrade_is_blocked_when_controls_exist(redirected_database_url) -> None:
    url = redirected_database_url
    cfg = make_alembic_config()
    command.upgrade(cfg, "head")
    engine = create_engine(url, future=True)
    tenant, user, client, period = (uuid.uuid4() for _ in range(4))
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO tenants (id, name) VALUES (:t, 'T')"), {"t": tenant})
        conn.execute(
            text("INSERT INTO users (id, tenant_id, email, full_name, password_hash, role, is_active) VALUES (:u, :t, 'a@luisiacontable-tests.dev', 'A', 'x', 'admin', true)"),
            {"u": user, "t": tenant},
        )
        conn.execute(
            text(
                "INSERT INTO clients (id, tenant_id, ruc, business_name, person_type, ruc_status, fiscal_address_condition, tax_regime, service_status) "
                "VALUES (:c, :t, '20100070970', 'C', 'juridica', 'ACTIVO', 'HABIDO', 'GENERAL', 'activo')"
            ),
            {"c": client, "t": tenant},
        )
        conn.execute(
            text(
                "INSERT INTO tax_periods (id, tenant_id, period_type, fiscal_year, month, canonical_code, start_date, end_date, is_open) "
                "VALUES (:p, :t, 'MONTHLY', 2026, 1, '2026-01', '2026-01-01', '2026-01-31', true)"
            ),
            {"p": period, "t": tenant},
        )
        conn.execute(
            text("INSERT INTO sire_controls (tenant_id, client_id, tax_period_id, component, row_version, created_by) VALUES (:t, :c, :p, 'RVIE', 1, :u)"),
            {"t": tenant, "c": client, "p": period, "u": user},
        )
    engine.dispose()
    with pytest.raises(RuntimeError, match="downgrade 0025 bloqueado"):
        command.downgrade(cfg, "0024")
    assert _tables(url) == TABLES  # nada se destruyo
