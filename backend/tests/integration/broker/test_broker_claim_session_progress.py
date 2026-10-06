"""Fase 5, Bloque 5.2, Domain E3.1.1 -- el claim EXITOSO es el evento
autoritativo `WAITING_FOR_AGENT -> IN_PROGRESS` (mismo lock/transaccion que el
claim). Broker real + PostgreSQL real + mTLS real; solo credenciales ficticias."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.broker.dispatch import dispatch_job_to_agent
from app.models.sol_agent_access import SolAccessSession, SolAgentJob
from app.services import sol_access_session_service as session_service
from tests.integration.broker._secret_delivery_helpers import commit_sol_access_session
from tests.integration.broker.conftest import cleanup_tenant
from tests.integration.broker.test_broker_secret_delivery_core import _authorize, _pair_agent


def _state(session_factory, session_id):
    s = session_factory()
    try:
        row = s.execute(select(SolAccessSession).where(SolAccessSession.id == session_id)).scalar_one()
        return row.status, row.version, row.terminal_reason_code
    finally:
        s.close()


def _job_status(session_factory, job_id):
    s = session_factory()
    try:
        return s.execute(select(SolAgentJob.status).where(SolAgentJob.id == job_id)).scalar_one()
    finally:
        s.close()


def _dispatch(session_factory, running_broker, setup, agent, session_id=None):
    session_id = session_id or setup["session_id"]
    ds = session_factory()
    try:
        job_id = dispatch_job_to_agent(
            ds, tenant_id=setup["tenant_id"], session_id=session_id, agent_id=agent.agent_id,
            pending_dispatch=running_broker.pending_dispatch,
        )
        ds.commit()
    finally:
        ds.close()
    return job_id


def _new_session(session_factory, setup):
    return commit_sol_access_session(
        session_factory, tenant_id=setup["tenant_id"], client_id=setup["client_id"],
        credential_id=setup["credential_id"], requester_id=setup["admin_id"],
    )


def _service(session_factory, fn, **kw):
    db = session_factory()
    try:
        out = fn(db, tenant_id=kw.pop("tenant_id"), **kw)
        db.commit()
        return out
    finally:
        db.close()


def test_A_successful_claim_moves_session_waiting_for_agent_to_in_progress(session_factory, running_broker, e1b_setup):
    s = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, s["tenant_id"], s["admin_id"])
        job_id = _dispatch(session_factory, running_broker, s, agent)
        assert _state(session_factory, s["session_id"])[0] == "WAITING_FOR_AGENT"
        assert agent.claim(job_id).status_code == 200
        assert _state(session_factory, s["session_id"])[0] == "IN_PROGRESS"
        assert _job_status(session_factory, job_id) == "CLAIMED"
        db = session_factory()
        try:
            row = db.execute(select(SolAccessSession).where(SolAccessSession.id == s["session_id"])).scalar_one()
            assert row.started_at is not None
        finally:
            db.close()
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_B_D_rejected_claims_never_mutate_the_session(session_factory, running_broker, e1b_setup):
    s = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, s["tenant_id"], s["admin_id"])
        other = _pair_agent(session_factory, running_broker, s["tenant_id"], s["admin_id"])
        job_id = _dispatch(session_factory, running_broker, s, agent)
        before = _state(session_factory, s["session_id"])
        assert before[0] == "WAITING_FOR_AGENT"
        # D: agente equivocado
        assert other.claim(job_id).status_code == 404
        assert _state(session_factory, s["session_id"]) == before
        # B: job inexistente / cuerpo del claim invalido
        assert agent.claim(uuid.uuid4()).status_code == 404
        assert _state(session_factory, s["session_id"]) == before
        # el claim legitimo todavia funciona y es el UNICO que mueve la sesion
        assert agent.claim(job_id).status_code == 200
        assert _state(session_factory, s["session_id"])[0] == "IN_PROGRESS"
        # reclamo duplicado (replay): rechazado, sin tocar la sesion
        after = _state(session_factory, s["session_id"])
        assert agent.claim(job_id).status_code == 404
        assert _state(session_factory, s["session_id"]) == after
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_C_stale_connection_claim_never_mutates_the_session(session_factory, running_broker, e1b_setup):
    s = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, s["tenant_id"], s["admin_id"])
        job_id = _dispatch(session_factory, running_broker, s, agent)
        before = _state(session_factory, s["session_id"])
        old = agent.connection_id
        assert agent.hello().status_code == 200  # la conexion anterior queda obsoleta
        agent.connection_id = old
        assert agent.claim(job_id).status_code == 409
        assert _state(session_factory, s["session_id"]) == before
        assert _job_status(session_factory, job_id) == "PENDING"
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def _terminal_then_claim(session_factory, running_broker, s, make_terminal, expected_status):
    agent = _pair_agent(session_factory, running_broker, s["tenant_id"], s["admin_id"])
    job_id = _dispatch(session_factory, running_broker, s, agent)
    make_terminal(job_id)
    status_before = _state(session_factory, s["session_id"])
    assert status_before[0] == expected_status
    response = agent.claim(job_id)
    assert response.status_code in {404, 409}, response.text
    assert _state(session_factory, s["session_id"]) == status_before  # NO resucitada ni modificada
    assert _job_status(session_factory, job_id) != "CLAIMED"


def test_E_cancelled_session_cannot_become_in_progress(session_factory, running_broker, e1b_setup):
    s = e1b_setup
    try:
        _terminal_then_claim(
            session_factory, running_broker, s,
            lambda job_id: _service(session_factory, session_service.cancel_session, tenant_id=s["tenant_id"],
                                    session_id=s["session_id"], actor_user_id=s["admin_id"]),
            "CANCELLED",
        )
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_F_expired_session_cannot_become_in_progress(session_factory, running_broker, e1b_setup):
    s = e1b_setup

    def _expire(job_id):
        db = session_factory()
        try:
            row = db.execute(select(SolAccessSession).where(SolAccessSession.id == s["session_id"])).scalar_one()
            row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
            db.commit()
            session_service.expire_session(db, tenant_id=s["tenant_id"], session_id=s["session_id"])
            db.commit()
        finally:
            db.close()

    try:
        _terminal_then_claim(session_factory, running_broker, s, _expire, "EXPIRED")
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_expired_by_clock_but_not_yet_transitioned_cannot_be_claimed(session_factory, running_broker, e1b_setup):
    """Vencida por TTL aunque nadie ejecuto aun `expire_session`: el claim se rechaza y la sesion no se mueve."""
    s = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, s["tenant_id"], s["admin_id"])
        job_id = _dispatch(session_factory, running_broker, s, agent)
        db = session_factory()
        try:
            row = db.execute(select(SolAccessSession).where(SolAccessSession.id == s["session_id"])).scalar_one()
            row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
            db.commit()
        finally:
            db.close()
        before = _state(session_factory, s["session_id"])
        assert agent.claim(job_id).status_code in {404, 409}
        assert _state(session_factory, s["session_id"]) == before
        assert before[0] == "WAITING_FOR_AGENT"
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_G_failed_session_cannot_become_in_progress(session_factory, running_broker, e1b_setup):
    s = e1b_setup
    try:
        _terminal_then_claim(
            session_factory, running_broker, s,
            lambda job_id: _service(session_factory, session_service.report_failed, tenant_id=s["tenant_id"],
                                    session_id=s["session_id"], expected_version=_state(session_factory, s["session_id"])[1],
                                    terminal_reason_code="PORTAL_CHANGED"),
            "FAILED",
        )
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_H_succeeded_session_cannot_become_in_progress(session_factory, running_broker, e1b_setup):
    s = e1b_setup

    def _succeed(job_id):
        _service(session_factory, session_service.start_progress, tenant_id=s["tenant_id"], session_id=s["session_id"],
                 expected_version=_state(session_factory, s["session_id"])[1])
        _service(session_factory, session_service.report_succeeded, tenant_id=s["tenant_id"], session_id=s["session_id"],
                 expected_version=_state(session_factory, s["session_id"])[1])

    try:
        _terminal_then_claim(session_factory, running_broker, s, _succeed, "SUCCEEDED")
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_I_normal_success_waiting_for_agent_claim_in_progress_succeeded(session_factory, running_broker, e1b_setup):
    s = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, s["tenant_id"], s["admin_id"])
        job_id = _dispatch(session_factory, running_broker, s, agent)
        seen = [_state(session_factory, s["session_id"])[0]]
        assert agent.claim(job_id).status_code == 200
        seen.append(_state(session_factory, s["session_id"])[0])
        _authorize(session_factory, running_broker, s, job_id)
        assert agent.request_credential(job_id).status_code == 200
        r = agent.report_status(job_id, "COMPLETED", session_id=s["session_id"])
        assert r.status_code == 200, r.text
        seen.append(_state(session_factory, s["session_id"])[0])
        assert seen == ["WAITING_FOR_AGENT", "IN_PROGRESS", "SUCCEEDED"]
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_J_challenge_waiting_for_agent_in_progress_waiting_for_user_then_succeeded(
    session_factory, running_broker, e1b_setup
):
    s = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, s["tenant_id"], s["admin_id"])
        job_id = _dispatch(session_factory, running_broker, s, agent)
        seen = [_state(session_factory, s["session_id"])[0]]
        assert agent.claim(job_id).status_code == 200
        seen.append(_state(session_factory, s["session_id"])[0])
        _authorize(session_factory, running_broker, s, job_id)
        assert agent.request_credential(job_id).status_code == 200
        assert agent.report_status(job_id, "WAITING_FOR_USER", session_id=s["session_id"]).status_code == 200
        seen.append(_state(session_factory, s["session_id"])[0])
        assert _job_status(session_factory, job_id) == "CLAIMED"
        assert agent.report_status(job_id, "COMPLETED", session_id=s["session_id"]).status_code == 200
        seen.append(_state(session_factory, s["session_id"])[0])
        assert seen == ["WAITING_FOR_AGENT", "IN_PROGRESS", "WAITING_FOR_USER", "SUCCEEDED"]
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_K_claim_wins_then_cancel_transitions_from_in_progress(session_factory, running_broker, e1b_setup):
    s = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, s["tenant_id"], s["admin_id"])
        job_id = _dispatch(session_factory, running_broker, s, agent)
        assert agent.claim(job_id).status_code == 200
        assert _state(session_factory, s["session_id"])[0] == "IN_PROGRESS"
        _service(session_factory, session_service.cancel_session, tenant_id=s["tenant_id"], session_id=s["session_id"],
                 actor_user_id=s["admin_id"])
        assert _state(session_factory, s["session_id"])[0] == "CANCELLED"
        assert _job_status(session_factory, job_id) == "CANCELLED"
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def _race(session_factory, running_broker, s, agent, session_id, competitor):
    job_id = _dispatch(session_factory, running_broker, s, agent, session_id)
    barrier = threading.Barrier(2)
    box: dict = {}

    def _claim():
        barrier.wait()
        box["claim"] = agent.claim(job_id).status_code

    def _other():
        barrier.wait()
        box["other"] = competitor(session_id)

    threads = [threading.Thread(target=_claim), threading.Thread(target=_other)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    return job_id, box


def test_K_claim_cancel_race_always_ends_cancelled_and_consistent(session_factory, running_broker, e1b_setup):
    s = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, s["tenant_id"], s["admin_id"])
        for i in range(4):
            sid = s["session_id"] if i == 0 else _new_session(session_factory, s)
            job_id, box = _race(
                session_factory, running_broker, s, agent, sid,
                lambda session_id: _service(session_factory, session_service.cancel_session,
                                            tenant_id=s["tenant_id"], session_id=session_id,
                                            actor_user_id=s["admin_id"]).status,
            )
            status, _v, _r = _state(session_factory, sid)
            assert status == "CANCELLED", (i, box, status)  # nunca queda IN_PROGRESS huerfana ni SUCCEEDED
            assert _job_status(session_factory, job_id) == "CANCELLED", (i, box)
            assert box["claim"] in {200, 404, 409}
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_L_claim_expiry_race_always_ends_expired_and_consistent(session_factory, running_broker, e1b_setup):
    s = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, s["tenant_id"], s["admin_id"])
        for i in range(4):
            sid = s["session_id"] if i == 0 else _new_session(session_factory, s)
            db = session_factory()
            try:
                row = db.execute(select(SolAccessSession).where(SolAccessSession.id == sid)).scalar_one()
                row.expires_at = datetime.now(UTC) + timedelta(milliseconds=150)  # vence durante la carrera
                db.commit()
            finally:
                db.close()

            def _expire(session_id):
                import time

                time.sleep(0.2)
                return _service(session_factory, session_service.expire_session, tenant_id=s["tenant_id"],
                                session_id=session_id).status

            job_id, box = _race(session_factory, running_broker, s, agent, sid, _expire)
            status, _v, reason = _state(session_factory, sid)
            assert status == "EXPIRED" and reason == "SESSION_EXPIRED", (i, box, status)
            assert _job_status(session_factory, job_id) in {"CANCELLED", "PENDING", "EXPIRED"}, (i, box)
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])


def test_credential_gate_semantics_unchanged_after_claim_promotion(session_factory, running_broker, e1b_setup):
    """Sin autorizacion no hay entrega aunque la sesion ya este IN_PROGRESS; la entrega sigue siendo de un solo uso."""
    s = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, s["tenant_id"], s["admin_id"])
        job_id = _dispatch(session_factory, running_broker, s, agent)
        assert agent.claim(job_id).status_code == 200
        assert _state(session_factory, s["session_id"])[0] == "IN_PROGRESS"
        assert agent.request_credential(job_id).status_code == 409  # sin grant: nada que entregar
        _authorize(session_factory, running_broker, s, job_id)
        assert agent.request_credential(job_id).status_code == 200
        assert agent.request_credential(job_id).status_code == 409  # un solo uso
    finally:
        cleanup_tenant(session_factory, s["tenant_id"])
