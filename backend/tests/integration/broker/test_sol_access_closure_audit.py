"""Domain F -- auditoria de cierre: ciclo de vida de la credencial, PREAUTH_READY JIT (repeticion / cancelacion /
expiracion / sesion terminal), perdida de contexto humano, atomicidad API+despacho, carreras de cancelacion y
RBAC/tenant. Broker REAL + PostgreSQL REAL. Solo credenciales ficticias. SIN SUNAT."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.client_sol_credential import ClientSolCredential
from app.models.sol_agent_access import SolAccessSession, SolAgentJob
from app.routers import sol_access as sol_access_router
from app.services import sol_agent_job_service as job_service
from tests.integration.broker.test_sol_access_api_flow import (  # noqa: F401  (fixture `world` + helpers)
    _claim,
    _db_session,
    _start,
    world,
)


def _credential(sf, tenant_id) -> ClientSolCredential:
    s = sf()
    try:
        return s.execute(select(ClientSolCredential).where(ClientSolCredential.tenant_id == tenant_id)).scalar_one()
    finally:
        s.close()


def _counts(sf, tenant_id) -> tuple[int, int]:
    s = sf()
    try:
        sessions = s.execute(select(func.count()).select_from(SolAccessSession).where(
            SolAccessSession.tenant_id == tenant_id)).scalar_one()
        jobs = s.execute(select(func.count()).select_from(SolAgentJob).where(
            SolAgentJob.tenant_id == tenant_id)).scalar_one()
        return sessions, jobs
    finally:
        s.close()


def _set_credential_status(sf, tenant_id, value: str, w=None) -> None:
    if value == "DISABLED":  # camino legal por la API (mantiene la coherencia de columnas de deshabilitacion)
        cred = _credential(sf, tenant_id)
        r = w["api"].post(f"/api/v1/clients/{w['client_id']}/sol-credential/disable", json={
            "expected_version": cred.version})
        assert r.status_code == 200, r.text
        return
    s = sf()
    try:
        row = s.execute(select(ClientSolCredential).where(ClientSolCredential.tenant_id == tenant_id)).scalar_one()
        row.status = value
        s.commit()
    finally:
        s.close()


# --- ciclo de vida de la credencial ---------------------------------------------------------------------------


def test_needs_validation_is_eligible_for_first_auth_and_status_is_never_mutated(world, running_broker):
    build, _mk, sf = world
    w = build()
    assert _credential(sf, w["tenant_id"]).status == "NEEDS_VALIDATION"
    sid = uuid.UUID(_start(w).json()["id"])
    job_id = _claim(w, None)
    assert w["agent"].report_status(job_id, "PREAUTH_READY", session_id=sid).status_code == 200
    assert w["agent"].request_credential(job_id).status_code == 200
    assert w["agent"].report_status(job_id, "COMPLETED", session_id=sid).status_code == 200
    # AUTH_SUCCEEDED NO promueve a ACTIVE en este bloque (ADR-0068: transicion no definida todavia)
    assert _credential(sf, w["tenant_id"]).status == "NEEDS_VALIDATION"


@pytest.mark.parametrize("reason", ["PORTAL_CHANGED", "BROWSER_CLOSED", "HUMAN_CHALLENGE_TIMEOUT", "CLEANUP_FAILED", "AUTH_FAILED"])
def test_non_auth_failures_never_mutate_the_credential_status(world, running_broker, reason):
    build, _mk, sf = world
    w = build()
    sid = uuid.UUID(_start(w).json()["id"])
    job_id = _claim(w, None)
    assert w["agent"].report_status(job_id, "PREAUTH_READY", session_id=sid).status_code == 200
    assert w["agent"].request_credential(job_id).status_code == 200
    assert w["agent"].report_status(job_id, "FAILED", session_id=sid, reason_code=reason).status_code == 200
    assert _db_session(sf, sid).status == "FAILED"
    assert _credential(sf, w["tenant_id"]).status == "NEEDS_VALIDATION"


@pytest.mark.parametrize("status", ["INVALID", "LOCKED_BY_SUNAT", "PASSWORD_EXPIRED", "DISABLED"])
def test_unusable_credential_states_are_rejected_at_create_and_create_nothing(world, running_broker, status):
    build, _mk, sf = world
    w = build()
    _set_credential_status(sf, w["tenant_id"], status, w)
    r = _start(w)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SOL_CREDENTIAL_NOT_CONFIGURED"
    assert _counts(sf, w["tenant_id"]) == (0, 0)


def test_credential_disabled_between_create_and_preauth_ready_never_gets_a_grant(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid = uuid.UUID(_start(w).json()["id"])
    job_id = _claim(w, None)
    _set_credential_status(sf, w["tenant_id"], "DISABLED", w)
    assert w["agent"].report_status(job_id, "PREAUTH_READY", session_id=sid).status_code == 409
    assert running_broker.secret_delivery_grants.peek(tenant_id=w["tenant_id"], job_id=job_id) is None
    assert w["agent"].request_credential(job_id).status_code in {404, 409}
    assert _credential(sf, w["tenant_id"]).status == "DISABLED"  # no se altera


# --- PREAUTH_READY JIT: repeticion / terminal / cancelacion / expiracion -----------------------------------------


def test_grant_only_appears_after_preauth_ready_and_replay_never_reopens_the_gate(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid = uuid.UUID(_start(w).json()["id"])
    peek = lambda j: running_broker.secret_delivery_grants.peek(tenant_id=w["tenant_id"], job_id=j)  # noqa: E731
    job_id = uuid.UUID(w["agent"].jobs_next().json()["job_id"])
    assert peek(job_id) is None  # GRANT_AT_SESSION_CREATE=NO / GRANT_AT_DISPATCH=NO
    assert w["agent"].claim(job_id).status_code == 200
    assert peek(job_id) is None  # GRANT_AT_CLAIM=NO
    assert w["agent"].report_status(job_id, "PREAUTH_READY", session_id=sid).status_code == 200
    first = peek(job_id)
    assert first is not None  # GRANT_AFTER_PREAUTH_READY=YES
    assert w["agent"].report_status(job_id, "PREAUTH_READY", session_id=sid).status_code == 200
    assert peek(job_id) is first  # la repeticion no crea un segundo grant
    assert w["agent"].request_credential(job_id).status_code == 200
    for _ in range(2):
        assert w["agent"].report_status(job_id, "PREAUTH_READY", session_id=sid).status_code == 409
        assert w["agent"].request_credential(job_id).status_code in {404, 409}  # sin segunda entrega
    assert peek(job_id) is None


@pytest.mark.parametrize("how", ["cancelled", "expired", "succeeded"])
def test_preauth_ready_on_a_terminal_session_is_rejected(world, running_broker, how):
    build, _mk, sf = world
    w = build()
    sid = uuid.UUID(_start(w).json()["id"])
    job_id = _claim(w, None)
    if how == "cancelled":
        assert w["api"].post(f"/api/v1/sol-access/sessions/{sid}/cancel").status_code == 200
    elif how == "expired":
        s = sf()
        try:
            s.execute(select(SolAccessSession).where(SolAccessSession.id == sid)).scalar_one().expires_at = (
                datetime.now(UTC) - timedelta(minutes=1))
            s.commit()
        finally:
            s.close()
        assert w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()["status"] == "EXPIRED"
    else:
        assert w["agent"].report_status(job_id, "PREAUTH_READY", session_id=sid).status_code == 200
        assert w["agent"].request_credential(job_id).status_code == 200
        assert w["agent"].report_status(job_id, "COMPLETED", session_id=sid).status_code == 200
    r = w["agent"].report_status(job_id, "PREAUTH_READY", session_id=sid)
    assert r.status_code in {404, 409}
    if how != "succeeded":
        assert running_broker.secret_delivery_grants.peek(tenant_id=w["tenant_id"], job_id=job_id) is None


def test_preauth_ready_from_another_tenant_agent_is_rejected(world, running_broker):
    build, _mk, sf = world
    a, b = build(), build()
    sid = uuid.UUID(_start(a).json()["id"])
    job_id = _claim(a, None)
    assert b["agent"].report_status(job_id, "PREAUTH_READY", session_id=sid).status_code == 404
    assert running_broker.secret_delivery_grants.peek(tenant_id=a["tenant_id"], job_id=job_id) is None


def test_preauth_ready_message_rejects_any_secret_or_extra_field():
    from pydantic import ValidationError

    from app.broker.protocol import PROTOCOL_VERSION, JobStatusReport

    base = {"protocol_version": PROTOCOL_VERSION, "job_id": str(uuid.uuid4()), "status": "PREAUTH_READY"}
    JobStatusReport.model_validate(base)
    for extra in ({"sol_password": "x"}, {"sol_username": "x"}, {"credential": "x"}, {"token": "x"}):
        with pytest.raises(ValidationError):
            JobStatusReport.model_validate({**base, **extra})
    with pytest.raises(ValidationError):
        JobStatusReport.model_validate({**base, "reason_code": "AUTH_FAILED"})  # reason solo con FAILED


# --- perdida del contexto humano (reinicio del backend) ------------------------------------------------------------


def test_context_loss_fails_closed_and_nothing_recreates_context_or_grant(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid = uuid.UUID(_start(w).json()["id"])
    job_id = _claim(w, None)
    running_broker.session_authorizations.clear()  # equivale a un reinicio del proceso
    for _ in range(2):
        assert w["agent"].report_status(job_id, "PREAUTH_READY", session_id=sid).status_code == 409
        assert w["agent"].request_credential(job_id).status_code in {404, 409}
        assert w["api"].get(f"/api/v1/sol-access/sessions/{sid}").status_code == 200  # leer no recrea contexto
    assert running_broker.session_authorizations.get(tenant_id=w["tenant_id"], session_id=sid) is None
    assert running_broker.secret_delivery_grants.peek(tenant_id=w["tenant_id"], job_id=job_id) is None
    assert _db_session(sf, sid).status == "IN_PROGRESS"  # nada se reintenta solo
    s = sf()
    try:
        assert s.execute(select(SolAgentJob.credential_delivered_at).where(SolAgentJob.id == job_id)).scalar_one() is None
    finally:
        s.close()


# --- atomicidad API + despacho -------------------------------------------------------------------------------------


def test_dispatch_store_failure_leaves_no_orphan_session_or_job(world, running_broker, monkeypatch):
    build, _mk, sf = world
    w = build()

    def boom(*_a, **_k):
        raise RuntimeError("almacen de despacho no disponible")

    monkeypatch.setattr(sol_access_router, "dispatch_job_to_agent", boom)
    from fastapi.testclient import TestClient

    from app.main import app

    api = TestClient(app, raise_server_exceptions=False)
    api.cookies.update(w["api"].cookies)
    api.headers.update(w["api"].headers)
    r = api.post("/api/v1/sol-access/sessions", json={
        "client_id": str(w["client_id"]), "idempotency_key": str(uuid.uuid4())})
    assert r.status_code == 500
    assert _counts(sf, w["tenant_id"]) == (0, 0)  # BROKER/DISPATCH_FAILURE crea sesion huerfana = NO
    assert running_broker.session_authorizations.get(tenant_id=w["tenant_id"], session_id=uuid.uuid4()) is None


def test_agent_vanishing_between_preflight_and_create_leaves_nothing(world, running_broker, monkeypatch):
    build, _mk, sf = world
    w = build()

    def vanished(*_a, **_k):
        raise job_service.SolAgentJobServiceError("el agente ya no esta disponible")

    monkeypatch.setattr(sol_access_router, "dispatch_job_to_agent", vanished)
    r = _start(w)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SOL_SESSION_NOT_STARTABLE"
    assert _counts(sf, w["tenant_id"]) == (0, 0)


def test_broker_disabled_creates_no_session(world, running_broker):
    from app.main import app

    build, _mk, sf = world
    w = build()
    del app.state.sol_broker
    r = _start(w)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "SOL_BROKER_UNAVAILABLE"
    assert _counts(sf, w["tenant_id"]) == (0, 0)


def test_duplicate_idempotency_key_dispatches_exactly_once(world, running_broker, monkeypatch):
    build, _mk, sf = world
    w = build()
    calls: list[int] = []
    real = sol_access_router.dispatch_job_to_agent

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(sol_access_router, "dispatch_job_to_agent", counting)
    key = uuid.uuid4()
    codes = [_start(w, key).status_code for _ in range(5)]
    assert codes == [201, 200, 200, 200, 200]
    assert len(calls) == 1  # IDEMPOTENT_DUPLICATE_DISPATCH_COUNT=1
    assert _counts(sf, w["tenant_id"]) == (1, 1)


# --- carreras: la autoridad del backend gana -------------------------------------------------------------------------


def test_cancel_after_success_or_expiry_cannot_overwrite_the_terminal_state(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid = uuid.UUID(_start(w).json()["id"])
    job_id = _claim(w, None)
    assert w["agent"].report_status(job_id, "PREAUTH_READY", session_id=sid).status_code == 200
    assert w["agent"].request_credential(job_id).status_code == 200
    assert w["agent"].report_status(job_id, "COMPLETED", session_id=sid).status_code == 200
    r = w["api"].post(f"/api/v1/sol-access/sessions/{sid}/cancel")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SOL_SESSION_TERMINAL"
    assert w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()["status"] == "SUCCEEDED"

    w2 = build()
    sid2 = uuid.UUID(_start(w2).json()["id"])
    s = sf()
    try:
        s.execute(select(SolAccessSession).where(SolAccessSession.id == sid2)).scalar_one().expires_at = (
            datetime.now(UTC) - timedelta(minutes=1))
        s.commit()
    finally:
        s.close()
    assert w2["api"].get(f"/api/v1/sol-access/sessions/{sid2}").json()["status"] == "EXPIRED"
    r2 = w2["api"].post(f"/api/v1/sol-access/sessions/{sid2}/cancel")
    assert r2.status_code == 409
    assert w2["api"].get(f"/api/v1/sol-access/sessions/{sid2}").json()["status"] == "EXPIRED"


def test_cancel_discards_human_context_so_a_later_preauth_cannot_authorize(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid = uuid.UUID(_start(w).json()["id"])
    job_id = _claim(w, None)
    assert w["api"].post(f"/api/v1/sol-access/sessions/{sid}/cancel").status_code == 200
    assert running_broker.session_authorizations.get(tenant_id=w["tenant_id"], session_id=sid) is None
    assert w["agent"].report_status(job_id, "PREAUTH_READY", session_id=sid).status_code in {404, 409}


# --- Domain G: un agente matado sin desconexion limpia NO debe figurar disponible (hallazgo del kill abrupto) ----------


def test_connected_but_silent_agent_is_not_available_and_session_start_is_rejected(world, running_broker):
    from app.models.sol_agent_access import SolAgent

    build, _mk, sf = world
    w = build()
    assert w["api"].get("/api/v1/sol-access/agents").json()["agents"][0]["available"] is True
    s = sf()
    try:
        agent = s.execute(select(SolAgent).where(SolAgent.id == w["agent"].agent_id)).scalar_one()
        agent.last_seen_at = datetime.now(UTC) - timedelta(minutes=10)  # sin latidos; el registro aun lo da por conectado
        s.commit()
    finally:
        s.close()
    assert running_broker.connections.is_connected(agent_id=w["agent"].agent_id)
    assert w["api"].get("/api/v1/sol-access/agents").json()["agents"][0]["available"] is False
    r = _start(w)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SOL_AGENT_UNAVAILABLE"
    assert _counts(sf, w["tenant_id"]) == (0, 0)
    assert w["agent"].heartbeat().status_code == 200  # un latido lo vuelve a dejar disponible
    assert w["api"].get("/api/v1/sol-access/agents").json()["agents"][0]["available"] is True
