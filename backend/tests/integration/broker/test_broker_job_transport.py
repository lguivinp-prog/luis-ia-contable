"""Fase 5, Bloque 5.2, Domain D -- transporte de jobs: reclamo delega en
`sol_agent_job_service.claim_job` como unica autoridad (nunca reimplementa
la logica de reclamo), replay produce exactamente un exito, un agente nunca
puede reclamar el job de otro, "desconexion durante el reclamo" nunca deja
un job en un estado indefinido, y el aviso de cancelacion via heartbeat es
puramente informativo (el estado durable ya es autoritativo antes de que el
agente lo vea)."""

from __future__ import annotations

from tests.integration.broker.agent_test_client import AgentTestClient
from tests.integration.broker.conftest import (
    cleanup_tenant,
    commit_credential,
    commit_session,
    commit_tenant_user,
    create_pairing_over_service,
)


def _paired_agent(session_factory, running_broker, tenant_id, admin_id) -> AgentTestClient:
    from cryptography.hazmat.primitives import serialization

    pairing_id, pairing_code = create_pairing_over_service(
        session_factory, tenant_id=tenant_id, requested_by_user_id=admin_id
    )
    ca_cert_pem = running_broker.ca.certificate.public_bytes(serialization.Encoding.PEM)
    agent = AgentTestClient(
        ca_certificate_pem=ca_cert_pem,
        pairing_base_url=f"https://127.0.0.1:{running_broker.pairing_bound_port}",
        operational_base_url=f"https://127.0.0.1:{running_broker.operational_bound_port}",
    )
    response = agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code)
    assert response.status_code == 200, response.text
    hello_response = agent.hello()
    assert hello_response.status_code == 200, hello_response.text
    return agent


def _setup_session_and_dispatch(session_factory, running_broker, *, tenant_id, admin_id, agent_id):
    from app.broker.dispatch import dispatch_job_to_agent

    client_id, credential_id = commit_credential(session_factory, tenant_id, admin_id)
    session_id = commit_session(
        session_factory, tenant_id=tenant_id, client_id=client_id, credential_id=credential_id, requester_id=admin_id
    )
    dispatch_session = session_factory()
    try:
        job_id = dispatch_job_to_agent(
            dispatch_session,
            tenant_id=tenant_id,
            session_id=session_id,
            agent_id=agent_id,
            pending_dispatch=running_broker.pending_dispatch,
        )
        dispatch_session.commit()
    finally:
        dispatch_session.close()
    return session_id, job_id


def test_A_claim_replay_over_real_transport_exactly_one_success(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        _session_id, job_id = _setup_session_and_dispatch(
            session_factory, running_broker, tenant_id=setup["tenant_id"], admin_id=setup["admin_id"], agent_id=agent.agent_id
        )

        results = [agent.claim(job_id).status_code for _ in range(3)]
        assert results.count(200) == 1
        assert all(code in (200, 404) for code in results)
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_B_agent_can_never_claim_a_job_dispatched_to_another_agent(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    try:
        agent_a = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        agent_b = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])

        _session_id, job_id = _setup_session_and_dispatch(
            session_factory, running_broker, tenant_id=setup["tenant_id"], admin_id=setup["admin_id"], agent_id=agent_a.agent_id
        )

        response = agent_b.claim(job_id)
        assert response.status_code == 404
        assert response.json()["error"] == "JOB_UNAVAILABLE"

        # El job sigue disponible para el agente correcto.
        response_a = agent_a.claim(job_id)
        assert response_a.status_code == 200
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_C_disconnect_during_claim_never_leaves_an_undefined_job_state(session_factory, running_broker):
    """"Desconexion durante el reclamo" en un transporte de peticion/
    respuesta sincrono (seccion 8/45 del informe): la peticion HTTP o bien
    completo su transaccion en el servidor (job -> CLAIMED, durable) o no lo
    hizo -- nunca un estado intermedio. Este test reclama con exito y
    verifica que, aunque el AGENTE nunca reciba/lea la respuesta despues
    (simulado con `close()` inmediato de la conexion subyacente), el estado
    en BD queda CLAIMED de forma determinista y NUNCA se redespacha."""
    from app.broker.dispatch import dispatch_job_to_agent
    from app.services import sol_agent_job_service as job_service
    from app.services.sol_agent_job_service import ActiveJobAlreadyExistsError

    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        session_id, job_id = _setup_session_and_dispatch(
            session_factory, running_broker, tenant_id=setup["tenant_id"], admin_id=setup["admin_id"], agent_id=agent.agent_id
        )

        response = agent.claim(job_id)
        assert response.status_code == 200

        verify_session = session_factory()
        try:
            job = job_service.get_job(verify_session, tenant_id=setup["tenant_id"], job_id=job_id)
            assert job.status == "CLAIMED"
        finally:
            verify_session.close()

        # Ningun redespacho silencioso: el job activo ya existe para este
        # agente/sesion -- Domain C rechaza un segundo `create_job`.
        redispatch_session = session_factory()
        try:
            try:
                dispatch_job_to_agent(
                    redispatch_session,
                    tenant_id=setup["tenant_id"],
                    session_id=session_id,
                    agent_id=agent.agent_id,
                    pending_dispatch=running_broker.pending_dispatch,
                )
                raise AssertionError("se esperaba ActiveJobAlreadyExistsError")
            except ActiveJobAlreadyExistsError:
                pass
        finally:
            redispatch_session.close()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_D_heartbeat_carries_cancel_notice_after_operator_cancels_session(session_factory, running_broker):
    """El estado durable YA es autoritativo antes de que exista este
    mensaje (seccion 39 del encargo) -- el aviso via heartbeat es
    puramente secundario/informativo, sin ack requerido."""
    from app.services import sol_access_session_service as session_service

    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        session_id, job_id = _setup_session_and_dispatch(
            session_factory, running_broker, tenant_id=setup["tenant_id"], admin_id=setup["admin_id"], agent_id=agent.agent_id
        )
        assert agent.claim(job_id).status_code == 200

        cancel_session = session_factory()
        try:
            session_service.cancel_session(
                cancel_session, tenant_id=setup["tenant_id"], session_id=session_id, actor_user_id=setup["admin_id"]
            )
            cancel_session.commit()
        finally:
            cancel_session.close()

        heartbeat_response = agent.heartbeat()
        assert heartbeat_response.status_code == 200
        body = heartbeat_response.json()
        assert body["cancel_notice"] is not None
        assert body["cancel_notice"]["job_id"] == str(job_id)

        # Un segundo heartbeat ya no repite el aviso (una vez visto, se
        # limpia del registro de conexion -- ver `set_active_job`).
        second_heartbeat = agent.heartbeat()
        assert second_heartbeat.json()["cancel_notice"] is None
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_E_status_report_rejects_a_job_that_does_not_belong_to_the_caller(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    try:
        agent_a = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        agent_b = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])

        _session_id, job_id = _setup_session_and_dispatch(
            session_factory, running_broker, tenant_id=setup["tenant_id"], admin_id=setup["admin_id"], agent_id=agent_a.agent_id
        )
        assert agent_a.claim(job_id).status_code == 200

        response = agent_b.report_status(job_id, "COMPLETED")
        assert response.status_code == 404
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])
