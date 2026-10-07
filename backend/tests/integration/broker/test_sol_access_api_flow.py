"""Fase 5, Bloque 5.2, Domain F: API de usuario del acceso SOL supervisado + autorizacion JUST-IN-TIME tras
`PREAUTH_READY`. Broker REAL + PostgreSQL REAL + mTLS REAL + boveda REAL (contrasena ficticia, KDF rapido SOLO
en pruebas) + el Agente de prueba. Solo credenciales ficticias. SIN SUNAT."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import sol_vault_crypto as crypto
from app.db.session import get_db
from app.main import app
from app.models.sol_agent_access import SolAccessSession, SolAgentJob
from app.models.user import User
from tests.integration.broker._secret_delivery_helpers import (
    FAST_ARGON2_PARAMS,
    TEST_SOL_PASSWORD_DO_NOT_USE,
    TEST_SOL_USER,
    commit_client,
)
from tests.integration.broker.conftest import cleanup_tenant, commit_tenant_user
from tests.integration.broker.test_broker_secret_delivery_core import _pair_agent

_VAULT_PASSWORD = "contrasena-vault-ficticia-f-1234"



def _purge_login_rows(session_factory, tenant_id) -> None:
    """El login real por API deja filas de intentos/eventos de seguridad que `cleanup_tenant` no conoce."""
    from sqlalchemy import delete

    from app.models.login_attempt import LoginAttempt
    from app.models.security_event import SecurityEvent

    s = session_factory()
    try:
        s.execute(delete(LoginAttempt).where(LoginAttempt.tenant_id == tenant_id))
        s.execute(delete(SecurityEvent).where(SecurityEvent.tenant_id == tenant_id))
        s.commit()
    finally:
        s.close()

@pytest.fixture()
def world(session_factory, running_broker, monkeypatch):
    monkeypatch.setattr(crypto, "production_argon2_params", lambda: FAST_ARGON2_PARAMS)

    def _db():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    app.state.sol_broker = running_broker
    created: list[uuid.UUID] = []

    def make_user_client(tenant_id, user_id) -> TestClient:
        s = session_factory()
        try:
            email = s.get(User, user_id).email
        finally:
            s.close()
        c = TestClient(app)
        r = c.post("/api/v1/auth/login", json={"email": email, "password": "TestPassword123!"})
        assert r.status_code == 200, r.text
        c.headers.update({"X-CSRF-Token": r.cookies.get("csrf_token")})
        return c

    def build(*, with_vault=True, with_credential=True, with_agent=True) -> dict:
        setup = commit_tenant_user(session_factory)
        created.append(setup["tenant_id"])
        api = make_user_client(setup["tenant_id"], setup["admin_id"])
        client_id, _ruc = commit_client(session_factory, setup["tenant_id"])
        out = {"api": api, "tenant_id": setup["tenant_id"], "admin_id": setup["admin_id"], "client_id": client_id}
        if with_vault:
            r = api.post("/api/v1/sol-vault/initialize", json={"personal_vault_password": _VAULT_PASSWORD})
            assert r.status_code == 200, r.text
            assert api.post("/api/v1/sol-vault/recovery/confirm",
                            json={"recovery_code": r.json()["recovery_code"]}).status_code == 200
        if with_vault and with_credential:
            r = api.post(f"/api/v1/clients/{client_id}/sol-credential", json={
                "credential_kind": "SECONDARY", "sol_username": TEST_SOL_USER,
                "sol_password": TEST_SOL_PASSWORD_DO_NOT_USE, "authorization_basis": "Autorizacion de prueba F"})
            assert r.status_code in {200, 201}, r.text
        if with_agent:
            out["agent"] = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
            assert out["agent"].heartbeat().status_code == 200  # el agente real envia heartbeat tras el HELLO
        return out

    yield build, make_user_client, session_factory
    app.dependency_overrides.pop(get_db, None)
    if hasattr(app.state, "sol_broker"):
        del app.state.sol_broker
    for tenant_id in created:
        _purge_login_rows(session_factory, tenant_id)
        cleanup_tenant(session_factory, tenant_id)


def _start(w, key=None):
    return w["api"].post("/api/v1/sol-access/sessions", json={
        "client_id": str(w["client_id"]), "idempotency_key": str(key or uuid.uuid4())})


def _db_session(session_factory, session_id):
    s = session_factory()
    try:
        return s.execute(select(SolAccessSession).where(SolAccessSession.id == session_id)).scalar_one()
    finally:
        s.close()


def _claim(w, session_json):
    agent = w["agent"]
    job_id = uuid.UUID(agent.jobs_next().json()["job_id"])
    assert agent.claim(job_id).status_code == 200
    return job_id


def test_full_user_flow_session_claim_preauth_credential_success(world, running_broker):
    build, _mk, sf = world
    w = build()
    r = _start(w)
    assert r.status_code == 201, r.text
    session = r.json()
    assert session["status"] == "WAITING_FOR_AGENT" and session["is_terminal"] is False
    sid = uuid.UUID(session["id"])

    job_id = _claim(w, session)
    assert w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()["status"] == "IN_PROGRESS"

    # NO hay grant antes de PREAUTH_READY (ni al crear la sesion, ni al despachar, ni al reclamar)
    assert running_broker.secret_delivery_grants.peek(tenant_id=w["tenant_id"], job_id=job_id) is None
    assert w["agent"].request_credential(job_id).status_code == 409

    ready = w["agent"].report_status(job_id, "PREAUTH_READY", session_id=sid)
    assert ready.status_code == 200, ready.text
    assert ready.json()["grant_authorized"] is True
    assert running_broker.secret_delivery_grants.peek(tenant_id=w["tenant_id"], job_id=job_id) is not None

    delivered = w["agent"].request_credential(job_id)
    assert delivered.status_code == 200, delivered.text
    assert w["agent"].request_credential(job_id).status_code == 409  # un solo uso
    assert w["agent"].report_status(job_id, "WAITING_FOR_USER", session_id=sid).status_code == 200
    assert w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()["status"] == "WAITING_FOR_USER"
    assert w["agent"].report_status(job_id, "COMPLETED", session_id=sid).status_code == 200
    final = w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()
    assert final["status"] == "SUCCEEDED" and final["operation_result"] == "LANDING_REACHED" and final["is_terminal"]
    # la sesion mas reciente del cliente es esta (refresco de pagina)
    assert w["api"].get(f"/api/v1/sol-access/clients/{w['client_id']}/session").json()["id"] == str(sid)


@pytest.mark.parametrize("failure", ["vault_locked", "no_context", "user_inactive", "session_cancelled", "stale_connection"])
def test_preauth_failure_never_creates_grant_nor_delivers(world, running_broker, failure):
    build, _mk, sf = world
    w = build()
    sid = uuid.UUID(_start(w).json()["id"])
    job_id = _claim(w, None)
    if failure == "vault_locked":
        assert w["api"].post("/api/v1/sol-vault/lock").status_code == 200
    elif failure == "no_context":
        running_broker.session_authorizations.clear()
    elif failure == "user_inactive":
        s = sf()
        try:
            user = s.get(User, w["admin_id"])
            user.is_active = False
            user.deactivated_at = datetime.now(UTC)
            user.deactivated_by = w["admin_id"]
            user.deactivation_reason = "prueba"
            s.commit()
        finally:
            s.close()
    elif failure == "session_cancelled":
        assert w["api"].post(f"/api/v1/sol-access/sessions/{sid}/cancel").status_code == 200
    elif failure == "stale_connection":
        old = w["agent"].connection_id
        assert w["agent"].hello().status_code == 200
        w["agent"].connection_id = old
    r = w["agent"].report_status(job_id, "PREAUTH_READY", session_id=sid)
    assert r.status_code in {404, 409}, (failure, r.status_code)
    assert running_broker.secret_delivery_grants.peek(tenant_id=w["tenant_id"], job_id=job_id) is None  # GRANT_CREATED=NO
    if failure == "stale_connection":
        w["agent"].hello()
    assert w["agent"].request_credential(job_id).status_code in {404, 409}  # CREDENTIAL_DELIVERY_COUNT=0
    s = sf()
    try:
        assert s.execute(select(SolAgentJob.credential_delivered_at).where(SolAgentJob.id == job_id)).scalar_one() is None
    finally:
        s.close()


def test_preauth_ready_from_wrong_agent_or_wrong_session_is_rejected(world, running_broker):
    build, _mk, sf = world
    w = build()
    other = _pair_agent(sf, running_broker, w["tenant_id"], w["admin_id"])
    assert other.heartbeat().status_code == 200
    sid = uuid.UUID(_start(w).json()["id"])
    # el backend elige el agente conectado mas reciente: se identifica al DUENO del job y al ajeno
    owner, stranger = None, None
    for candidate, rest in ((w["agent"], other), (other, w["agent"])):
        job = candidate.jobs_next().json().get("job_id")
        if job:
            owner, stranger, job_id = candidate, rest, uuid.UUID(job)
            break
    assert owner is not None and owner.claim(job_id).status_code == 200
    assert stranger.report_status(job_id, "PREAUTH_READY").status_code == 404
    assert owner.report_status(job_id, "PREAUTH_READY", session_id=uuid.uuid4()).status_code == 404
    assert running_broker.secret_delivery_grants.peek(tenant_id=w["tenant_id"], job_id=job_id) is None
    assert _db_session(sf, sid).status == "IN_PROGRESS"  # sesion intacta


def test_preauth_ready_before_claim_is_rejected_and_repeats_create_a_single_grant(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid = uuid.UUID(_start(w).json()["id"])
    job_id = uuid.UUID(w["agent"].jobs_next().json()["job_id"])
    # aun sin claim: el job no esta CLAIMED ni la sesion IN_PROGRESS
    assert w["agent"].report_status(job_id, "PREAUTH_READY", session_id=sid).status_code in {404, 409}
    assert running_broker.secret_delivery_grants.peek(tenant_id=w["tenant_id"], job_id=job_id) is None
    assert w["agent"].claim(job_id).status_code == 200
    assert w["agent"].report_status(job_id, "PREAUTH_READY", session_id=sid).status_code == 200
    assert w["agent"].report_status(job_id, "PREAUTH_READY", session_id=sid).status_code == 200  # idempotente
    assert w["agent"].request_credential(job_id).status_code == 200
    assert w["agent"].report_status(job_id, "PREAUTH_READY", session_id=sid).status_code == 409  # ya entregada


def test_preflight_rejections_are_authoritative_and_create_nothing(world, running_broker):
    build, _mk, sf = world
    # sin agente conectado
    w = build(with_agent=False)
    r = _start(w)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SOL_AGENT_UNAVAILABLE"
    # sin credencial
    w2 = build(with_credential=False)
    r = _start(w2)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SOL_CREDENTIAL_NOT_CONFIGURED"
    # boveda bloqueada
    w3 = build()
    assert w3["api"].post("/api/v1/sol-vault/lock").status_code == 200
    r = _start(w3)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SOL_VAULT_LOCKED"
    s = sf()
    try:
        assert s.execute(select(SolAccessSession).where(SolAccessSession.tenant_id.in_(
            [w["tenant_id"], w2["tenant_id"], w3["tenant_id"]]))).first() is None
    finally:
        s.close()
    # broker deshabilitado
    del app.state.sol_broker
    r = _start(build(with_agent=False))
    assert r.status_code == 503 and r.json()["detail"]["code"] == "SOL_BROKER_UNAVAILABLE"


def test_duplicate_start_is_idempotent_one_logical_session_and_one_job(world, running_broker):
    build, _mk, sf = world
    w = build()
    key = uuid.uuid4()
    a, b = _start(w, key), _start(w, key)
    assert a.status_code == 201 and b.status_code == 200 and a.json()["id"] == b.json()["id"]
    sid = uuid.UUID(a.json()["id"])
    s = sf()
    try:
        assert len(s.execute(select(SolAgentJob).where(SolAgentJob.session_id == sid)).scalars().all()) == 1
    finally:
        s.close()
    # misma clave con OTRO cliente => conflicto de idempotencia, nunca una sesion nueva
    other_client, _ = commit_client(sf, w["tenant_id"])
    r = w["api"].post("/api/v1/sol-access/sessions", json={"client_id": str(other_client), "idempotency_key": str(key)})
    assert r.status_code in {404, 409}


def test_concurrent_double_click_produces_one_logical_session(world, running_broker):
    build, _mk, sf = world
    w = build()
    key = uuid.uuid4()
    barrier = threading.Barrier(2)
    results: list = []

    def _go():
        barrier.wait()
        results.append(_start(w, key))

    threads = [threading.Thread(target=_go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    ok = [r for r in results if r.status_code in {200, 201}]
    assert ok, [r.text for r in results]
    ids = {r.json()["id"] for r in ok}
    assert len(ids) == 1
    s = sf()
    try:
        sessions = s.execute(select(SolAccessSession).where(SolAccessSession.tenant_id == w["tenant_id"])).scalars().all()
        assert len(sessions) == 1
        assert len(s.execute(select(SolAgentJob).where(SolAgentJob.session_id == sessions[0].id)).scalars().all()) == 1
    finally:
        s.close()


def test_second_active_session_for_the_same_client_is_rejected(world, running_broker):
    build, _mk, sf = world
    w = build()
    assert _start(w).status_code == 201
    r = _start(w)  # clave NUEVA mientras hay una sesion activa
    assert r.status_code == 409
    assert r.json()["detail"]["code"] in {"SOL_SESSION_ALREADY_ACTIVE", "SOL_AGENT_UNAVAILABLE"}


def test_cancel_rules_and_terminal_session_cannot_be_cancelled_again(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid = _start(w).json()["id"]
    r = w["api"].post(f"/api/v1/sol-access/sessions/{sid}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED" and r.json()["is_terminal"]
    again = w["api"].post(f"/api/v1/sol-access/sessions/{sid}/cancel")
    assert again.status_code in {200, 409}  # idempotente por la autoridad existente o rechazo explicito
    assert w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()["status"] == "CANCELLED"
    assert w["api"].post(f"/api/v1/sol-access/sessions/{uuid.uuid4()}/cancel").status_code == 404


def test_late_agent_success_after_ui_cancel_cannot_resurrect_the_session(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid = uuid.UUID(_start(w).json()["id"])
    job_id = _claim(w, None)
    assert w["agent"].report_status(job_id, "PREAUTH_READY", session_id=sid).status_code == 200
    assert w["agent"].request_credential(job_id).status_code == 200
    assert w["api"].post(f"/api/v1/sol-access/sessions/{sid}/cancel").status_code == 200
    assert w["agent"].report_status(job_id, "COMPLETED", session_id=sid).status_code == 409
    assert w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()["status"] == "CANCELLED"


def test_expiry_is_materialised_lazily_by_the_existing_authority(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid = uuid.UUID(_start(w).json()["id"])
    s = sf()
    try:
        row = s.execute(select(SolAccessSession).where(SolAccessSession.id == sid)).scalar_one()
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        s.commit()
    finally:
        s.close()
    got = w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()
    assert got["status"] == "EXPIRED" and got["terminal_reason_code"] == "SESSION_EXPIRED" and got["is_terminal"]
    assert w["api"].get(f"/api/v1/sol-access/clients/{w['client_id']}/session").json()["status"] == "EXPIRED"


def test_tenant_isolation_sessions_clients_and_agents(world, running_broker):
    build, _mk, sf = world
    a, b = build(), build()
    sid = _start(a).json()["id"]
    assert b["api"].get(f"/api/v1/sol-access/sessions/{sid}").status_code == 404
    assert b["api"].post(f"/api/v1/sol-access/sessions/{sid}/cancel").status_code == 404
    assert b["api"].get(f"/api/v1/sol-access/clients/{a['client_id']}/session").status_code == 404
    r = b["api"].post("/api/v1/sol-access/sessions", json={"client_id": str(a["client_id"]),
                                                          "idempotency_key": str(uuid.uuid4())})
    assert r.status_code == 404
    agents_b = b["api"].get("/api/v1/sol-access/agents").json()["agents"]
    assert str(a["agent"].agent_id) not in {x["id"] for x in agents_b}
    assert a["api"].get(f"/api/v1/sol-access/sessions/{sid}").status_code == 200


def test_agents_list_exposes_only_safe_metadata_and_real_availability(world, running_broker):
    build, _mk, sf = world
    w = build()
    data = w["api"].get("/api/v1/sol-access/agents").json()
    assert data["broker_enabled"] is True
    [agent] = data["agents"]
    assert set(agent) == {"id", "display_name", "status", "paired_at", "last_seen_at", "available"}
    assert agent["available"] is True and agent["status"] == "ACTIVE"
    _start(w)  # job activo => el agente ya no esta disponible para otra sesion
    assert w["api"].get("/api/v1/sol-access/agents").json()["agents"][0]["available"] is False


def test_no_secret_in_any_api_response_or_session_schema(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid = uuid.UUID(_start(w).json()["id"])
    job_id = _claim(w, None)
    assert w["agent"].report_status(job_id, "PREAUTH_READY", session_id=sid).status_code == 200
    assert w["agent"].request_credential(job_id).status_code == 200
    bodies = [
        w["api"].get(f"/api/v1/sol-access/sessions/{sid}").text,
        w["api"].get(f"/api/v1/sol-access/clients/{w['client_id']}/session").text,
        w["api"].get("/api/v1/sol-access/agents").text,
        w["api"].get(f"/api/v1/clients/{w['client_id']}/sol-credential/status").text,
        w["api"].get("/api/v1/sol-vault/status").text,
    ]
    for body in bodies:
        assert TEST_SOL_USER not in body and TEST_SOL_PASSWORD_DO_NOT_USE not in body and _VAULT_PASSWORD not in body
    assert set(w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()) == {
        "id", "client_id", "scope", "status", "terminal_reason_code", "operation_result", "created_at", "started_at",
        "waiting_for_user_at", "completed_at", "expires_at", "version", "is_terminal"}


def test_session_creation_requires_csrf_and_authentication(world, running_broker):
    build, _mk, sf = world
    w = build()
    anon = TestClient(app)
    assert anon.post("/api/v1/sol-access/sessions", json={"client_id": str(w["client_id"]),
                                                          "idempotency_key": str(uuid.uuid4())}).status_code in {401, 403}
    no_csrf = _mk(w["tenant_id"], w["admin_id"])
    no_csrf.headers.pop("X-CSRF-Token", None)
    assert no_csrf.post("/api/v1/sol-access/sessions", json={
        "client_id": str(w["client_id"]), "idempotency_key": str(uuid.uuid4())}).status_code == 403
    assert no_csrf.post(f"/api/v1/sol-access/sessions/{uuid.uuid4()}/cancel").status_code == 403
