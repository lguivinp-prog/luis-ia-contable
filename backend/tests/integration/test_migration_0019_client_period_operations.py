"""Migracion 0019 (M1): client_period_operations + client_period_operation_history. upgrade -> downgrade a 0018 ->
re-upgrade sobre PostgreSQL real, cabeza unica de Alembic y constraints efectivamente aplicados por la BD."""

import uuid

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from tests.integration._migration_helpers import make_alembic_config

_TABLES = {"client_period_operations", "client_period_operation_history"}


def _tables(url) -> set[str]:
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename = ANY(:names)"),
                {"names": list(_TABLES)},
            ).all()
    finally:
        engine.dispose()
    return {r[0] for r in rows}


def test_single_head_is_0019() -> None:
    script = ScriptDirectory.from_config(make_alembic_config())
    assert script.get_heads() == ["0019"]
    assert script.get_revision("0019").down_revision == "0018"


def test_upgrade_downgrade_reupgrade(redirected_database_url) -> None:
    url = redirected_database_url
    cfg = make_alembic_config()
    command.upgrade(cfg, "0018")
    assert _tables(url) == set()
    command.upgrade(cfg, "head")
    assert _tables(url) == _TABLES
    command.downgrade(cfg, "0018")
    assert _tables(url) == set()
    command.upgrade(cfg, "head")
    assert _tables(url) == _TABLES


def _seed(conn):
    tenant, other = uuid.uuid4(), uuid.uuid4()
    for t in (tenant, other):
        conn.execute(text("INSERT INTO tenants (id, name) VALUES (:id, 'T')"), {"id": t})
    ids = {}
    for t, label in ((tenant, "a"), (other, "b")):
        client, period, user = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO clients (id, tenant_id, ruc, business_name, person_type, ruc_status, "
                "fiscal_address_condition, tax_regime, service_status) VALUES "
                "(:id, :t, :ruc, 'C', 'juridica', 'ACTIVO', 'HABIDO', 'GENERAL', 'activo')"
            ),
            {"id": client, "t": t, "ruc": "20" + str(uuid.uuid4().int)[:9]},
        )
        conn.execute(
            text(
                "INSERT INTO tax_periods (id, tenant_id, period_type, fiscal_year, month, canonical_code, start_date, end_date, is_open) "
                "VALUES (:id, :t, 'MONTHLY', 2026, 1, :code, '2026-01-01', '2026-01-31', true)"
            ),
            {"id": period, "t": t, "code": "2026-01"},
        )
        conn.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, full_name, password_hash, role, is_active) "
                "VALUES (:id, :t, :email, 'U', 'x', 'admin', true)"
            ),
            {"id": user, "t": t, "email": f"{uuid.uuid4().hex[:8]}@x.dev"},
        )
        ids[label] = (t, client, period, user)
    return ids


def _insert_op(conn, t, client, period, user=None, **overrides):
    values = {"t": t, "c": client, "p": period, "u": user, "b": "NOT_REVIEWED", "a": "NOT_STARTED", "m": "NOT_STARTED", "v": 1}
    values.update(overrides)
    conn.execute(
        text(
            "INSERT INTO client_period_operations (id, tenant_id, client_id, tax_period_id, buzon_status, "
            "accounting_status, client_communication_status, row_version, updated_by, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :t, :c, :p, :b, :a, :m, :v, :u, now(), now())"
        ),
        values,
    )


@pytest.mark.parametrize(
    "overrides",
    [{"b": "INVENTADO"}, {"a": "NOT_REVIEWED"}, {"m": "COMPLETED"}, {"v": 0}],
)
def test_database_rejects_invalid_values(redirected_database_url, overrides) -> None:
    command.upgrade(make_alembic_config(), "head")
    engine = create_engine(redirected_database_url, future=True)
    try:
        with engine.connect() as conn:
            ids = _seed(conn)
            t, client, period, _user = ids["a"]
            with pytest.raises(IntegrityError):
                _insert_op(conn, t, client, period, **overrides)
            conn.rollback()
    finally:
        engine.dispose()


def test_database_enforces_tenant_integrity_and_uniqueness(redirected_database_url) -> None:
    command.upgrade(make_alembic_config(), "head")
    engine = create_engine(redirected_database_url, future=True)
    try:
        with engine.connect() as conn:
            ids = _seed(conn)
            ta, ca, pa, ua = ids["a"]
            tb, cb, pb, ub = ids["b"]
            _insert_op(conn, ta, ca, pa, ua)
            with pytest.raises(IntegrityError), conn.begin_nested():  # duplicado de (tenant, cliente, periodo)
                _insert_op(conn, ta, ca, pa)
            with pytest.raises(IntegrityError), conn.begin_nested():  # cliente de otro tenant
                _insert_op(conn, ta, cb, pa)
            with pytest.raises(IntegrityError), conn.begin_nested():  # periodo de otro tenant
                _insert_op(conn, ta, ca, pb)
            with pytest.raises(IntegrityError), conn.begin_nested():  # actor de otro tenant
                _insert_op(conn, tb, cb, pb, ua)
            with pytest.raises(IntegrityError), conn.begin_nested():  # notas > 2000
                conn.execute(
                    text("UPDATE client_period_operations SET notes = :n WHERE tenant_id = :t"),
                    {"n": "x" * 2001, "t": ta},
                )
            # historial: debe apuntar al alcance exacto de la operacion
            op_id = conn.execute(text("SELECT id FROM client_period_operations WHERE tenant_id = :t"), {"t": ta}).scalar()
            insert_history = text(
                "INSERT INTO client_period_operation_history (id, tenant_id, operation_id, client_id, tax_period_id, "
                "field_name, from_value, to_value, row_version_after, actor_user_id, created_at) "
                "VALUES (gen_random_uuid(), :t, :o, :c, :p, :f, 'a', 'b', 1, :u, now())"
            )
            ok = {"t": ta, "o": op_id, "c": ca, "p": pa, "f": "buzon_status", "u": ua}
            conn.execute(insert_history, ok)
            with pytest.raises(IntegrityError), conn.begin_nested():
                conn.execute(insert_history, {**ok, "c": cb})
            with pytest.raises(IntegrityError), conn.begin_nested():
                conn.execute(insert_history, {**ok, "f": "document_status"})
            conn.rollback()
    finally:
        engine.dispose()
