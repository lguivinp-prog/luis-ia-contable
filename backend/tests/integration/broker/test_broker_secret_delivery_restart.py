"""Fase 5, Bloque 5.2, Domain E1-B -- pruebas Q y R del plan
(`BLOCK5_2_E1B_TEST_PLAN.md`): un reinicio/crash del broker pierde POR
COMPLETO el registro de leases/grants process-local (`ConnectionRegistry`,
`SecretDeliveryGrantRegistry`) -- BD sigue siendo la unica autoridad
durable. Mismo patron de dos instancias `BrokerServer` sucesivas ya
productivo en `test_broker_restart_recovery.py::test_A` (Domain D)."""

from __future__ import annotations

import uuid

from app.broker.dispatch import dispatch_job_to_agent
from app.broker.server import BrokerServer
from app.core.sol_vault_registry import vault_unlock_registry
from app.services import sol_agent_credential_delivery_service as delivery_service
from tests.integration.broker._secret_delivery_helpers import get_user_row
from tests.integration.broker.agent_test_client import AgentTestClient
from tests.integration.broker.conftest import cleanup_tenant, create_pairing_over_service


def _pair_agent(session_factory, broker, tenant_id, admin_id) -> AgentTestClient:
    from cryptography.hazmat.primitives import serialization

    pairing_id, pairing_code = create_pairing_over_service(
        session_factory, tenant_id=tenant_id, requested_by_user_id=admin_id
    )
    agent = AgentTestClient(
        ca_certificate_pem=broker.ca.certificate.public_bytes(serialization.Encoding.PEM),
        pairing_base_url=f"https://127.0.0.1:{broker.pairing_bound_port}",
        operational_base_url=f"https://127.0.0.1:{broker.operational_bound_port}",
    )
    assert agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code).status_code == 200
    assert agent.hello().status_code == 200
    return agent


def _dispatch_and_claim(session_factory, broker, setup, agent) -> uuid.UUID:
    dispatch_session = session_factory()
    try:
        job_id = dispatch_job_to_agent(
            dispatch_session,
            tenant_id=setup["tenant_id"],
            session_id=setup["session_id"],
            agent_id=agent.agent_id,
            pending_dispatch=broker.pending_dispatch,
        )
        dispatch_session.commit()
    finally:
        dispatch_session.close()
    assert agent.claim(job_id).status_code == 200
    return job_id


def _authorize(session_factory, broker, setup, job_id) -> None:
    actor = get_user_row(session_factory, setup["admin_id"])
    delivery_service.authorize_credential_delivery(
        session_factory,
        grant_registry=broker.secret_delivery_grants,
        vault_registry=vault_unlock_registry,
        tenant_id=setup["tenant_id"],
        session_id=setup["session_id"],
        job_id=job_id,
        actor=actor,
        app_session_id=setup["app_session_id"],
    )


def test_Q_restart_before_gate_consumption_requires_explicit_reauthorization(
    session_factory, broker_ca, pairing_key, e1b_setup
):
    """Q: el grant se autorizo, pero el broker se reinicia ANTES de que la
    entrega lo consuma. El grant process-local se pierde por completo --
    NUNCA se reconstruye a partir de la fila de auditoria
    `CREDENTIAL_DELIVERY_AUTHORIZED` (`GRANT_RECONSTRUCTED_FROM_AUDIT=NO`),
    aunque esa fila SI sobrevive en BD. Solo una autorizacion explicita
    NUEVA (contra el broker nuevo) puede habilitar la entrega."""
    setup = e1b_setup
    try:
        first_broker = BrokerServer(
            host="127.0.0.1", pairing_port=0, operational_port=0, ca=broker_ca, session_factory=session_factory
        )
        first_broker.start()
        try:
            agent = _pair_agent(session_factory, first_broker, setup["tenant_id"], setup["admin_id"])
            job_id = _dispatch_and_claim(session_factory, first_broker, setup, agent)
            _authorize(session_factory, first_broker, setup, job_id)
            assert first_broker.secret_delivery_grants.peek(tenant_id=setup["tenant_id"], job_id=job_id) is not None

            # la fila de auditoria SI quedo comprometida en BD (Domain
            # E1-A.1) -- se verifica para dejar constancia de que su
            # supervivencia NUNCA por si sola reconstruye el grant.
            from sqlalchemy import select

            from app.models.audit_log import AuditAction, AuditLog

            verify_session = session_factory()
            try:
                audit_row = verify_session.execute(
                    select(AuditLog).where(
                        AuditLog.tenant_id == setup["tenant_id"],
                        AuditLog.action == AuditAction.CREDENTIAL_DELIVERY_AUTHORIZED,
                        AuditLog.entity_id == job_id,
                    )
                ).scalar_one_or_none()
                assert audit_row is not None
            finally:
                verify_session.close()
        finally:
            # simula crash/reinicio: el registro de conexiones y el grant
            # process-local se pierden por completo.
            first_broker.stop()

        second_broker = BrokerServer(
            host="127.0.0.1", pairing_port=0, operational_port=0, ca=broker_ca, session_factory=session_factory
        )
        second_broker.start()
        try:
            agent.operational_base_url = f"https://127.0.0.1:{second_broker.operational_bound_port}"
            assert agent.hello().status_code == 200  # certificado sobrevive, reconecta

            # sin grant en el broker nuevo -- rechazo generico, NUNCA una
            # reconstruccion espuria a partir de la auditoria
            response = agent.request_credential(job_id)
            assert response.status_code == 409
            assert response.json()["error"] == "CREDENTIAL_UNAVAILABLE"

            from app.services import sol_agent_job_service as job_service

            verify_session = session_factory()
            try:
                job = job_service.get_job(verify_session, tenant_id=setup["tenant_id"], job_id=job_id)
                assert job.credential_delivered_at is None
            finally:
                verify_session.close()

            # una NUEVA autorizacion explicita, contra el broker nuevo,
            # SI habilita la entrega con normalidad -- la recuperacion es
            # deliberada, nunca automatica.
            _authorize(session_factory, second_broker, setup, job_id)
            recovered = agent.request_credential(job_id)
            assert recovered.status_code == 200
            body = recovered.json()
            assert body["ruc"] == setup["ruc"]
        finally:
            second_broker.stop()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_R_restart_after_gate_consumption_no_redelivery_ever(session_factory, broker_ca, pairing_key, e1b_setup):
    """R: la entrega ya tuvo exito (gate consumido, `credential_delivered_at`
    ya comprometido en BD) ANTES del reinicio. Tras el reinicio, ni una
    peticion de entrega ni un intento de re-autorizacion pueden volver a
    producir el secreto -- la columna durable de Domain C es la unica
    autoridad y bloquea cualquier redelivery para siempre, sin importar
    cuantas veces se reinicie el broker."""
    setup = e1b_setup
    try:
        first_broker = BrokerServer(
            host="127.0.0.1", pairing_port=0, operational_port=0, ca=broker_ca, session_factory=session_factory
        )
        first_broker.start()
        try:
            agent = _pair_agent(session_factory, first_broker, setup["tenant_id"], setup["admin_id"])
            job_id = _dispatch_and_claim(session_factory, first_broker, setup, agent)
            _authorize(session_factory, first_broker, setup, job_id)

            delivered = agent.request_credential(job_id)
            assert delivered.status_code == 200
        finally:
            first_broker.stop()

        second_broker = BrokerServer(
            host="127.0.0.1", pairing_port=0, operational_port=0, ca=broker_ca, session_factory=session_factory
        )
        second_broker.start()
        try:
            agent.operational_base_url = f"https://127.0.0.1:{second_broker.operational_bound_port}"
            assert agent.hello().status_code == 200

            # ninguna peticion de entrega directa puede volver a recibir
            # el secreto -- sin grant en el broker nuevo
            response = agent.request_credential(job_id)
            assert response.status_code == 409
            assert response.json()["error"] == "CREDENTIAL_UNAVAILABLE"

            # ni siquiera una NUEVA autorizacion explicita puede reabrir la
            # ventana -- la columna durable `credential_delivered_at` ya
            # esta comprometida y `authorize_credential_delivery` la
            # rechaza de forma generica, exactamente igual que cualquier
            # otro job ya no elegible.
            actor = get_user_row(session_factory, setup["admin_id"])
            try:
                delivery_service.authorize_credential_delivery(
                    session_factory,
                    grant_registry=second_broker.secret_delivery_grants,
                    vault_registry=vault_unlock_registry,
                    tenant_id=setup["tenant_id"],
                    session_id=setup["session_id"],
                    job_id=job_id,
                    actor=actor,
                    app_session_id=setup["app_session_id"],
                )
                raise AssertionError("se esperaba CredentialAuthorizationNotEligibleError")
            except delivery_service.CredentialAuthorizationNotEligibleError:
                pass

            assert second_broker.secret_delivery_grants.peek(tenant_id=setup["tenant_id"], job_id=job_id) is None
        finally:
            second_broker.stop()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])
