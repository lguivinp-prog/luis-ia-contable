"""Fase 5, Bloque 5.2, Domain D -- seccion 56 del encargo: un reinicio del
broker pierde el registro de conexiones/despacho pendiente PROCESS-LOCAL
(aceptable -- BD sigue siendo autoritativa), pero NUNCA causa ejecucion
duplicada de un job ni redespacho automatico/silencioso de un secreto. La
recuperacion es deliberadamente CONSERVADORA: un job ya despachado pero aun
no reclamado antes del reinicio queda huerfano (PENDING en BD, sin token
recuperable) hasta una accion explicita de operador -- nunca se reconstruye
ni se filtra el token en claro."""

from __future__ import annotations

from app.broker.server import BrokerServer
from tests.integration.broker.agent_test_client import AgentTestClient
from tests.integration.broker.conftest import (
    cleanup_tenant,
    commit_credential,
    commit_session,
    commit_tenant_user,
    create_pairing_over_service,
)


def test_A_restart_never_causes_duplicate_execution_or_silent_secret_redelivery(session_factory, broker_ca, pairing_key):
    from app.broker.dispatch import dispatch_job_to_agent
    from app.services import sol_agent_job_service as job_service

    setup = commit_tenant_user(session_factory)
    try:
        first_broker = BrokerServer(
            host="127.0.0.1", pairing_port=0, operational_port=0, ca=broker_ca, session_factory=session_factory
        )
        first_broker.start()
        try:
            from cryptography.hazmat.primitives import serialization

            pairing_id, pairing_code = create_pairing_over_service(
                session_factory, tenant_id=setup["tenant_id"], requested_by_user_id=setup["admin_id"]
            )
            agent = AgentTestClient(
                ca_certificate_pem=broker_ca.certificate.public_bytes(serialization.Encoding.PEM),
                pairing_base_url=f"https://127.0.0.1:{first_broker.pairing_bound_port}",
                operational_base_url=f"https://127.0.0.1:{first_broker.operational_bound_port}",
            )
            assert agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code).status_code == 200
            assert agent.hello().status_code == 200

            client_id, credential_id = commit_credential(session_factory, setup["tenant_id"], setup["admin_id"])
            session_id = commit_session(
                session_factory,
                tenant_id=setup["tenant_id"],
                client_id=client_id,
                credential_id=credential_id,
                requester_id=setup["admin_id"],
            )
            dispatch_session = session_factory()
            try:
                job_id = dispatch_job_to_agent(
                    dispatch_session,
                    tenant_id=setup["tenant_id"],
                    session_id=session_id,
                    agent_id=agent.agent_id,
                    pending_dispatch=first_broker.pending_dispatch,
                )
                dispatch_session.commit()
            finally:
                dispatch_session.close()

            available_before_crash = agent.jobs_next()
            assert available_before_crash.json()["job_id"] == str(job_id)
        finally:
            # Simula un crash/reinicio: el registro y el despacho pendiente
            # process-local se pierden por completo.
            first_broker.stop()

        second_broker = BrokerServer(
            host="127.0.0.1",
            pairing_port=0,
            operational_port=0,
            ca=broker_ca,  # misma CA "recargada tras el reinicio"
            session_factory=session_factory,
        )
        second_broker.start()
        try:
            agent.operational_base_url = f"https://127.0.0.1:{second_broker.operational_bound_port}"

            # El agente reconecta y reautentica mTLS con exito -- su
            # certificado y la fila `sol_agents` sobrevivieron el reinicio.
            assert agent.hello().status_code == 200

            # El job sigue PENDING en BD, pero el broker nuevo NUNCA lo
            # redespacha automaticamente -- fail-closed, no "disponible".
            available_after_restart = agent.jobs_next()
            assert available_after_restart.json()["job_id"] is None

            # Un intento de reclamo tampoco reconstruye/filtra el token.
            claim_after_restart = agent.claim(job_id)
            assert claim_after_restart.status_code == 404
            assert claim_after_restart.json()["error"] == "JOB_UNAVAILABLE"

            # El job sigue exactamente PENDING -- nunca CLAIMED por una
            # reconstruccion espuria, nunca duplicado.
            verify_session = session_factory()
            try:
                job = job_service.get_job(verify_session, tenant_id=setup["tenant_id"], job_id=job_id)
                assert job.status == "PENDING"
            finally:
                verify_session.close()
        finally:
            second_broker.stop()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])
