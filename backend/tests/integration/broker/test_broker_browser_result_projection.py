"""Fase 5, Bloque 5.2, Domain E3.1 -- proyeccion AUTORITATIVA del resultado
semantico del navegador sobre `sol_access_sessions` (matriz A-R). Broker real,
PostgreSQL real, mTLS real, entrega de credencial E1-B real; solo credenciales
ficticias. El Agente solo reporta un evento de lista cerrada; el backend valida
y transiciona."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.sol_agent_access import SolAccessSession, SolAgentJob
from app.services import sol_access_session_service as session_service
from tests.integration.broker.conftest import cleanup_tenant, commit_tenant_user
from tests.integration.broker.test_broker_secret_delivery_core import (
    _authorize,
    _dispatch_and_claim,
    _pair_agent,
)


def _session(session_factory, session_id):
    s = session_factory()
    try:
        row = s.execute(select(SolAccessSession).where(SolAccessSession.id == session_id)).scalar_one()
        return row.status, row.terminal_reason_code, row.operation_result
    finally:
        s.close()


def _job(session_factory, job_id):
    s = session_factory()
    try:
        row = s.execute(select(SolAgentJob).where(SolAgentJob.id == job_id)).scalar_one()
        return row.status, row.credential_delivered_at
    finally:
        s.close()


def _claimed(session_factory, running_broker, e1b_setup):
    agent = _pair_agent(session_factory, running_broker, e1b_setup["tenant_id"], e1b_setup["admin_id"])
    job_id = _dispatch_and_claim(session_factory, running_broker, e1b_setup, agent)
    return agent, job_id


def _deliver(session_factory, running_broker, e1b_setup, agent, job_id):
    _authorize(session_factory, running_broker, e1b_setup, job_id)
    response = agent.request_credential(job_id)
    assert response.status_code == 200, response.text


def test_A_auth_succeeded_projects_to_succeeded_and_completes_job_atomically(
    session_factory, running_broker, e1b_setup
):
    s = e1b_setup
    try:
        agent, job_id = _claimed(session_factory, running_broker, s)
        _deliver(session_factory, running_broker, s, agent, job_id)
        # R: con la credencial entregada pero SIN resultado, ni sesion ni job son exitosos
        assert _session(session_factory, s["session_id"])[0] != "SUCCEEDED"
        assert _job(session_factory, job_id)[0] == "CLAIMED"

        r = agent.report_status(job_id, "COMPLETED", session_id=s["session_id"])
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "COMPLETED" and r.json()["session_status"] == "SUCCEEDED"
        assert _session(session_factory, s["session_id"]) == ("SUCCEEDED", None, "LANDING_REACHED")
        assert _job(session_factory, job_id)[0] == "COMPLETED"
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_B_Q_challenge_projects_to_waiting_for_user_and_job_stays_claimed(session_factory, running_broker, e1b_setup):
    s = e1b_setup
    try:
        agent, job_id = _claimed(session_factory, running_broker, s)
        _deliver(session_factory, running_broker, s, agent, job_id)
        r = agent.report_status(job_id, "WAITING_FOR_USER", session_id=s["session_id"])
        assert r.status_code == 200, r.text
        assert r.json()["session_status"] == "WAITING_FOR_USER"
        assert _session(session_factory, s["session_id"])[0] == "WAITING_FOR_USER"
        assert _job(session_factory, job_id)[0] == "CLAIMED"  # Q: el job NO esta COMPLETED mientras espera
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_C_waiting_for_user_then_success_projects_to_succeeded(session_factory, running_broker, e1b_setup):
    s = e1b_setup
    try:
        agent, job_id = _claimed(session_factory, running_broker, s)
        _deliver(session_factory, running_broker, s, agent, job_id)
        assert agent.report_status(job_id, "WAITING_FOR_USER").status_code == 200
        r = agent.report_status(job_id, "COMPLETED")
        assert r.status_code == 200, r.text
        assert _session(session_factory, s["session_id"])[0] == "SUCCEEDED"
        assert _job(session_factory, job_id)[0] == "COMPLETED"
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_D_waiting_for_user_then_cancel_and_late_result_cannot_resurrect(session_factory, running_broker, e1b_setup):
    s = e1b_setup
    try:
        agent, job_id = _claimed(session_factory, running_broker, s)
        _deliver(session_factory, running_broker, s, agent, job_id)
        assert agent.report_status(job_id, "WAITING_FOR_USER").status_code == 200
        db = session_factory()
        try:
            session_service.cancel_session(db, tenant_id=s["tenant_id"], session_id=s["session_id"],
                                           actor_user_id=s["admin_id"])
            db.commit()
        finally:
            db.close()
        assert _session(session_factory, s["session_id"])[0] == "CANCELLED"
        late = agent.report_status(job_id, "COMPLETED")  # carrera: exito tardio del navegador
        assert late.status_code == 409
        assert _session(session_factory, s["session_id"])[0] == "CANCELLED"  # L: nunca CANCELLED -> SUCCEEDED
        assert _job(session_factory, job_id)[0] == "CANCELLED"
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_E_waiting_for_user_then_expiry_and_late_result_cannot_resurrect(session_factory, running_broker, e1b_setup):
    s = e1b_setup
    try:
        agent, job_id = _claimed(session_factory, running_broker, s)
        _deliver(session_factory, running_broker, s, agent, job_id)
        assert agent.report_status(job_id, "WAITING_FOR_USER").status_code == 200
        db = session_factory()
        try:
            row = db.execute(select(SolAccessSession).where(SolAccessSession.id == s["session_id"])).scalar_one()
            row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
            db.commit()
            session_service.expire_session(db, tenant_id=s["tenant_id"], session_id=s["session_id"])
            db.commit()
        finally:
            db.close()
        assert _session(session_factory, s["session_id"])[0:2] == ("EXPIRED", "SESSION_EXPIRED")
        assert agent.report_status(job_id, "COMPLETED").status_code == 409  # EXPIRED -> SUCCEEDED imposible
        assert _session(session_factory, s["session_id"])[0] == "EXPIRED"
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


@pytest.mark.parametrize("reason", ["PORTAL_CHANGED", "AUTH_FAILED", "BROWSER_CLOSED", "AUTH_HOST_CHANGED",
                                    "HUMAN_CHALLENGE_TIMEOUT", "INTERNAL_ERROR"])
def test_F_G_failures_project_to_failed_with_durable_reason_and_audit(session_factory, running_broker, e1b_setup, reason):
    s = e1b_setup
    try:
        agent, job_id = _claimed(session_factory, running_broker, s)
        r = agent.report_status(job_id, "FAILED", reason_code=reason, session_id=s["session_id"])
        assert r.status_code == 200, r.text
        assert _session(session_factory, s["session_id"])[0:2] == ("FAILED", reason)  # ubicacion durable del motivo
        assert _job(session_factory, job_id)[0] == "FAILED"
        db = session_factory()
        try:
            audits = db.execute(select(AuditLog).where(AuditLog.entity_id == s["session_id"],
                                                       AuditLog.action == "SESSION_FAILED")).scalars().all()
            assert any((a.log_metadata or {}).get("terminal_reason_code") == reason for a in audits)
        finally:
            db.close()
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_H_wrong_agent_result_is_rejected(session_factory, running_broker, e1b_setup):
    s = e1b_setup
    try:
        agent, job_id = _claimed(session_factory, running_broker, s)
        other = _pair_agent(session_factory, running_broker, s["tenant_id"], s["admin_id"])
        assert other.report_status(job_id, "FAILED", reason_code="PORTAL_CHANGED").status_code == 404
        assert _session(session_factory, s["session_id"])[0] != "FAILED"
        assert _job(session_factory, job_id)[0] == "CLAIMED"
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_I_wrong_tenant_result_is_rejected(session_factory, running_broker, e1b_setup):
    s = e1b_setup
    other_tenant = commit_tenant_user(session_factory)
    try:
        agent, job_id = _claimed(session_factory, running_broker, s)
        foreign = _pair_agent(session_factory, running_broker, other_tenant["tenant_id"], other_tenant["admin_id"])
        assert foreign.report_status(job_id, "FAILED", reason_code="PORTAL_CHANGED").status_code == 404
        assert _session(session_factory, s["session_id"])[0] != "FAILED"
    finally:
        cleanup_tenant(session_factory, other_tenant["tenant_id"])
        cleanup_tenant(session_factory, s["tenant_id"])


def test_J_stale_connection_result_is_rejected(session_factory, running_broker, e1b_setup):
    s = e1b_setup
    try:
        agent, job_id = _claimed(session_factory, running_broker, s)
        old_connection = agent.connection_id
        assert agent.hello().status_code == 200  # nueva conexion: la anterior queda obsoleta
        agent.connection_id = old_connection
        r = agent.report_status(job_id, "FAILED", reason_code="PORTAL_CHANGED")
        assert r.status_code == 409
        assert _session(session_factory, s["session_id"])[0] != "FAILED"
        assert _job(session_factory, job_id)[0] == "CLAIMED"
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_K_wrong_job_session_relation_is_rejected(session_factory, running_broker, e1b_setup):
    s = e1b_setup
    try:
        agent, job_id = _claimed(session_factory, running_broker, s)
        r = agent.report_status(job_id, "FAILED", reason_code="PORTAL_CHANGED", session_id=uuid.uuid4())
        assert r.status_code == 404
        assert _session(session_factory, s["session_id"])[0] != "FAILED"
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_L_terminal_session_cannot_be_resurrected(session_factory, running_broker, e1b_setup):
    s = e1b_setup
    try:
        agent, job_id = _claimed(session_factory, running_broker, s)
        assert agent.report_status(job_id, "FAILED", reason_code="PORTAL_CHANGED").status_code == 200
        for status, reason in (("COMPLETED", None), ("WAITING_FOR_USER", None), ("FAILED", "AUTH_FAILED")):
            assert agent.report_status(job_id, status, reason_code=reason).status_code == 409
        assert _session(session_factory, s["session_id"])[0:2] == ("FAILED", "PORTAL_CHANGED")
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_M_N_duplicate_same_result_is_idempotent_and_conflicting_duplicate_rejected(
    session_factory, running_broker, e1b_setup
):
    s = e1b_setup
    try:
        agent, job_id = _claimed(session_factory, running_broker, s)
        _deliver(session_factory, running_broker, s, agent, job_id)
        assert agent.report_status(job_id, "WAITING_FOR_USER").status_code == 200
        assert agent.report_status(job_id, "WAITING_FOR_USER").status_code == 200  # M: idempotente
        assert agent.report_status(job_id, "COMPLETED").status_code == 200
        again = agent.report_status(job_id, "COMPLETED")
        assert again.status_code == 200 and again.json()["session_status"] == "SUCCEEDED"  # M
        assert agent.report_status(job_id, "FAILED", reason_code="PORTAL_CHANGED").status_code == 409  # N
        assert _session(session_factory, s["session_id"])[0] == "SUCCEEDED"
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_N_conflicting_failed_duplicate_is_rejected(session_factory, running_broker, e1b_setup):
    s = e1b_setup
    try:
        agent, job_id = _claimed(session_factory, running_broker, s)
        assert agent.report_status(job_id, "FAILED", reason_code="PORTAL_CHANGED").status_code == 200
        assert agent.report_status(job_id, "FAILED", reason_code="PORTAL_CHANGED").status_code == 200  # idempotente
        assert agent.report_status(job_id, "FAILED", reason_code="AUTH_FAILED").status_code == 409  # conflicto
        assert _session(session_factory, s["session_id"])[1] == "PORTAL_CHANGED"
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_success_without_delivered_credential_is_rejected(session_factory, running_broker, e1b_setup):
    """Un HTTP 200 del agente nunca basta: sin credencial entregada no hay exito."""
    s = e1b_setup
    try:
        agent, job_id = _claimed(session_factory, running_broker, s)
        assert agent.report_status(job_id, "COMPLETED").status_code == 409
        assert _session(session_factory, s["session_id"])[0] != "SUCCEEDED"
        assert _job(session_factory, job_id)[0] == "CLAIMED"
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_O_result_schema_is_a_closed_allowlist_and_carries_no_secret_payload(
    session_factory, running_broker, e1b_setup, caplog
):
    s = e1b_setup
    try:
        agent, job_id = _claimed(session_factory, running_broker, s)
        import httpx

        def raw(body: dict):
            with agent._operational_client() as c:
                return c.post(f"{agent.operational_base_url}/agent/jobs/{job_id}/status",
                              json={"protocol_version": 1, "job_id": str(job_id), **body},
                              headers=agent._connection_headers())

        # campos no reconocidos (secretos/DOM/URL/estado de BD) -> rechazados por el esquema estricto
        for extra in ({"sol_password": "x"}, {"ruc": "x"}, {"html": "<x>"}, {"url": "https://x?token=1"},
                      {"session_state": "SUCCEEDED"}, {"state": "SUCCEEDED"}):
            assert raw({"status": "FAILED", "reason_code": "PORTAL_CHANGED", **extra}).status_code in {400, 422}
        # codigos fuera de la lista cerrada (incluidos los que decide el backend) -> rechazados
        for bad in ("CANCELLED_BY_USER", "SESSION_EXPIRED", "AGENT_REVOKED", "WRONG_TAXPAYER_CONTEXT", "xyz",
                    "PORTAL_CHANGED; DROP", "a" * 500):
            assert raw({"status": "FAILED", "reason_code": bad}).status_code in {400, 422}
        # coherencia status/reason_code
        assert raw({"status": "FAILED"}).status_code in {400, 422}
        assert raw({"status": "COMPLETED", "reason_code": "AUTH_FAILED"}).status_code in {400, 422}
        assert raw({"status": "SUCCEEDED"}).status_code in {400, 422}
        assert _session(session_factory, s["session_id"])[0] != "FAILED"
        assert httpx  # (import usado)
        assert "sol_password" not in caplog.text
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_P_projection_never_reopens_the_credential_delivery_gate(session_factory, running_broker, e1b_setup):
    s = e1b_setup
    try:
        agent, job_id = _claimed(session_factory, running_broker, s)
        _deliver(session_factory, running_broker, s, agent, job_id)
        delivered_at = _job(session_factory, job_id)[1]
        assert delivered_at is not None
        assert agent.report_status(job_id, "WAITING_FOR_USER").status_code == 200
        assert agent.request_credential(job_id).status_code == 409  # sin redelivery mientras espera
        assert agent.report_status(job_id, "COMPLETED").status_code == 200
        assert _job(session_factory, job_id)[1] == delivered_at  # la marca del gate no cambio
        assert running_broker.secret_delivery_grants.peek(tenant_id=s["tenant_id"], job_id=job_id) is None
        assert agent.request_credential(job_id).status_code in {404, 409}  # ni tras el exito (job terminal)
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_failed_result_does_not_reopen_the_gate_either(session_factory, running_broker, e1b_setup):
    s = e1b_setup
    try:
        agent, job_id = _claimed(session_factory, running_broker, s)
        _deliver(session_factory, running_broker, s, agent, job_id)
        assert agent.report_status(job_id, "FAILED", reason_code="BROWSER_CLOSED").status_code == 200
        assert agent.request_credential(job_id).status_code in {404, 409}
        assert running_broker.secret_delivery_grants.peek(tenant_id=s["tenant_id"], job_id=job_id) is None
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_agent_reportable_reason_codes_are_consistent_across_layers():
    from app.broker.protocol import AGENT_REPORTABLE_REASON_CODES as broker_codes
    from app.models.sol_agent_access import SolAccessTerminalReasonCode
    from windows_agent.portal_contract import AGENT_REPORTABLE_REASON_CODES as agent_codes

    assert tuple(broker_codes) == tuple(agent_codes)
    valid = {c.value for c in SolAccessTerminalReasonCode}
    assert set(broker_codes) <= valid
    # el Agente jamas puede reportar lo que el backend decide por su cuenta
    assert not set(broker_codes) & {"CANCELLED_BY_USER", "SESSION_EXPIRED", "AGENT_REVOKED", "JOB_EXPIRED",
                                    "VAULT_LOCKED", "WRONG_TAXPAYER_CONTEXT"}
