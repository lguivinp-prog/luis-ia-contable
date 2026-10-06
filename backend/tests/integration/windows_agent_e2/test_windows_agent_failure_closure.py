"""Fase 5, Bloque 5.2, Domain E2.1-B -- cierre de ciclo de vida/fallos del
Agente Windows (L: broker offline al arrancar, M: reconexion tras broker
disponible/reiniciado, Q: cancelacion). Ejercita `AgentRuntime.run_forever`
(el MISMO bucle que usa `python -m windows_agent run`) contra el broker real
(PostgreSQL real + mTLS real)."""

from __future__ import annotations

import socket
import threading
import time
from unittest.mock import patch

from app.broker.dispatch import dispatch_job_to_agent
from app.broker.server import BrokerServer
from tests.integration.broker.conftest import cleanup_tenant, commit_tenant_user
from tests.integration.windows_agent_e2.test_windows_agent_lifecycle import (
    _ca_pem,
    _config_for,
    _create_real_session_for_agent_job,
    _pair_real_agent,
)
from windows_agent.broker_client import OperationalBrokerClient
from windows_agent.config import AgentConfig
from windows_agent.runtime import RECONNECT_WAIT_SECONDS, AgentRuntime


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _fast_client_factory(config: AgentConfig):
    """Mismo cliente productivo; solo se acorta el backoff INTERNO para que
    la prueba no tarde ~15s por intento (la escala real la cubren las pruebas
    unitarias de `call_with_backoff`)."""

    def _factory(ssl_context):
        return OperationalBrokerClient(
            operational_base_url=config.operational_base_url,
            ssl_context=ssl_context,
            protocol_version=config.protocol_version,
            timeout=2.0,
            backoff_sequence=(0.0, 0.0),
            sleep=lambda s: None,
        )

    return _factory


def _start_broker(like: BrokerServer, session_factory, *, pairing_port=None, operational_port=None) -> BrokerServer:
    server = BrokerServer(
        host="127.0.0.1",
        pairing_port=pairing_port if pairing_port is not None else like.pairing_bound_port,
        operational_port=operational_port if operational_port is not None else like.operational_bound_port,
        ca=like.ca,
        session_factory=session_factory,
        handshake_timeout_seconds=5.0,
        idle_timeout_seconds=5.0,
        read_timeout_seconds=5.0,
    )
    server.start()
    return server


def _wait_until(predicate, timeout=15.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _paired(session_factory, running_broker, tmp_path):
    setup = commit_tenant_user(session_factory)
    store = _pair_real_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"], tmp_path)
    return setup, store


def test_L_broker_offline_agent_survives_with_bounded_backoff_and_no_secret_request(
    session_factory, running_broker, tmp_path
):
    """L: el agente arranca con el broker NO disponible. El proceso sobrevive
    (run_forever no lanza), espera con la escala acotada (sin bucle activo),
    nunca solicita credencial y no toca la identidad."""
    setup, store = _paired(session_factory, running_broker, tmp_path)
    try:
        ca_pem = _ca_pem(running_broker)
        identity = store.load()
        identity_files_before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
        # broker "offline": puertos que nadie escucha
        config = AgentConfig(pairing_port=_free_port(), operational_port=_free_port())

        runtime = AgentRuntime(
            identity=identity,
            config=config,
            ssl_context_factory=lambda: store.build_client_ssl_context(ca_certificate_pem=ca_pem),
            client_factory=_fast_client_factory(config),
        )
        waits: list[float] = []
        threads_before = threading.active_count()

        with patch.object(OperationalBrokerClient, "_request_credential_once") as credential_spy:
            runtime.run_forever(
                should_stop=lambda: sum(waits) >= 62.0,  # 2+4+8+16+30 = 60s simulados de espera
                sleep=lambda s: waits.append(s),
            )
            credential_spy.assert_not_called()

        # BROKER_OFFLINE_BACKOFF_BOUNDED: la escala observada es exactamente la
        # del producto, creciente y acotada en su ultimo valor.
        slices = list(waits)
        assert max(slices) <= 0.5  # rebanadas acotadas: la parada se atiende con rapidez
        assert sum(slices) >= sum(RECONNECT_WAIT_SECONDS[:5])  # espera real, no bucle activo
        assert runtime.connection_id is None
        # sin hilos nuevos sin acotar
        assert threading.active_count() <= threads_before + 1
        # identidad intacta
        assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == identity_files_before
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_L_each_wait_follows_the_documented_bounded_schedule(session_factory, running_broker, tmp_path):
    setup, store = _paired(session_factory, running_broker, tmp_path)
    try:
        ca_pem = _ca_pem(running_broker)
        config = AgentConfig(pairing_port=_free_port(), operational_port=_free_port())
        runtime = AgentRuntime(
            identity=store.load(),
            config=config,
            ssl_context_factory=lambda: store.build_client_ssl_context(ca_certificate_pem=ca_pem),
            client_factory=_fast_client_factory(config),
        )
        attempts = {"n": 0}
        original = runtime.connect

        def _counting_connect():
            attempts["n"] += 1
            return original()

        runtime.connect = _counting_connect  # type: ignore[method-assign]
        chosen: list[float] = []
        orig_wait = runtime._wait

        def _record_wait(seconds, should_stop, sleep):
            chosen.append(seconds)
            return orig_wait(seconds, should_stop, lambda s: None)

        runtime._wait = _record_wait  # type: ignore[method-assign]
        runtime.run_forever(should_stop=lambda: len(chosen) >= 8, sleep=lambda s: None)

        assert chosen == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0, 30.0]
        assert attempts["n"] == len(chosen)  # un intento por espera: nunca un bucle activo
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_M_A_agent_connects_after_broker_becomes_available(session_factory, running_broker, tmp_path):
    """M-A: el MISMO runtime arranca con el broker caido; al volver, establece
    una conexion NUEVA y valida."""
    setup, store = _paired(session_factory, running_broker, tmp_path)
    broker2 = None
    try:
        ca_pem = _ca_pem(running_broker)
        ports = (running_broker.pairing_bound_port, running_broker.operational_bound_port)
        running_broker.stop()  # broker caido
        config = AgentConfig(pairing_port=ports[0], operational_port=ports[1])
        runtime = AgentRuntime(
            identity=store.load(),
            config=config,
            ssl_context_factory=lambda: store.build_client_ssl_context(ca_certificate_pem=ca_pem),
            client_factory=_fast_client_factory(config),
        )
        stop = threading.Event()
        thread = threading.Thread(
            target=lambda: runtime.run_forever(
                should_stop=stop.is_set,
                loop_interval_seconds=0.05,
                reconnect_wait_seconds=(0.05, 0.1),
                sleep=lambda s: time.sleep(min(s, 0.02)),
            ),
            daemon=True,
        )
        thread.start()
        time.sleep(0.4)
        assert runtime.connection_id is None  # sigue vivo pero sin conexion
        assert thread.is_alive()

        broker2 = _start_broker(running_broker, session_factory, pairing_port=ports[0], operational_port=ports[1])
        assert _wait_until(lambda: runtime.connection_id is not None)
        assert runtime.state.value == "CONNECTED"
        stop.set()
        thread.join(5)
        assert not thread.is_alive()
    finally:
        if broker2 is not None:
            broker2.stop()
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_M_B_agent_reconnects_with_new_connection_id_after_broker_restart(
    session_factory, running_broker, tmp_path
):
    """M-B: con conexion establecida, el broker se reinicia (registro de
    conexiones vacio). El agente detecta la perdida y hace un HELLO NUEVO:
    `connection_id` distinto y nunca se reutiliza el anterior."""
    setup, store = _paired(session_factory, running_broker, tmp_path)
    broker2 = None
    try:
        ca_pem = _ca_pem(running_broker)
        ports = (running_broker.pairing_bound_port, running_broker.operational_bound_port)
        config = AgentConfig(pairing_port=ports[0], operational_port=ports[1])
        runtime = AgentRuntime(
            identity=store.load(),
            config=config,
            ssl_context_factory=lambda: store.build_client_ssl_context(ca_certificate_pem=ca_pem),
            client_factory=_fast_client_factory(config),
        )

        sent_ids: list[str] = []
        original_headers = OperationalBrokerClient._connection_headers

        def _recording_headers(self, connection_id):
            sent_ids.append(connection_id)
            return original_headers(self, connection_id)

        stop = threading.Event()
        with patch.object(OperationalBrokerClient, "_connection_headers", _recording_headers):
            thread = threading.Thread(
                target=lambda: runtime.run_forever(
                    should_stop=stop.is_set,
                    loop_interval_seconds=0.05,
                    reconnect_wait_seconds=(0.05, 0.1),
                    sleep=lambda s: time.sleep(min(s, 0.02)),
                ),
                daemon=True,
            )
            thread.start()
            assert _wait_until(lambda: runtime.connection_id is not None and len(sent_ids) >= 2)
            old_id = runtime.connection_id

            running_broker.stop()  # reinicio del broker: estado process-local perdido
            time.sleep(0.3)
            broker2 = _start_broker(running_broker, session_factory, pairing_port=ports[0], operational_port=ports[1])

            assert _wait_until(lambda: runtime.connection_id is not None and runtime.connection_id != old_id)
            new_id = runtime.connection_id
            marker = len(sent_ids)
            assert _wait_until(lambda: len(sent_ids) >= marker + 2)  # sigue operando con el id nuevo
            stop.set()
            thread.join(5)

        assert new_id != old_id  # STALE_CONNECTION_ID_REUSED=NO
        post_new = sent_ids[sent_ids.index(new_id):]
        assert old_id not in post_new  # STALE_CONNECTION_LEASE_REUSED=NO: nunca se vuelve a presentar el id viejo
        assert runtime.state.value in {"CONNECTED", "DISCONNECTED"}
    finally:
        if broker2 is not None:
            broker2.stop()
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_Q_cancellation_observed_reported_and_never_followed_by_credential_fetch(
    session_factory, running_broker, tmp_path, monkeypatch, caplog
):
    """Q: job reclamado + cancelacion del operador. El agente observa el
    `cancel_notice` en el heartbeat, lo reporta (evento estructurado + estado
    local liberado -- el contrato NO exige confirmacion), no solicita
    credencial, y un pedido posterior de credencial es rechazado por el
    backend sin entregar secreto. Idempotente: un segundo heartbeat no repite
    el aviso."""
    from app.services import sol_access_session_service as session_service

    setup, store = _paired(session_factory, running_broker, tmp_path)
    try:
        ca_pem = _ca_pem(running_broker)
        identity = store.load()
        runtime = AgentRuntime(
            identity=identity,
            config=_config_for(running_broker),
            ssl_context_factory=lambda: store.build_client_ssl_context(ca_certificate_pem=ca_pem),
        )
        runtime.connect()
        _client_id, _credential_id, session_id = _create_real_session_for_agent_job(
            session_factory, monkeypatch, setup
        )
        dsession = session_factory()
        try:
            job_id = dispatch_job_to_agent(
                dsession,
                tenant_id=setup["tenant_id"],
                session_id=session_id,
                agent_id=identity.agent_id,
                pending_dispatch=running_broker.pending_dispatch,
            )
            dsession.commit()
        finally:
            dsession.close()
        assert runtime.poll_and_claim_once() == job_id
        assert runtime._active_job_id == job_id

        csession = session_factory()
        try:
            session_service.cancel_session(
                csession, tenant_id=setup["tenant_id"], session_id=session_id, actor_user_id=setup["admin_id"]
            )
            csession.commit()
        finally:
            csession.close()

        with patch.object(
            OperationalBrokerClient, "_request_credential_once", wraps=runtime.credential_client()._request_credential_once
        ) as credential_spy, caplog.at_level("INFO", logger="windows_agent"):
            first = runtime.heartbeat_once()
            assert first["cancel_notice"]["job_id"] == str(job_id)  # AGENT_CANCELLATION_OBSERVED
            assert runtime._active_job_id is None  # liberado localmente: no sigue ejecutando ese job
            assert "job_cancellation_observed" in caplog.text  # AGENT_CANCELLATION_REPORTED
            assert str(job_id) in caplog.text

            second = runtime.heartbeat_once()
            assert second["cancel_notice"] is None  # idempotente: no se repite el aviso
            assert runtime._active_job_id is None

            # el bucle del agente nunca pidio credencial
            credential_spy.assert_not_called()

        # y si alguien la pidiera igualmente, el backend la rechaza sin secreto
        import pytest

        from windows_agent.broker_client import CredentialFetchError

        with pytest.raises(CredentialFetchError):
            with runtime.credential_client().fetch_credential_for_job(
                connection_id=runtime.connection_id, job_id=job_id
            ):
                pass
        runtime.stop()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])
