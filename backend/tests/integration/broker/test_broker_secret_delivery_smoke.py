"""Prueba humo end-to-end de Domain E1-B: boveda real -> credencial SOL
cifrada real -> sesion de acceso -> agente emparejado real (mTLS) -> job
despachado y reclamado -> autorizacion -> entrega de credencial sobre el
canal operacional real. Sirve como cableado base antes de la matriz
completa A-AD."""

from __future__ import annotations

from app.broker.dispatch import dispatch_job_to_agent
from app.core.sol_vault_registry import vault_unlock_registry
from app.services import sol_agent_credential_delivery_service as delivery_service
from tests.integration.broker._secret_delivery_helpers import (
    TEST_SOL_PASSWORD_DO_NOT_USE,
    TEST_SOL_USER,
    get_user_row,
)
from tests.integration.broker.agent_test_client import AgentTestClient
from tests.integration.broker.conftest import cleanup_tenant, create_pairing_over_service


def _pair_agent(session_factory, running_broker, tenant_id, admin_id) -> AgentTestClient:
    from cryptography.hazmat.primitives import serialization

    pairing_id, pairing_code = create_pairing_over_service(
        session_factory, tenant_id=tenant_id, requested_by_user_id=admin_id
    )
    agent = AgentTestClient(
        ca_certificate_pem=running_broker.ca.certificate.public_bytes(serialization.Encoding.PEM),
        pairing_base_url=f"https://127.0.0.1:{running_broker.pairing_bound_port}",
        operational_base_url=f"https://127.0.0.1:{running_broker.operational_bound_port}",
    )
    assert agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code).status_code == 200
    assert agent.hello().status_code == 200
    return agent


def test_full_authorize_and_deliver_credential_lifecycle(session_factory, running_broker, monkeypatch, e1b_setup):
    setup = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])

        dispatch_session = session_factory()
        try:
            job_id = dispatch_job_to_agent(
                dispatch_session,
                tenant_id=setup["tenant_id"],
                session_id=setup["session_id"],
                agent_id=agent.agent_id,
                pending_dispatch=running_broker.pending_dispatch,
            )
            dispatch_session.commit()
        finally:
            dispatch_session.close()

        assert agent.claim(job_id).status_code == 200

        actor = get_user_row(session_factory, setup["admin_id"])
        delivery_service.authorize_credential_delivery(
            session_factory,
            grant_registry=running_broker.secret_delivery_grants,
            vault_registry=vault_unlock_registry,
            tenant_id=setup["tenant_id"],
            session_id=setup["session_id"],
            job_id=job_id,
            actor=actor,
            app_session_id=setup["app_session_id"],
        )

        response = agent.request_credential(job_id)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ruc"] == setup["ruc"]
        assert body["sol_username"] == TEST_SOL_USER
        assert body["sol_password"] == TEST_SOL_PASSWORD_DO_NOT_USE
        # Domain E1-B.1, Gate C (cierre de evidencia): el contrato de
        # cabeceras congelado (seccion 19/70 del encargo E1-B) nunca tuvo
        # una prueba que verificara las TRES cabeceras exigidas -- solo
        # `Cache-Control` estaba cubierta.
        assert response.headers.get("Cache-Control") == "no-store"
        assert response.headers.get("Pragma") == "no-cache"
        assert response.headers.get("X-Content-Type-Options") == "nosniff"

        # gate de Domain C efectivamente consumido
        verify_session = session_factory()
        try:
            from app.services import sol_agent_job_service as job_service

            job = job_service.get_job(verify_session, tenant_id=setup["tenant_id"], job_id=job_id)
            assert job.credential_delivered_at is not None
        finally:
            verify_session.close()

        # replay -- el grant ya se consumio, nunca una segunda entrega
        replay = agent.request_credential(job_id)
        assert replay.status_code == 409
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])
