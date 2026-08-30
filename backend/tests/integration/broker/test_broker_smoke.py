"""Prueba humo end-to-end del ciclo de vida completo (Fase 5, Bloque 5.2,
Domain D): pairing real por CSR -> mTLS -> hello -> heartbeat -> descubrir
job -> reclamar -> reportar COMPLETED. Sirve como cableado base antes de la
matriz completa de pruebas especificas."""

from __future__ import annotations

from tests.integration.broker.agent_test_client import AgentTestClient
from tests.integration.broker.conftest import (
    cleanup_tenant,
    commit_credential,
    commit_session,
    commit_tenant_user,
    create_pairing_over_service,
)


def test_full_lifecycle_pairing_mtls_heartbeat_claim_complete(session_factory, running_broker, broker_ca):
    setup = commit_tenant_user(session_factory)
    client_id, credential_id = commit_credential(session_factory, setup["tenant_id"], setup["admin_id"])
    session_id = commit_session(
        session_factory,
        tenant_id=setup["tenant_id"],
        client_id=client_id,
        credential_id=credential_id,
        requester_id=setup["admin_id"],
    )
    pairing_id, pairing_code = create_pairing_over_service(
        session_factory, tenant_id=setup["tenant_id"], requested_by_user_id=setup["admin_id"]
    )

    try:
        from cryptography.hazmat.primitives import serialization

        ca_cert_pem = running_broker.ca.certificate.public_bytes(serialization.Encoding.PEM)
        agent = AgentTestClient(
            ca_certificate_pem=ca_cert_pem,
            pairing_base_url=f"https://127.0.0.1:{running_broker.pairing_bound_port}",
            operational_base_url=f"https://127.0.0.1:{running_broker.operational_bound_port}",
        )

        pairing_response = agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code)
        assert pairing_response.status_code == 200, pairing_response.text
        assert agent.agent_id is not None

        hello_response = agent.hello()
        assert hello_response.status_code == 200, hello_response.text

        heartbeat_response = agent.heartbeat()
        assert heartbeat_response.status_code == 200, heartbeat_response.text
        assert heartbeat_response.json()["cancel_notice"] is None

        empty_next = agent.jobs_next()
        assert empty_next.status_code == 200
        assert empty_next.json()["job_id"] is None

        from app.broker.dispatch import dispatch_job_to_agent

        dispatch_session = session_factory()
        try:
            job_id = dispatch_job_to_agent(
                dispatch_session,
                tenant_id=setup["tenant_id"],
                session_id=session_id,
                agent_id=agent.agent_id,
                pending_dispatch=running_broker.pending_dispatch,
            )
            dispatch_session.commit()
        finally:
            dispatch_session.close()

        available = agent.jobs_next()
        assert available.status_code == 200
        body = available.json()
        assert body["job_id"] == str(job_id)
        assert body["session_id"] == str(session_id)

        claim_response = agent.claim(job_id)
        assert claim_response.status_code == 200, claim_response.text
        assert claim_response.json()["accepted"] is True

        replay_response = agent.claim(job_id)
        assert replay_response.status_code == 404
        assert replay_response.json()["error"] == "JOB_UNAVAILABLE"

        status_response = agent.report_status(job_id, "COMPLETED")
        assert status_response.status_code == 200, status_response.text
        assert status_response.json()["status"] == "COMPLETED"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])
