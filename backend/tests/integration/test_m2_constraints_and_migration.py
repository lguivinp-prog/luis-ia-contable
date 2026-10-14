"""M2: migracion 0020 (up/down/up en PostgreSQL real, cabeza unica) y constraints de BD de las sesiones con destino, los
snapshots de la Ficha y las altas asistidas (integridad por tenant y coherencia impuestas por la BD, no solo por servicio)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.models.client_ficha_ruc import ClientFichaRucSnapshot, ClientOnboarding
from app.models.sol_agent_access import SolAccessSession
from tests.integration._migration_helpers import make_alembic_config
from tests.integration._sol_agent_access_helpers import (
    make_client,
    make_client_sol_credential,
    make_sol_access_session,
)

# ------------------------------------------------------------------------------------------------- migracion


def test_single_head_is_0024_after_m3_and_0020_chains_from_0019():
    script = ScriptDirectory.from_config(make_alembic_config())
    assert script.get_heads() == ["0024"]
    assert script.get_revision("0024").down_revision == "0023"
    assert script.get_revision("0020").down_revision == "0019" and script.get_revision("0021").down_revision == "0020"
    assert script.get_revision("0022").down_revision == "0021"


def _constraint_def(url, table, name):
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            return conn.execute(
                text("SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                     "WHERE t.relname = :t AND c.conname = :n"), {"t": table, "n": name}).scalar()
    finally:
        engine.dispose()


def _tables(url):
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename IN "
                                     "('client_ficha_ruc_snapshots','client_onboardings')")).all()
        return {r[0] for r in rows}
    finally:
        engine.dispose()


def _columns(url):
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='sol_access_sessions'")).all()
        return {r[0] for r in rows}
    finally:
        engine.dispose()


def test_upgrade_downgrade_reupgrade_on_postgresql(redirected_database_url):
    url = redirected_database_url
    cfg = make_alembic_config()
    command.upgrade(cfg, "0019")
    assert _tables(url) == set() and "destination" not in _columns(url)
    assert "NAVIGATE_TO_DESTINATION" not in _constraint_def(url, "sol_access_sessions", "ck_sol_access_sessions_scope")

    command.upgrade(cfg, "head")
    assert _tables(url) == {"client_ficha_ruc_snapshots", "client_onboardings"}
    assert {"destination", "extract_ficha", "human_control_at"} <= _columns(url)
    assert "NAVIGATE_TO_DESTINATION" in _constraint_def(url, "sol_access_sessions", "ck_sol_access_sessions_scope")
    assert "HUMAN_CONTROL" in _constraint_def(url, "sol_access_sessions", "ck_sol_access_sessions_status")
    assert "DESTINATION_REACHED" in _constraint_def(url, "sol_access_sessions", "ck_sol_access_sessions_operation_result")

    command.downgrade(cfg, "0019")  # reversible: tablas fuera, CHECKs originales restaurados
    assert _tables(url) == set() and not ({"destination", "extract_ficha", "human_control_at"} & _columns(url))
    assert "HUMAN_CONTROL" not in _constraint_def(url, "sol_access_sessions", "ck_sol_access_sessions_status")
    assert "DESTINATION_REACHED" not in _constraint_def(url, "sol_access_sessions", "ck_sol_access_sessions_operation_result")

    command.upgrade(cfg, "head")
    assert _tables(url) == {"client_ficha_ruc_snapshots", "client_onboardings"}


def test_m1_tables_survive_the_m2_roundtrip(redirected_database_url):
    url = redirected_database_url
    cfg = make_alembic_config()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0019")
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            present = {r[0] for r in conn.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'client_period_operation%'")).all()}
    finally:
        engine.dispose()
    assert present == {"client_period_operations", "client_period_operation_history"}


# ------------------------------------------------------------------------------------- sesiones (constraints)


def _scoped_session(db_session, tenant, admin_user, **overrides):
    client = make_client(db_session, tenant)
    credential = make_client_sol_credential(db_session, tenant, client, admin_user)
    row = SolAccessSession(
        tenant_id=tenant.id, client_id=client.id, credential_id=credential.id, requested_by_user_id=admin_user.id,
        scope=overrides.pop("scope", "NAVIGATE_TO_DESTINATION"), destination=overrides.pop("destination", "FICHA_RUC"),
        status=overrides.pop("status", "CREATED"), idempotency_key=uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(minutes=10), **overrides)
    db_session.add(row)
    return client, credential, row


def test_valid_destination_session_and_human_control_state_are_accepted(db_session, tenant, admin_user):
    _c, _cr, row = _scoped_session(db_session, tenant, admin_user, extract_ficha=True, status="HUMAN_CONTROL",
                                   operation_result="DESTINATION_REACHED")
    db_session.flush()
    assert row.status == "HUMAN_CONTROL"


@pytest.mark.parametrize("overrides", [
    {"scope": "NAVIGATE_TO_DESTINATION", "destination": None},  # alcance de navegacion SIN destino
    {"scope": "AUTHENTICATE_TO_LANDING_ONLY", "destination": "MENU_SOL"},  # destino SIN alcance de navegacion
    {"destination": "BUZON_SUNAT"}, {"destination": "https://x"},  # M3: SIRE & co. son CONOCIDOS (migracion 0021); lo demas no
    {"destination": "SIRE", "extract_ficha": True},  # extraer fuera de FICHA_RUC
    {"destination": "MENU_SOL", "extract_ficha": True},  # extraer fuera de FICHA_RUC
    {"scope": "OTRO"}, {"status": "NOPE"}, {"operation_result": "OTHER"},
])
def test_session_destination_constraints_are_enforced_by_the_database(db_session, tenant, admin_user, overrides):
    _scoped_session(db_session, tenant, admin_user, **overrides)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_human_control_session_still_counts_as_the_one_active_session_per_credential(db_session, tenant, admin_user):
    client, credential, first = _scoped_session(db_session, tenant, admin_user, status="HUMAN_CONTROL")
    db_session.flush()
    db_session.add(SolAccessSession(
        tenant_id=tenant.id, client_id=client.id, credential_id=credential.id, requested_by_user_id=admin_user.id,
        scope="AUTHENTICATE_TO_LANDING_ONLY", status="CREATED", idempotency_key=uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(minutes=10)))
    with pytest.raises(IntegrityError):
        db_session.flush()


# ------------------------------------------------------------------------------------------- snapshots / alta


def _second_tenant_with_client(db_session):
    from app.models.tenant import Tenant

    other = Tenant(name=f"Otro tenant {uuid.uuid4().hex[:6]}")
    db_session.add(other)
    db_session.flush()
    return other, make_client(db_session, other)


def _snapshot(db_session, tenant, client, session_row, **overrides):
    values = dict(tenant_id=tenant.id, client_id=client.id, session_id=session_row.id, purpose="REFRESH",
                  source="SUNAT_FICHA_RUC", schema_version=1, retrieved_at=datetime.now(UTC), fields={"ruc": client.ruc},
                  malformed=[], diff=[], review_status="PENDING_REVIEW")
    values.update(overrides)
    row = ClientFichaRucSnapshot(**values)
    db_session.add(row)
    return row


@pytest.fixture()
def base(db_session, tenant, admin_user):
    client = make_client(db_session, tenant)
    credential = make_client_sol_credential(db_session, tenant, client, admin_user)
    session_row = make_sol_access_session(db_session, tenant, client, credential, admin_user, status="SUCCEEDED")
    return client, credential, session_row


def test_valid_snapshot_is_accepted(db_session, tenant, base):
    client, _cr, session_row = base
    _snapshot(db_session, tenant, client, session_row)
    db_session.flush()


@pytest.mark.parametrize("overrides", [
    {"source": "OTRA_FUENTE"}, {"purpose": "OTRO"}, {"review_status": "NOPE"}, {"schema_version": 0}, {"version": 0},
    {"review_status": "ACCEPTED"},  # aceptada SIN revisor ni fecha
])
def test_snapshot_constraints_are_enforced(db_session, tenant, base, overrides):
    client, _cr, session_row = base
    _snapshot(db_session, tenant, client, session_row, **overrides)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_snapshot_pending_cannot_carry_a_reviewer(db_session, tenant, admin_user, base):
    client, _cr, session_row = base
    _snapshot(db_session, tenant, client, session_row, reviewed_by=admin_user.id, reviewed_at=datetime.now(UTC))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_one_snapshot_per_session_and_one_pending_per_client(db_session, tenant, admin_user, base):
    client, credential, first_session = base
    _snapshot(db_session, tenant, client, first_session)
    db_session.flush()
    _snapshot(db_session, tenant, client, first_session, review_status="REJECTED", reviewed_by=admin_user.id, reviewed_at=datetime.now(UTC))
    with pytest.raises(IntegrityError):  # misma sesion
        db_session.flush()
    db_session.rollback()


def test_two_pending_snapshots_for_the_same_client_are_rejected(db_session, tenant, admin_user):
    client = make_client(db_session, tenant)
    credential = make_client_sol_credential(db_session, tenant, client, admin_user)
    s1 = make_sol_access_session(db_session, tenant, client, credential, admin_user, status="SUCCEEDED")
    s2 = make_sol_access_session(db_session, tenant, client, credential, admin_user, status="SUCCEEDED")
    _snapshot(db_session, tenant, client, s1)
    db_session.flush()
    _snapshot(db_session, tenant, client, s2)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_snapshot_cannot_cross_tenants(db_session, tenant, admin_user, base):
    client, _cr, session_row = base
    other, other_client = _second_tenant_with_client(db_session)
    _snapshot(db_session, tenant, other_client, session_row)  # cliente de OTRO tenant
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_snapshot_reviewer_and_session_must_belong_to_the_same_tenant(db_session, tenant, base):
    from app.core.security import hash_password
    from app.models.user import User

    client, _cr, session_row = base
    other, _oc = _second_tenant_with_client(db_session)
    foreign = User(tenant_id=other.id, email=f"f-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev", full_name="Ajeno",
                   password_hash=hash_password("TestPassword123!"), role="admin", is_active=True)
    db_session.add(foreign)
    db_session.flush()
    _snapshot(db_session, tenant, client, session_row, review_status="ACCEPTED", reviewed_by=foreign.id, reviewed_at=datetime.now(UTC))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_onboarding_one_active_per_client_valid_status_and_tenant_integrity(db_session, tenant, admin_user):
    client = make_client(db_session, tenant)
    db_session.add(ClientOnboarding(tenant_id=tenant.id, client_id=client.id, created_by=admin_user.id, status="IN_PROGRESS"))
    db_session.flush()
    db_session.add(ClientOnboarding(tenant_id=tenant.id, client_id=client.id, created_by=admin_user.id, status="AWAITING_REVIEW"))
    with pytest.raises(IntegrityError):  # dos altas activas para el mismo cliente
        db_session.flush()
    db_session.rollback()


def test_onboarding_cross_tenant_client_is_rejected(db_session, tenant, admin_user):
    other, other_client = _second_tenant_with_client(db_session)
    db_session.add(ClientOnboarding(tenant_id=tenant.id, client_id=other_client.id, created_by=admin_user.id, status="IN_PROGRESS"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_onboarding_status_is_validated_by_the_database(db_session, tenant, admin_user):
    client = make_client(db_session, tenant)
    db_session.add(ClientOnboarding(tenant_id=tenant.id, client_id=client.id, created_by=admin_user.id, status="NOPE"))
    with pytest.raises(IntegrityError):
        db_session.flush()


@pytest.mark.parametrize("destination", ["SIRE", "SIRE_RVIE", "SIRE_RCE", "SIRE_GENERACION_REGISTROS", "DECLARACIONES_Y_PAGOS", "DECLARA_FACIL", "TRAMITES_Y_CONSULTAS"])
def test_m3_known_destinations_are_accepted_by_the_database_without_enabling_them(db_session, tenant, admin_user, destination):
    _scoped_session(db_session, tenant, admin_user, destination=destination)
    db_session.flush()  # la BD ya no es un segundo catalogo desalineado; la habilitacion la decide el backend
