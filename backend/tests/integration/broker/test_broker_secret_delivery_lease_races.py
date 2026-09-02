"""Fase 5, Bloque 5.2, Domain E1-B -- pruebas N, O y P del plan
(`BLOCK5_2_E1B_TEST_PLAN.md`): carreras REALES contra la ventana critica de
entrega -- (N) un `HELLO` de reemplazo mientras el lease de conexion esta
activo, (O) revocacion del agente durante la ventana de descifrado/commit, y
(P) cancelacion de la sesion durante esa misma ventana. Sincronizacion
deterministica via `threading.Event` alrededor del unico punto de
descifrado nombrado (`_decrypt_credential`) -- nunca `time.sleep` como unico
mecanismo de orden."""

from __future__ import annotations

import threading
import uuid

from app.broker.dispatch import dispatch_job_to_agent
from app.core.sol_vault_registry import vault_unlock_registry
from app.services import sol_agent_credential_delivery_service as delivery_service
from tests.integration.broker._secret_delivery_helpers import get_user_row
from tests.integration.broker.agent_test_client import CONNECTION_ID_HEADER, AgentTestClient
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


def _install_blocking_decrypt(monkeypatch, *, entered: threading.Event, release: threading.Event):
    """Bloquea el UNICO punto de descifrado nombrado justo antes de que se
    ejecute -- mantiene el lease de conexion activo y el grant ya en
    `IN_FLIGHT` (adquirido ANTES de este punto en `deliver_credential`)
    durante toda la ventana que el hilo principal necesita para inyectar la
    mutacion concurrente."""
    real_decrypt = delivery_service._decrypt_credential

    def _blocking_decrypt(db, *, tenant_id, credential, vault_root_key):
        entered.set()
        release.wait(timeout=10.0)
        return real_decrypt(db, tenant_id=tenant_id, credential=credential, vault_root_key=vault_root_key)

    monkeypatch.setattr(delivery_service, "_decrypt_credential", _blocking_decrypt)


def test_N_hello_replacement_rejected_while_secret_lease_active(
    session_factory, running_broker, e1b_setup, monkeypatch
):
    """N: mientras la entrega esta en vuelo (lease activo sobre la conexion
    logica del agente), un `HELLO` de reemplazo del MISMO agente (mismo
    certificado de cliente) debe rechazarse con `CONNECTION_BUSY` -- nunca
    bloquear indefinidamente, nunca reemplazar la conexion a mitad de la
    entrega. Tras liberarse el lease, el `NEW_REPLACES_OLD` normal (Domain
    D.1) vuelve a aplicar sin cambios."""
    setup = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        job_id = _dispatch_and_claim(session_factory, running_broker, setup, agent)
        _authorize(session_factory, running_broker, setup, job_id)

        entered = threading.Event()
        release = threading.Event()
        _install_blocking_decrypt(monkeypatch, entered=entered, release=release)

        result: list = [None]

        def _deliver():
            result[0] = agent.request_credential(job_id)

        delivery_thread = threading.Thread(target=_deliver)
        delivery_thread.start()
        assert entered.wait(timeout=5.0), "el descifrado nunca inicio -- no se pudo establecer la ventana de carrera"

        original_connection_id = agent.connection_id
        assert running_broker.connections.get(agent_id=agent.agent_id).secret_lease_count > 0

        # segunda conexion fisica del MISMO agente (mismo certificado de
        # cliente) intenta reemplazar mientras el lease sigue activo --
        # arnes separado para no pisar el `connection_id` del agente
        # original todavia en vuelo.
        replacement = AgentTestClient(
            ca_certificate_pem=agent._ca_cert_pem,
            pairing_base_url=agent.pairing_base_url,
            operational_base_url=agent.operational_base_url,
        )
        replacement._client_cert_pem = agent._client_cert_pem
        replacement._client_key_pem = agent._client_key_pem
        # reutiliza los MISMOS archivos ya escritos por `agent` (solo
        # lectura desde aqui en adelante -- seguro incluso bajo
        # concurrencia, ver el hallazgo documentado en
        # `agent_test_client.py::_store_client_identity`).
        replacement._client_cert_path = agent._client_cert_path
        replacement._client_key_path = agent._client_key_path
        replacement.agent_id = agent.agent_id

        busy_response = replacement.hello()
        assert busy_response.status_code == 409
        assert busy_response.json()["error"] == "CONNECTION_BUSY"

        release.set()
        delivery_thread.join(timeout=15.0)
        assert not delivery_thread.is_alive()
        assert result[0] is not None
        assert result[0].status_code == 200
        assert result[0].json()["sol_username"] == "TEST_SOL_USER"

        # la conexion original NUNCA fue reemplazada durante la ventana --
        # sigue vigente para el mismo `connection_id` que tenia antes.
        assert agent.connection_id == original_connection_id
        heartbeat = agent.heartbeat()
        assert heartbeat.status_code == 200

        # liberado el lease (entrega ya terminada), un HELLO de reemplazo
        # legitimo SI tiene exito con normalidad -- la ventana era acotada,
        # nunca un bloqueo permanente.
        after_release = replacement.hello()
        assert after_release.status_code == 200

        # y la conexion vieja, ahora reemplazada de verdad, deja de ser
        # valida para peticiones futuras (mismo principio ya congelado en
        # Domain D.1 -- se confirma via heartbeat con el `connection_id`
        # viejo).
        stale_heartbeat = agent.heartbeat(headers={CONNECTION_ID_HEADER: original_connection_id})
        assert stale_heartbeat.status_code == 409
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_O_revocation_during_lease_window_gate_never_committed_no_secret_sent(
    session_factory, running_broker, e1b_setup, monkeypatch
):
    """O: el agente es revocado MIENTRAS la entrega esta en vuelo (despues
    de la autenticacion mTLS inicial y de haber ganado el grant, pero antes
    del commit del gate). `mark_credential_delivered` (Domain C) re-verifica
    el estado del agente como su PROPIA precondicion (`agent.status ==
    "ACTIVE"`, bloqueada con `SELECT ... FOR UPDATE` +
    `populate_existing=True`, ver el hallazgo de esta misma bateria de
    pruebas en `sol_agent_access_repository.py`) -- la revocacion ya
    comprometida se detecta AQUI, ANTES de que el gate llegue a
    comprometerse: nunca hace falta que la revalidacion final (paso 13)
    actue como unico backstop para este caso especifico. El resultado
    observable es el mismo espiritu que el paso 13 (ningun secreto se
    escribe jamas, ninguna redelivery posterior), solo que detectado un
    paso antes."""
    setup = e1b_setup
    try:
        from app.services import sol_agent_service

        agent = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        job_id = _dispatch_and_claim(session_factory, running_broker, setup, agent)
        _authorize(session_factory, running_broker, setup, job_id)

        entered = threading.Event()
        release = threading.Event()
        _install_blocking_decrypt(monkeypatch, entered=entered, release=release)

        result: list = [None]

        def _deliver():
            result[0] = agent.request_credential(job_id)

        delivery_thread = threading.Thread(target=_deliver)
        delivery_thread.start()
        assert entered.wait(timeout=5.0), "el descifrado nunca inicio -- no se pudo establecer la ventana de carrera"

        revoke_session = session_factory()
        try:
            sol_agent_service.revoke_agent(
                revoke_session, tenant_id=setup["tenant_id"], agent_id=agent.agent_id, actor_user_id=setup["admin_id"]
            )
            revoke_session.commit()
        finally:
            revoke_session.close()

        release.set()
        delivery_thread.join(timeout=15.0)
        assert not delivery_thread.is_alive()
        assert result[0] is not None

        # el secreto NUNCA se escribio en la respuesta -- la propia
        # precondicion de `mark_credential_delivered` detecto la
        # revocacion antes de comprometer nada.
        assert result[0].status_code == 409
        assert result[0].json()["error"] == "CREDENTIAL_UNAVAILABLE"
        assert "sol_username" not in result[0].text
        assert "sol_password" not in result[0].text

        # el gate NUNCA se comprometio -- rechazado antes del commit.
        from app.services import sol_agent_job_service as job_service

        verify_session = session_factory()
        try:
            job = job_service.get_job(verify_session, tenant_id=setup["tenant_id"], job_id=job_id)
            assert job.credential_delivered_at is None
        finally:
            verify_session.close()

        assert running_broker.secret_delivery_grants.peek(tenant_id=setup["tenant_id"], job_id=job_id) is None

        # el agente revocado ya ni siquiera puede autenticar para intentar
        # una redelivery.
        retry = agent.request_credential(job_id)
        assert retry.status_code == 401
        assert retry.json()["error"] == "AGENT_REVOKED"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_P_session_cancelled_during_lease_window_delivery_rejected_gate_never_committed(
    session_factory, running_broker, e1b_setup, monkeypatch
):
    """P: la sesion de acceso se cancela MIENTRAS la entrega ya paso su
    propio chequeo temprano de sesion no-terminal (`deliver_credential`) y
    esta en la ventana de descifrado/commit. `mark_credential_delivered`
    (Domain C, `_lock_session_then_job`) vuelve a bloquear y RE-VERIFICAR
    el estado de la sesion como su propia precondicion -- con
    `populate_existing=True` (correccion aplicada en este mismo round tras
    un hallazgo real de esta prueba, ver `sol_agent_access_repository.py`,
    `get_session_for_update`/`get_agent_for_update`/`get_job_for_update`/
    `get_job_by_token_hash_for_update`: sin esto, una `Session` de
    SQLAlchemy que ya habia cargado la fila SIN bloqueo devolvia el objeto
    obsoleto cacheado en su identity map incluso bajo `with_for_update()`,
    dejando pasar por alto una cancelacion ya comprometida por otra
    conexion) esa re-verificacion ve la cancelacion YA comprometida y
    rechaza -- el gate NUNCA se compromete, nunca una mezcla de "sesion
    cancelada" + "secreto entregado". El ordenamiento inverso (cancelacion
    ANTES de que la entrega siquiera inicie) ya esta cubierto de forma
    determinista por `test_G_cancelled_session_rejects_delivery`
    (`test_broker_secret_delivery_core.py`)."""
    setup = e1b_setup
    try:
        from app.services import sol_access_session_service as session_service

        agent = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        job_id = _dispatch_and_claim(session_factory, running_broker, setup, agent)
        _authorize(session_factory, running_broker, setup, job_id)

        entered = threading.Event()
        release = threading.Event()
        _install_blocking_decrypt(monkeypatch, entered=entered, release=release)

        result: list = [None]

        def _deliver():
            result[0] = agent.request_credential(job_id)

        delivery_thread = threading.Thread(target=_deliver)
        delivery_thread.start()
        assert entered.wait(timeout=5.0), "el descifrado nunca inicio -- no se pudo establecer la ventana de carrera"

        cancel_session = session_factory()
        try:
            session_service.cancel_session(
                cancel_session, tenant_id=setup["tenant_id"], session_id=setup["session_id"], actor_user_id=setup["admin_id"]
            )
            cancel_session.commit()
        finally:
            cancel_session.close()

        release.set()
        delivery_thread.join(timeout=15.0)
        assert not delivery_thread.is_alive()
        assert result[0] is not None

        # la cancelacion, ya comprometida antes de que `mark_credential_
        # delivered` re-verificara, gana -- rechazo generico, el secreto
        # NUNCA se escribio en la respuesta.
        assert result[0].status_code == 409
        assert result[0].json()["error"] == "CREDENTIAL_UNAVAILABLE"
        assert "sol_username" not in result[0].text
        assert "sol_password" not in result[0].text

        from app.models.sol_agent_access import SolAccessSession
        from app.services import sol_agent_job_service as job_service

        verify_session = session_factory()
        try:
            job = job_service.get_job(verify_session, tenant_id=setup["tenant_id"], job_id=job_id)
            # el gate NUNCA se comprometio -- nunca una mezcla corrupta de
            # "sesion cancelada" + "credencial entregada".
            assert job.credential_delivered_at is None
            session_row = verify_session.get(SolAccessSession, setup["session_id"])
            assert session_row.status == "CANCELLED"
        finally:
            verify_session.close()

        assert running_broker.secret_delivery_grants.peek(tenant_id=setup["tenant_id"], job_id=job_id) is None

        # ningun reintento posterior puede volver a recibir el secreto --
        # la sesion ya es terminal (y `cancel_session` cascada el job
        # asociado fuera de CLAIMED, por eso el rechazo generico puede
        # variar entre 404/409 segun donde ocurra el chequeo -- mismo
        # principio no-oracle ya aplicado en test_G/test_H).
        retry = agent.request_credential(job_id)
        assert retry.status_code in (404, 409)
        assert "sol_username" not in retry.text
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])
