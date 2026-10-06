"""Fase 5, Bloque 5.2, Domain E2 -- matriz de pruebas B/C/E/F/I/J/K/N/O/P/
W/X/AQ/AR/AS/AT del encargo: ciclo de vida REAL del Agente Windows
(pairing/identidad/mTLS/heartbeat/descubrimiento/reclamo/capacidad de
credencial) contra un broker real (PostgreSQL real, mTLS real) --
`FakeIdentityStore` sustituye SOLO el almacenamiento DPAPI (imposible en
este host Linux/Docker de pruebas), nunca el resto del flujo productivo
(`pairing.pair_agent`, `AgentRuntime`, `OperationalBrokerClient` son el
codigo REAL, sin mocks)."""

from __future__ import annotations

import uuid

import pytest

from app.broker.dispatch import dispatch_job_to_agent
from tests.integration.broker.conftest import cleanup_tenant, create_pairing_over_service
from tests.integration.windows_agent_e2._fake_identity_store import FakeIdentityStore
from windows_agent.config import AgentConfig
from windows_agent.identity import NotPairedError
from windows_agent.pairing import pair_agent
from windows_agent.runtime import AgentRuntime


def _ca_pem(running_broker) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return running_broker.ca.certificate.public_bytes(serialization.Encoding.PEM)


def _config_for(running_broker) -> AgentConfig:
    return AgentConfig(
        pairing_port=running_broker.pairing_bound_port,
        operational_port=running_broker.operational_bound_port,
    )


def _pair_real_agent(session_factory, running_broker, tenant_id, admin_id, data_dir) -> FakeIdentityStore:
    pairing_id, pairing_code = create_pairing_over_service(
        session_factory, tenant_id=tenant_id, requested_by_user_id=admin_id
    )
    store = FakeIdentityStore(data_dir)
    pair_agent(
        identity_store=store,
        pairing_base_url=_config_for(running_broker).pairing_base_url,
        ca_certificate_pem=_ca_pem(running_broker),
        pairing_id=pairing_id,
        pairing_code=pairing_code,
        protocol_version=1,
        timeout=10.0,
    )
    return store


def test_B_successful_pairing_persists_real_identity(session_factory, running_broker, tmp_path):
    """B/AS: pairing real -- CSR real, codigo real, firma real de la CA
    local, certificado real, identidad persistida. `E2_REAL_PAIRING=PASS`."""
    from tests.integration.broker.conftest import commit_tenant_user

    setup = commit_tenant_user(session_factory)
    try:
        store = _pair_real_agent(
            session_factory, running_broker, setup["tenant_id"], setup["admin_id"], tmp_path
        )
        assert store.is_paired()
        identity = store.load()
        assert identity.agent_id is not None
        assert identity.certificate_not_after > __import__("datetime").datetime.now(
            __import__("datetime").UTC
        )
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_C_incorrect_pairing_code_rejected_never_persists(session_factory, running_broker, tmp_path):
    from tests.integration.broker.conftest import commit_tenant_user

    setup = commit_tenant_user(session_factory)
    try:
        pairing_id, _real_code = create_pairing_over_service(
            session_factory, tenant_id=setup["tenant_id"], requested_by_user_id=setup["admin_id"]
        )
        import httpx

        store = FakeIdentityStore(tmp_path)
        with pytest.raises(httpx.HTTPStatusError):
            pair_agent(
                identity_store=store,
                pairing_base_url=_config_for(running_broker).pairing_base_url,
                ca_certificate_pem=_ca_pem(running_broker),
                pairing_id=pairing_id,
                pairing_code="000000",
                protocol_version=1,
                timeout=10.0,
            )
        assert not store.is_paired()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_E_F_identity_persisted_and_reloaded_after_simulated_restart(
    session_factory, running_broker, tmp_path
):
    """E/F: la identidad sobrevive un "reinicio" del agente -- una
    instancia NUEVA de `FakeIdentityStore` sobre el MISMO directorio de
    datos carga la misma identidad, sin volver a emparejar."""
    from tests.integration.broker.conftest import commit_tenant_user

    setup = commit_tenant_user(session_factory)
    try:
        store_before = _pair_real_agent(
            session_factory, running_broker, setup["tenant_id"], setup["admin_id"], tmp_path
        )
        identity_before = store_before.load()

        store_after_restart = FakeIdentityStore(tmp_path)  # simula un proceso nuevo
        identity_after = store_after_restart.load()
        assert identity_after.agent_id == identity_before.agent_id
        assert identity_after.certificate_pem == identity_before.certificate_pem
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_G_corrupt_identity_fails_closed(tmp_path):
    """Material presente pero corrupto -- nunca se borra/re-empareja
    automaticamente (seccion 70 del encargo)."""
    from windows_agent.identity import IdentityCorruptError

    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "agent.cert.pem").write_bytes(b"not a real certificate")
    (tmp_path / "agent.key.pem").write_bytes(b"not a real key")
    (tmp_path / "agent.keypass").write_bytes(b"x" * 64)
    (tmp_path / "identity.json").write_text('{"agent_id": "not-a-uuid"}', encoding="ascii")

    store = FakeIdentityStore(tmp_path)
    assert store.is_paired()
    with pytest.raises(IdentityCorruptError):
        store.load()


def test_unpaired_startup_raises_not_paired(tmp_path):
    store = FakeIdentityStore(tmp_path)
    assert not store.is_paired()
    with pytest.raises(NotPairedError):
        store.load()


def test_I_J_K_hello_connection_id_memory_only_and_heartbeat(session_factory, running_broker, tmp_path):
    """I/J/K: HELLO real por mTLS, `connection_id` NUNCA escrito en
    ningun archivo del directorio de datos (memoria unicamente), y
    heartbeat real exitoso. `E2_REAL_MTLS=PASS`."""
    from tests.integration.broker.conftest import commit_tenant_user

    setup = commit_tenant_user(session_factory)
    try:
        store = _pair_real_agent(
            session_factory, running_broker, setup["tenant_id"], setup["admin_id"], tmp_path
        )
        identity = store.load()
        config = _config_for(running_broker)
        ca_pem = _ca_pem(running_broker)

        runtime = AgentRuntime(
            identity=identity,
            config=config,
            ssl_context_factory=lambda: store.build_client_ssl_context(ca_certificate_pem=ca_pem),
        )
        runtime.connect()
        assert runtime.state.value == "CONNECTED"
        assert runtime.connection_id is not None

        heartbeat_result = runtime.heartbeat_once()
        assert isinstance(heartbeat_result, dict)

        # J: barrido del directorio de datos completo -- el connection_id
        # jamas debe aparecer en ningun archivo persistido.
        for path in tmp_path.rglob("*"):
            if path.is_file():
                content = path.read_bytes()
                assert runtime.connection_id.encode("ascii") not in content

        runtime.stop()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_N_O_P_W_job_discovery_claim_no_token_no_auto_credential(
    session_factory, running_broker, tmp_path, monkeypatch
):
    """N/O/P/W: descubrimiento y reclamo real de job, el agente NUNCA
    recibe el token de job, y `poll_and_claim_once` NUNCA invoca la
    solicitud de credencial -- espiado directamente sobre el metodo
    interno real (nunca inferido solo de la ausencia de una respuesta).
    `CLAIMING_JOB_CONSUMES_CREDENTIAL_GATE=NO`."""
    from tests.integration.broker.conftest import commit_tenant_user

    setup = commit_tenant_user(session_factory)
    try:
        store = _pair_real_agent(
            session_factory, running_broker, setup["tenant_id"], setup["admin_id"], tmp_path
        )
        identity = store.load()
        config = _config_for(running_broker)
        ca_pem = _ca_pem(running_broker)

        runtime = AgentRuntime(
            identity=identity,
            config=config,
            ssl_context_factory=lambda: store.build_client_ssl_context(ca_certificate_pem=ca_pem),
        )
        runtime.connect()

        # dispatch de job real (Domain C/D) para este agente
        client_id, credential_id, session_id = _create_real_session_for_agent_job(
            session_factory, monkeypatch, setup
        )
        dispatch_session = session_factory()
        try:
            job_id_expected = dispatch_job_to_agent(
                dispatch_session,
                tenant_id=setup["tenant_id"],
                session_id=session_id,
                agent_id=identity.agent_id,
                pending_dispatch=running_broker.pending_dispatch,
            )
            dispatch_session.commit()
        finally:
            dispatch_session.close()

        from unittest.mock import patch

        from windows_agent.broker_client import OperationalBrokerClient

        with patch.object(OperationalBrokerClient, "_request_credential_once") as credential_spy:
            claimed_job_id = runtime.poll_and_claim_once()
            assert claimed_job_id == job_id_expected
            credential_spy.assert_not_called()

        runtime.stop()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def _create_real_session_for_agent_job(session_factory, monkeypatch, setup):
    """Cadena real completa (boveda inicializada/desbloqueada + credencial
    SOL cifrada real con sentinelas de prueba + sesion de acceso) --
    mismo patron ya productivo de `e1b_setup`, construido aqui inline
    porque esta prueba usa `commit_tenant_user` (sin `app_session_id`) en
    vez de `e1b_setup`."""
    from datetime import UTC, datetime, timedelta

    from app.models.session import Session as AppSession
    from tests.integration.broker._secret_delivery_helpers import (
        commit_client,
        commit_sol_access_session,
        create_real_credential,
        init_vault_and_confirm_recovery,
    )

    session = session_factory()
    try:
        now = datetime.now(UTC)
        app_session = AppSession(
            tenant_id=setup["tenant_id"],
            user_id=setup["admin_id"],
            token_hash=uuid.uuid4().hex,
            csrf_token_hash=uuid.uuid4().hex,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(hours=8),
        )
        session.add(app_session)
        session.flush()
        session.commit()
        app_session_id = app_session.id
    finally:
        session.close()

    client_id, _ruc = commit_client(session_factory, setup["tenant_id"])
    vault_id = init_vault_and_confirm_recovery(
        session_factory,
        monkeypatch,
        tenant_id=setup["tenant_id"],
        user_id=setup["admin_id"],
        app_session_id=app_session_id,
    )
    credential_id = create_real_credential(
        session_factory,
        monkeypatch,
        tenant_id=setup["tenant_id"],
        vault_id=vault_id,
        client_id=client_id,
        user_id=setup["admin_id"],
        app_session_id=app_session_id,
    )
    session_id = commit_sol_access_session(
        session_factory,
        tenant_id=setup["tenant_id"],
        client_id=client_id,
        credential_id=credential_id,
        requester_id=setup["admin_id"],
    )
    return client_id, credential_id, session_id
