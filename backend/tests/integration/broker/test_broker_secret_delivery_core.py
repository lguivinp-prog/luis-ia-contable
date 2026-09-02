"""Fase 5, Bloque 5.2, Domain E1-B -- matriz de pruebas A-I, V, W del plan
(`BLOCK5_2_E1B_TEST_PLAN.md`): fallos ANTES del gate de entrega -- ninguno
debe descifrar ni consumir `credential_delivered_at`."""

from __future__ import annotations

import time
import uuid

from app.broker.dispatch import dispatch_job_to_agent
from app.core.sol_vault_registry import vault_unlock_registry
from app.services import sol_agent_credential_delivery_service as delivery_service
from tests.integration.broker._secret_delivery_helpers import get_user_row
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


def _dispatch_and_claim(session_factory, running_broker, setup, agent) -> uuid.UUID:
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
    return job_id


def _authorize(session_factory, running_broker, setup, job_id) -> None:
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


def _assert_never_delivered(session_factory, tenant_id, job_id) -> None:
    from app.services import sol_agent_job_service as job_service

    session = session_factory()
    try:
        job = job_service.get_job(session, tenant_id=tenant_id, job_id=job_id)
        assert job.credential_delivered_at is None
    finally:
        session.close()


def test_A_vault_locked_at_authorization_rejects_before_grant_created(session_factory, running_broker, e1b_setup):
    setup = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        job_id = _dispatch_and_claim(session_factory, running_broker, setup, agent)

        vault_unlock_registry.lock(
            tenant_id=setup["tenant_id"],
            vault_id=setup["vault_id"],
            user_id=setup["admin_id"],
            app_session_id=setup["app_session_id"],
        )

        actor = get_user_row(session_factory, setup["admin_id"])
        try:
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
            raise AssertionError("se esperaba CredentialAuthorizationVaultLockedError")
        except delivery_service.CredentialAuthorizationVaultLockedError:
            pass

        assert running_broker.secret_delivery_grants.peek(tenant_id=setup["tenant_id"], job_id=job_id) is None
        response = agent.request_credential(job_id)
        assert response.status_code == 409
        _assert_never_delivered(session_factory, setup["tenant_id"], job_id)
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_B_grant_absent_never_authorized(session_factory, running_broker, e1b_setup):
    setup = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        job_id = _dispatch_and_claim(session_factory, running_broker, setup, agent)

        response = agent.request_credential(job_id)
        assert response.status_code == 409
        assert response.json()["error"] == "CREDENTIAL_UNAVAILABLE"
        _assert_never_delivered(session_factory, setup["tenant_id"], job_id)
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_C_grant_expired_by_ttl(session_factory, running_broker, e1b_setup):
    setup = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        job_id = _dispatch_and_claim(session_factory, running_broker, setup, agent)
        _authorize(session_factory, running_broker, setup, job_id)

        # Fuerza el vencimiento del grant recien creado sin esperar 60s
        # reales -- manipula directamente el reloj monotonico interno via
        # una nueva autorizacion con TTL casi nulo (evita depender de
        # `time.sleep(60)`).
        running_broker.secret_delivery_grants.authorize(
            tenant_id=setup["tenant_id"],
            session_id=setup["session_id"],
            job_id=job_id,
            agent_id=agent.agent_id,
            user_id=setup["admin_id"],
            app_session_id=setup["app_session_id"],
            credential_id=setup["credential_id"],
            ttl_seconds=0.05,
        )
        time.sleep(0.15)

        response = agent.request_credential(job_id)
        assert response.status_code == 409
        _assert_never_delivered(session_factory, setup["tenant_id"], job_id)
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_D_wrong_agent_cannot_receive_credential(session_factory, running_broker, e1b_setup):
    setup = e1b_setup
    try:
        agent_a = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        agent_b = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        job_id = _dispatch_and_claim(session_factory, running_broker, setup, agent_a)
        _authorize(session_factory, running_broker, setup, job_id)

        response = agent_b.request_credential(job_id)
        assert response.status_code == 404
        assert response.json()["error"] == "JOB_UNAVAILABLE"
        _assert_never_delivered(session_factory, setup["tenant_id"], job_id)

        # el agente correcto SI puede
        good = agent_a.request_credential(job_id)
        assert good.status_code == 200
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_E_stale_connection_cannot_receive_credential(session_factory, running_broker, e1b_setup):
    setup = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        job_id = _dispatch_and_claim(session_factory, running_broker, setup, agent)
        _authorize(session_factory, running_broker, setup, job_id)

        stale_connection_id = agent.connection_id
        assert agent.hello().status_code == 200  # reemplaza la conexion

        from tests.integration.broker.agent_test_client import CONNECTION_ID_HEADER

        response = agent.request_credential(job_id, headers={CONNECTION_ID_HEADER: stale_connection_id})
        assert response.status_code == 409
        assert response.json()["error"] == "CONNECTION_REPLACED"
        _assert_never_delivered(session_factory, setup["tenant_id"], job_id)
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_F_revoked_agent_cannot_receive_credential(session_factory, running_broker, e1b_setup):
    from app.services import sol_agent_service

    setup = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        job_id = _dispatch_and_claim(session_factory, running_broker, setup, agent)
        _authorize(session_factory, running_broker, setup, job_id)

        revoke_session = session_factory()
        try:
            sol_agent_service.revoke_agent(
                revoke_session, tenant_id=setup["tenant_id"], agent_id=agent.agent_id, actor_user_id=setup["admin_id"]
            )
            revoke_session.commit()
        finally:
            revoke_session.close()

        response = agent.request_credential(job_id)
        assert response.status_code == 401
        assert response.json()["error"] == "AGENT_REVOKED"
        _assert_never_delivered(session_factory, setup["tenant_id"], job_id)
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_G_cancelled_session_rejects_delivery(session_factory, running_broker, e1b_setup):
    from app.services import sol_access_session_service as session_service

    setup = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        job_id = _dispatch_and_claim(session_factory, running_broker, setup, agent)
        _authorize(session_factory, running_broker, setup, job_id)

        cancel_session = session_factory()
        try:
            session_service.cancel_session(
                cancel_session, tenant_id=setup["tenant_id"], session_id=setup["session_id"], actor_user_id=setup["admin_id"]
            )
            cancel_session.commit()
        finally:
            cancel_session.close()

        response = agent.request_credential(job_id)
        assert response.status_code in (404, 409)
        _assert_never_delivered(session_factory, setup["tenant_id"], job_id)
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_H_expired_session_rejects_delivery(session_factory, running_broker, e1b_setup):
    from app.models.sol_agent_access import SolAccessSession

    setup = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        job_id = _dispatch_and_claim(session_factory, running_broker, setup, agent)
        _authorize(session_factory, running_broker, setup, job_id)

        from datetime import UTC, datetime, timedelta

        expire_session = session_factory()
        try:
            row = expire_session.get(SolAccessSession, setup["session_id"])
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            expire_session.commit()
        finally:
            expire_session.close()

        response = agent.request_credential(job_id)
        assert response.status_code in (404, 409)
        _assert_never_delivered(session_factory, setup["tenant_id"], job_id)
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_I_job_not_claimed_states_reject_delivery(session_factory, running_broker, e1b_setup):
    """Parametrizado manualmente sobre varios estados no-`CLAIMED` -- un
    job recien despachado (`PENDING`, nunca reclamado) es el caso mas
    simple y ya cubre la rama principal; los estados terminales adicionales
    ya estan cubiertos por G/H (via el job cancelado/expirado en cascada) y
    por Domain C (mark_credential_delivered ya rechaza todos los no-
    `CLAIMED`, sin reabrir esa logica aqui)."""
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
        # Deliberadamente NUNCA reclamado -- sigue PENDING.

        response = agent.request_credential(job_id)
        assert response.status_code in (404, 409)
        _assert_never_delivered(session_factory, setup["tenant_id"], job_id)
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_V_mtls_and_connection_id_requirements_apply_to_credential_endpoint(session_factory, running_broker, e1b_setup):
    """No hay atajos: sin `connection_id`, el endpoint de credencial se
    comporta igual que el resto del canal operacional (Domain D.1)."""
    setup = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        job_id = _dispatch_and_claim(session_factory, running_broker, setup, agent)
        _authorize(session_factory, running_broker, setup, job_id)

        agent.connection_id = None
        response = agent.request_credential(job_id)
        assert response.status_code == 409
        assert response.json()["error"] == "CONNECTION_NOT_ESTABLISHED"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_W_job_token_and_grant_are_never_confused(session_factory, running_broker, e1b_setup):
    """El token de job (Domain C, `pending_dispatch`) y el grant de
    entrega (Domain E1) son almacenes completamente separados -- un job
    reclamado con exito (token de job ya consumido) NUNCA autoriza por si
    solo una entrega de secreto sin un grant explicito."""
    setup = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        job_id = _dispatch_and_claim(session_factory, running_broker, setup, agent)
        # El job ya fue reclamado con exito (token de job consumido) -- SIN
        # llamar a `_authorize`, un grant nunca existio.
        assert running_broker.pending_dispatch.peek_metadata(agent_id=agent.agent_id) is None
        assert running_broker.secret_delivery_grants.peek(tenant_id=setup["tenant_id"], job_id=job_id) is None

        response = agent.request_credential(job_id)
        assert response.status_code == 409
        assert response.json()["error"] == "CREDENTIAL_UNAVAILABLE"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])
