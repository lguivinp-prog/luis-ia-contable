"""Fase 5, Bloque 5.2, Domain E1-B -- pruebas S, T, AA, AB, AC y AD del plan
(`BLOCK5_2_E1B_TEST_PLAN.md`): higiene de secretos (logs/BD) y bordes de
autorizacion. Mismo patron de `caplog` ya productivo en
`test_broker_log_redaction.py` (Domain D) para S/AD."""

from __future__ import annotations

import logging
import uuid

from app.broker.dispatch import dispatch_job_to_agent
from app.core import clock
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


def test_Z_wrong_agent_request_never_mutates_legitimate_grant_integration_level(
    session_factory, running_broker, e1b_setup
):
    """Z: mientras un grant legitimo de OTRO agente esta `AUTHORIZED`, una
    peticion de entrega real (mTLS real, broker real) de un agente
    equivocado debe rechazarse SIN mutar el registro en absoluto --
    inspeccionado directamente antes y despues (mismo principio que la
    prueba unitaria `test_C_wrong_agent_cannot_acquire_and_never_mutates_
    legitimate_grant`, aqui a nivel de integracion real). Complementa
    `test_D_wrong_agent_cannot_receive_credential`
    (`test_broker_secret_delivery_core.py`), que solo prueba el resultado
    HTTP observable (404) y que el agente correcto igual puede entregar
    despues -- Z prueba ademas que el grant en si nunca fue tocado, ni
    siquiera transitoriamente."""
    setup = e1b_setup
    try:
        agent_legit = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        agent_wrong = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        job_id = _dispatch_and_claim(session_factory, running_broker, setup, agent_legit)
        _authorize(session_factory, running_broker, setup, job_id)

        before = running_broker.secret_delivery_grants.peek(tenant_id=setup["tenant_id"], job_id=job_id)
        assert before is not None
        assert before.state == "AUTHORIZED"
        assert before.agent_id == agent_legit.agent_id

        response = agent_wrong.request_credential(job_id)
        assert response.status_code == 404
        assert response.json()["error"] == "JOB_UNAVAILABLE"

        after = running_broker.secret_delivery_grants.peek(tenant_id=setup["tenant_id"], job_id=job_id)
        # el dataclass es inmutable y comparable por valor -- identico en
        # CADA campo, no solo en `state`/`agent_id`, confirma que
        # `try_acquire_in_flight` (o cualquier otro camino) nunca llego
        # siquiera a ejecutarse para esta clave.
        assert after == before

        # el agente legitimo SI puede entregar con normalidad despues.
        legit_response = agent_legit.request_credential(job_id)
        assert legit_response.status_code == 200
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_S_AD_full_cycle_never_leaks_plaintext_credentials_or_ruc_in_logs(
    session_factory, running_broker, e1b_setup, caplog
):
    """S/AD: barrido de logs sobre un ciclo autorizar+entregar completo --
    ni `sol_username`, ni `sol_password`, ni el RUC en claro pueden aparecer
    en NINGUNA linea capturada (DEBUG+), sin importar si la peticion tiene
    exito o es rechazada."""
    caplog.set_level(logging.DEBUG)
    setup = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        job_id = _dispatch_and_claim(session_factory, running_broker, setup, agent)
        _authorize(session_factory, running_broker, setup, job_id)

        delivered = agent.request_credential(job_id)
        assert delivered.status_code == 200

        # replay -- tambien debe pasar por el codigo de rechazo sin fugas
        replay = agent.request_credential(job_id)
        assert replay.status_code == 409

        full_log_text = caplog.text
        assert TEST_SOL_USER not in full_log_text
        assert TEST_SOL_PASSWORD_DO_NOT_USE not in full_log_text
        assert setup["ruc"] not in full_log_text
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_T_db_rows_after_delivery_never_contain_plaintext_outside_encrypted_columns(
    session_factory, running_broker, e1b_setup
):
    """T: tras una entrega exitosa, ninguna fila de `audit_log`,
    `sol_agent_jobs` ni `sol_access_sessions` contiene el username/password
    en claro ni el RUC en claro -- las UNICAS columnas autorizadas a
    contener material derivado de la credencial son las YA cifradas de
    `client_sol_credentials` (fuera de alcance de esta verificacion, que se
    concentra en las tablas que Domain E1-B toca)."""
    setup = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        job_id = _dispatch_and_claim(session_factory, running_broker, setup, agent)
        _authorize(session_factory, running_broker, setup, job_id)

        delivered = agent.request_credential(job_id)
        assert delivered.status_code == 200

        from app.models.audit_log import AuditLog
        from app.models.sol_agent_access import SolAccessSession, SolAgentJob

        verify_session = session_factory()
        try:
            audit_rows = (
                verify_session.query(AuditLog).filter(AuditLog.tenant_id == setup["tenant_id"]).all()
            )
            job_row = verify_session.get(SolAgentJob, job_id)
            session_row = verify_session.get(SolAccessSession, setup["session_id"])

            haystacks = []
            for row in audit_rows:
                haystacks.append(str(row.action))
                haystacks.append(str(row.log_metadata) if row.log_metadata is not None else "")
            haystacks.append(str(job_row.__dict__))
            haystacks.append(str(session_row.__dict__))
            combined = "\n".join(haystacks)

            assert TEST_SOL_USER not in combined
            assert TEST_SOL_PASSWORD_DO_NOT_USE not in combined
            assert setup["ruc"] not in combined
        finally:
            verify_session.close()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_AA_durable_audit_write_failure_leaves_no_active_grant(session_factory, running_broker, e1b_setup):
    """AA: si el commit DURABLE de la auditoria
    (`CREDENTIAL_DELIVERY_AUTHORIZED`) falla, el grant NUNCA queda
    disponible -- `authorize_credential_delivery` es su propio limite de
    transaccion (seccion 20/21 del encargo E1-B) y el commit ocurre ANTES
    de activar el grant; si ese commit falla, la excepcion propaga sin que
    `grant_registry.authorize(...)` llegue a ejecutarse jamas.

    Domain E1-B.1, Gate A: desde que `authorize_credential_delivery` abre
    y posee su propia sesion (`session_factory()` interno), esta prueba
    inyecta el fallo envolviendo el propio `session_factory` -- mismo
    patron ya productivo de `test_L`
    (`test_broker_secret_delivery_failure_injection.py`) y de
    `test_broker_certificate_issuance_order.py::test_G` -- en vez de
    mutar una sesion que el test ya no crea directamente."""
    setup = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        job_id = _dispatch_and_claim(session_factory, running_broker, setup, agent)

        def _failing_commit_factory():
            db = session_factory()

            def _failing_commit():
                raise RuntimeError("SIMULATED_DURABLE_AUDIT_COMMIT_FAILURE")

            db.commit = _failing_commit
            return db

        actor = get_user_row(session_factory, setup["admin_id"])
        raised = False
        try:
            delivery_service.authorize_credential_delivery(
                _failing_commit_factory,
                grant_registry=running_broker.secret_delivery_grants,
                vault_registry=vault_unlock_registry,
                tenant_id=setup["tenant_id"],
                session_id=setup["session_id"],
                job_id=job_id,
                actor=actor,
                app_session_id=setup["app_session_id"],
            )
        except RuntimeError as exc:
            raised = "SIMULATED_DURABLE_AUDIT_COMMIT_FAILURE" in str(exc)
        assert raised

        assert running_broker.secret_delivery_grants.peek(tenant_id=setup["tenant_id"], job_id=job_id) is None

        # ni siquiera una entrega directa (sin pasar por HTTP) puede
        # adquirir un grant que nunca llego a existir.
        assert (
            running_broker.secret_delivery_grants.try_acquire_in_flight(
                tenant_id=setup["tenant_id"], job_id=job_id, agent_id=agent.agent_id
            )
            is None
        )

        response = agent.request_credential(job_id)
        assert response.status_code == 409
        assert response.json()["error"] == "CREDENTIAL_UNAVAILABLE"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_AB_verify_access_operation_cannot_authorize_credential_delivery(
    session_factory, running_broker, e1b_setup
):
    """AB: una autorizacion de desbloqueo restringida a `VERIFY_ACCESS`
    (Bloque 5.1) NUNCA habilita `authorize_credential_delivery` --
    `CREDENTIAL_DELIVERY` es una operacion DISTINTA y debe solicitarse
    explicitamente (Domain E1-A.1). Construye la entrada directamente en
    `vault_unlock_registry.issue(...)` con `allowed_operations={
    "VERIFY_ACCESS"}` -- bypasea deliberadamente los conjuntos normales
    admin/contador de `sol_vault_service.py` (que YA incluyen
    `CREDENTIAL_DELIVERY`) para poder probar el borde exacto."""
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
        vault_unlock_registry.issue(
            vault_root_key=b"0" * 32,
            tenant_id=setup["tenant_id"],
            vault_id=setup["vault_id"],
            user_id=setup["admin_id"],
            app_session_id=setup["app_session_id"],
            allowed_operations=frozenset({"VERIFY_ACCESS"}),
            authorized_client_ids=None,
            ttl_seconds=600,
            now=clock.utcnow(),
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
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_AC_extra_ruc_field_rejected_by_strict_schema_and_response_ruc_always_trusted_source(
    session_factory, running_broker, e1b_setup
):
    """AC: `CredentialDeliveryRequest` (`extra="forbid"`) rechaza cualquier
    intento de inyectar un campo no reconocido -- incluido un `ruc` propuesto
    por el agente -- con `422 INVALID_MESSAGE`, ANTES de que ese cuerpo
    llegue siquiera a la logica de entrega. El RUC devuelto en una entrega
    exitosa proviene EXCLUSIVAMENTE de la cadena de confianza
    `SolAgentJob.session_id -> SolAccessSession.client_id -> Client.ruc`
    (ya verificado en cada prueba de entrega exitosa de este modulo, ej.
    `test_broker_secret_delivery_smoke.py`) -- nunca de un valor que el
    agente pudiera proponer."""
    setup = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        job_id = _dispatch_and_claim(session_factory, running_broker, setup, agent)
        _authorize(session_factory, running_broker, setup, job_id)

        injected = agent.request_credential(job_id, extra_body={"ruc": "99999999999"})
        assert injected.status_code == 422
        assert injected.json()["error"] == "INVALID_MESSAGE"
        assert "99999999999" not in injected.text

        # el grant SIGUE disponible -- el intento invalido nunca lo tocó
        assert running_broker.secret_delivery_grants.peek(tenant_id=setup["tenant_id"], job_id=job_id) is not None

        # una peticion bien formada, inmediatamente despues, SI funciona y
        # el RUC devuelto es el de la cadena de confianza real, nunca el
        # valor que el intento anterior trato de inyectar.
        legit = agent.request_credential(job_id)
        assert legit.status_code == 200
        body = legit.json()
        assert body["ruc"] == setup["ruc"]
        assert body["ruc"] != "99999999999"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])
