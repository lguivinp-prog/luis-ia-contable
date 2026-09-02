"""Fase 5, Bloque 5.2, Domain E1-B -- pruebas K/X/Y del plan
(`BLOCK5_2_E1B_TEST_PLAN.md`): entrega concurrente REAL sobre PostgreSQL
real (dos conexiones/sesiones independientes, una por peticion HTTP) y mTLS
real -- exactamente una respuesta con texto plano, exactamente un gate de
BD consumido, y el punto de descifrado invocado exactamente una vez
(medido con un espia `wraps=`, nunca inferido solo de la unica respuesta
200 exitosa)."""

from __future__ import annotations

import threading
import uuid
from unittest.mock import patch

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


def test_K_X_Y_concurrent_delivery_exactly_one_winner_one_decrypt(session_factory, running_broker, e1b_setup):
    """K: dos peticiones HTTP simultaneas del MISMO agente/conexion logica,
    para el mismo job/grant, sincronizadas con `threading.Barrier` para
    maximizar la superposicion real, sobre PostgreSQL real (`session_factory`
    real, cada peticion abre su propia conexion vía `BrokerServer.
    session_factory`) y mTLS real (dos sockets fisicos, mismo certificado de
    cliente). X: exactamente una adquisicion `IN_FLIGHT` gana -- medido por
    exactamente una respuesta 200 con texto plano y la otra un rechazo
    generico 409. Y: el punto de descifrado unico y nombrado
    (`_decrypt_credential`) se invoca EXACTAMENTE una vez en total -- espiado
    con `mock.patch(..., wraps=...)`, nunca inferido solo de la unica
    respuesta 200 (mismo patron que el espia de `_perform_ca_signature` en
    Domain D.1)."""
    setup = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        job_id = _dispatch_and_claim(session_factory, running_broker, setup, agent)
        _authorize(session_factory, running_broker, setup, job_id)

        real_decrypt = delivery_service._decrypt_credential
        results: list = [None, None]
        barrier = threading.Barrier(2)

        def _call(index: int) -> None:
            barrier.wait(timeout=5.0)
            results[index] = agent.request_credential(job_id)

        with patch.object(delivery_service, "_decrypt_credential", wraps=real_decrypt) as spy:
            t1 = threading.Thread(target=_call, args=(0,))
            t2 = threading.Thread(target=_call, args=(1,))
            t1.start()
            t2.start()
            t1.join(timeout=15.0)
            t2.join(timeout=15.0)

            # Y: medido DENTRO del `with patch` -- nunca inferido despues de
            # que el espia ya fue desmontado.
            decrypt_call_count = spy.call_count

        assert results[0] is not None and results[1] is not None
        assert not t1.is_alive() and not t2.is_alive()

        status_codes = sorted(r.status_code for r in results)
        assert status_codes == [200, 409], status_codes

        winner = results[0] if results[0].status_code == 200 else results[1]
        loser = results[1] if winner is results[0] else results[0]

        body = winner.json()
        assert body["ruc"] == setup["ruc"]
        assert body["sol_username"] == TEST_SOL_USER
        assert body["sol_password"] == TEST_SOL_PASSWORD_DO_NOT_USE
        assert winner.headers.get("Cache-Control") == "no-store"

        assert loser.json()["error"] == "CREDENTIAL_UNAVAILABLE"

        assert decrypt_call_count == 1

        # X: el gate de BD (Domain C) tambien se consumio exactamente una
        # vez -- `mark_credential_delivered` es su propio guardia
        # optimista, esto solo confirma el resultado observable.
        from app.services import sol_agent_job_service as job_service

        verify_session = session_factory()
        try:
            job = job_service.get_job(verify_session, tenant_id=setup["tenant_id"], job_id=job_id)
            assert job.credential_delivered_at is not None
        finally:
            verify_session.close()

        # el grant ya no existe (consumido de forma irrevocable, nunca
        # disponible para un tercer intento ni vuelve a AUTHORIZED)
        assert running_broker.secret_delivery_grants.peek(tenant_id=setup["tenant_id"], job_id=job_id) is None

        # un tercer intento, ya resuelta la carrera, recibe el mismo
        # rechazo generico -- nunca una redelivery automatica
        third = agent.request_credential(job_id)
        assert third.status_code == 409
        assert third.json()["error"] == "CREDENTIAL_UNAVAILABLE"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])
