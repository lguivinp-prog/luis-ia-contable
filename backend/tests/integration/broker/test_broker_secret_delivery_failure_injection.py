"""Fase 5, Bloque 5.2, Domain E1-B -- pruebas L y M del plan
(`BLOCK5_2_E1B_TEST_PLAN.md`): inyeccion deterministica de fallos en los dos
bordes criticos del orden exacto de entrega -- (L) fallo de BD DESPUES de
descifrar pero ANTES del commit del gate, y (M) fallo de escritura de la
respuesta HTTP DESPUES de que el commit del gate ya tuvo exito. Mismo patron
de inyeccion ya productivo en `test_broker_certificate_issuance_order.py::
test_G` (Domain D.1): envolver el punto exacto una sola vez, nunca un mock
amplio que oculte el resto del comportamiento real."""

from __future__ import annotations

import uuid

from app.broker.dispatch import dispatch_job_to_agent
from app.broker.server import _BrokerHttpHandler
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


def test_L_db_commit_failure_after_decrypt_never_delivers_never_persists_gate(
    session_factory, running_broker, e1b_setup, monkeypatch
):
    """L: el commit del gate (Domain C, `mark_credential_delivered`) falla
    DESPUES de que el descifrado ya tuvo exito -- mismo patron de
    `session.commit` sobreescrito una sola vez que `test_G` de Domain D.1.
    Se inyecta SOLO en `running_broker.session_factory` (atributo plano de
    instancia), y SOLO para la peticion de entrega -- pairing/dispatch/
    claim/authorize ya ocurrieron con exito antes del monkeypatch."""
    setup = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        job_id = _dispatch_and_claim(session_factory, running_broker, setup, agent)
        _authorize(session_factory, running_broker, setup, job_id)

        real_session_factory = running_broker.session_factory

        def _failing_commit_factory():
            db = real_session_factory()
            original_commit = db.commit
            state = {"used": False}

            def _commit_once_then_fail():
                if not state["used"]:
                    state["used"] = True
                    raise RuntimeError("SIMULATED_DB_FAILURE_AFTER_DECRYPT_BEFORE_GATE_COMMIT")
                return original_commit()

            db.commit = _commit_once_then_fail
            return db

        monkeypatch.setattr(running_broker, "session_factory", _failing_commit_factory)

        response = agent.request_credential(job_id)
        assert response.status_code == 500
        assert response.json()["error"] == "INTERNAL_ERROR"
        assert "sol_username" not in response.text
        assert "sol_password" not in response.text

        monkeypatch.setattr(running_broker, "session_factory", real_session_factory)

        from app.services import sol_agent_job_service as job_service

        verify_session = session_factory()
        try:
            job = job_service.get_job(verify_session, tenant_id=setup["tenant_id"], job_id=job_id)
            assert job.credential_delivered_at is None
        finally:
            verify_session.close()

        # el grant NUNCA vuelve a AUTHORIZED -- se invalido, no queda
        # disponible para un reintento silencioso
        assert running_broker.secret_delivery_grants.peek(tenant_id=setup["tenant_id"], job_id=job_id) is None

        # un reintento tras el fallo transitorio recibe el mismo rechazo
        # generico -- ninguna redelivery automatica, se requiere una nueva
        # autorizacion explicita
        retry = agent.request_credential(job_id)
        assert retry.status_code == 409
        assert retry.json()["error"] == "CREDENTIAL_UNAVAILABLE"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_M_response_write_failure_after_gate_commit_gate_stays_consumed_no_redelivery(
    session_factory, running_broker, e1b_setup, monkeypatch
):
    """M: el commit del gate tiene EXITO (queda irrevocablemente consumido),
    pero la escritura final de la respuesta HTTP falla -- se inyecta en
    `_BrokerHttpHandler.end_headers` (el ultimo punto de la clase manejadora
    antes de escribir el cuerpo con el secreto), disparando una sola vez y
    solo para la ruta de credencial. El agente NUNCA recibe el secreto en
    este intento, pero el gate ya consumido significa que NINGUN reintento
    puede volver a recibirlo (seccion 46 del encargo)."""
    setup = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        job_id = _dispatch_and_claim(session_factory, running_broker, setup, agent)
        _authorize(session_factory, running_broker, setup, job_id)

        original_end_headers = _BrokerHttpHandler.end_headers
        state = {"used": False}

        def _end_headers_fail_once_for_credential(self):
            if not state["used"] and "/credential" in self.path:
                state["used"] = True
                # `flush_headers()` normalmente vacia `_headers_buffer`
                # DESPUES de escribir con exito -- como este raise
                # simula que la escritura fisica jamas llego al socket,
                # se vacia aqui explicitamente para que la respuesta de
                # error subsiguiente (`_send_error_code`) nunca concatene
                # los encabezados abortados de la respuesta 200 con los
                # de la respuesta 500 (evita un HTTP invalido en el
                # cliente -- no es el objeto de esta prueba).
                self._headers_buffer = []
                raise ConnectionResetError("SIMULATED_WRITE_FAILURE_AFTER_GATE_COMMIT")
            return original_end_headers(self)

        monkeypatch.setattr(_BrokerHttpHandler, "end_headers", _end_headers_fail_once_for_credential)

        response = agent.request_credential(job_id)
        assert response.status_code == 500
        assert response.json()["error"] == "INTERNAL_ERROR"
        assert "sol_username" not in response.text
        assert "sol_password" not in response.text

        from app.services import sol_agent_job_service as job_service

        verify_session = session_factory()
        try:
            job = job_service.get_job(verify_session, tenant_id=setup["tenant_id"], job_id=job_id)
            # el gate SI quedo consumido -- el commit ya habia tenido exito
            # antes de que fallara la escritura de la respuesta
            assert job.credential_delivered_at is not None
        finally:
            verify_session.close()

        assert running_broker.secret_delivery_grants.peek(tenant_id=setup["tenant_id"], job_id=job_id) is None

        # ningun reintento puede recibir el secreto -- el gate ya esta
        # consumido de forma irrevocable, sin redelivery automatica
        retry = agent.request_credential(job_id)
        assert retry.status_code == 409
        assert retry.json()["error"] == "CREDENTIAL_UNAVAILABLE"
        assert "sol_username" not in retry.text
        assert "sol_password" not in retry.text
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])
