"""Fase 5, Bloque 5.2, Domain E2 -- matriz AC/AD/AE/AF/AG/AB/AN/AP/U/V/AV/
AW del encargo: barrido de residuos de secretos (disco/entorno/argv/
registro), ninguna redelivery automatica tras ambiguedad, apagado limpio
suelta el estado en memoria, identidad duplicada respeta el lease de
conexion, y el codigo productivo del paquete nunca referencia SUNAT ni
Playwright."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.broker.dispatch import dispatch_job_to_agent
from app.core.sol_vault_registry import vault_unlock_registry
from app.models.session import Session as AppSession
from app.services import sol_agent_credential_delivery_service as delivery_service
from tests.integration.broker._secret_delivery_helpers import (
    TEST_SOL_PASSWORD_DO_NOT_USE,
    TEST_SOL_USER,
    commit_client,
    commit_sol_access_session,
    create_real_credential,
    get_user_row,
    init_vault_and_confirm_recovery,
)
from tests.integration.broker.conftest import cleanup_tenant, commit_tenant_user, create_pairing_over_service
from tests.integration.windows_agent_e2._fake_identity_store import FakeIdentityStore
from windows_agent.broker_client import CredentialFetchError
from windows_agent.config import AgentConfig
from windows_agent.pairing import pair_agent
from windows_agent.runtime import AgentRuntime


def _ca_pem(running_broker) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return running_broker.ca.certificate.public_bytes(serialization.Encoding.PEM)


def _config_for(running_broker) -> AgentConfig:
    return AgentConfig(
        pairing_port=running_broker.pairing_bound_port, operational_port=running_broker.operational_bound_port
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


def _full_real_session(session_factory, monkeypatch, setup):
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

    client_id, ruc = commit_client(session_factory, setup["tenant_id"])
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
    return {"app_session_id": app_session_id, "ruc": ruc, "session_id": session_id}


def test_AC_AD_AE_AF_AG_no_secret_residue_anywhere_after_real_credential_fetch(
    session_factory, running_broker, tmp_path, monkeypatch, caplog
):
    """Ciclo completo real (pairing->HELLO->claim->autorizar->solicitar
    credencial explicitamente->descartar) con sentinelas UNICOS por
    corrida -- barrido de disco/entorno/logs tras el ciclo.
    `RUC_RESIDUE=0`, `USERNAME_RESIDUE=0`, `PASSWORD_RESIDUE=0`,
    `SOL_SECRET_IN_AGENT_ENVIRONMENT=NO`, `SOL_SECRET_RESIDUE_ON_DISK=NO`."""
    import logging

    caplog.set_level(logging.DEBUG)
    env_snapshot_before = dict(os.environ)

    setup = commit_tenant_user(session_factory)
    try:
        store = _pair_real_agent(
            session_factory, running_broker, setup["tenant_id"], setup["admin_id"], tmp_path
        )
        identity = store.load()
        runtime = AgentRuntime(
            identity=identity,
            config=_config_for(running_broker),
            ssl_context_factory=lambda: store.build_client_ssl_context(
                ca_certificate_pem=_ca_pem(running_broker)
            ),
        )
        runtime.connect()

        extra = _full_real_session(session_factory, monkeypatch, setup)
        dispatch_session = session_factory()
        try:
            job_id = dispatch_job_to_agent(
                dispatch_session,
                tenant_id=setup["tenant_id"],
                session_id=extra["session_id"],
                agent_id=identity.agent_id,
                pending_dispatch=running_broker.pending_dispatch,
            )
            dispatch_session.commit()
        finally:
            dispatch_session.close()
        assert runtime.poll_and_claim_once() == job_id

        actor = get_user_row(session_factory, setup["admin_id"])
        delivery_service.authorize_credential_delivery(
            session_factory,
            grant_registry=running_broker.secret_delivery_grants,
            vault_registry=vault_unlock_registry,
            tenant_id=setup["tenant_id"],
            session_id=extra["session_id"],
            job_id=job_id,
            actor=actor,
            app_session_id=extra["app_session_id"],
        )

        client = runtime.credential_client()
        with client.fetch_credential_for_job(
            connection_id=runtime.connection_id, job_id=job_id
        ) as credential:
            assert credential.sol_username == TEST_SOL_USER  # confirma que SI se recibio el sentinela

        runtime.stop()

        # --- barrido, DESPUES de descartar la credencial ---
        sentinels = [extra["ruc"], TEST_SOL_USER, TEST_SOL_PASSWORD_DO_NOT_USE]

        for path in Path(tmp_path).rglob("*"):
            if path.is_file():
                content = path.read_bytes()
                for sentinel in sentinels:
                    assert sentinel.encode("ascii") not in content, f"residuo en disco: {path}"

        assert TEST_SOL_USER not in caplog.text
        assert TEST_SOL_PASSWORD_DO_NOT_USE not in caplog.text
        assert extra["ruc"] not in caplog.text

        env_after = dict(os.environ)
        assert env_after == env_snapshot_before  # el proceso nunca escribio su propio entorno
        for sentinel in sentinels:
            assert sentinel not in " ".join(f"{k}={v}" for k, v in env_after.items())
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_AB_no_generic_retry_on_ambiguous_transport_failure_during_credential_fetch(
    session_factory, running_broker, tmp_path, monkeypatch
):
    """AB: una ambiguedad de TRANSPORTE (nunca un rechazo de aplicacion)
    durante la solicitud de credencial nunca se reintenta automaticamente
    desde el propio metodo -- `AGENT_AUTOMATIC_SECRET_RETRY_AFTER_
    AMBIGUOUS_FAILURE=NO`."""
    setup = commit_tenant_user(session_factory)
    try:
        store = _pair_real_agent(
            session_factory, running_broker, setup["tenant_id"], setup["admin_id"], tmp_path
        )
        identity = store.load()
        runtime = AgentRuntime(
            identity=identity,
            config=_config_for(running_broker),
            ssl_context_factory=lambda: store.build_client_ssl_context(
                ca_certificate_pem=_ca_pem(running_broker)
            ),
        )
        runtime.connect()

        extra = _full_real_session(session_factory, monkeypatch, setup)
        dispatch_session = session_factory()
        try:
            job_id = dispatch_job_to_agent(
                dispatch_session,
                tenant_id=setup["tenant_id"],
                session_id=extra["session_id"],
                agent_id=identity.agent_id,
                pending_dispatch=running_broker.pending_dispatch,
            )
            dispatch_session.commit()
        finally:
            dispatch_session.close()
        assert runtime.poll_and_claim_once() == job_id

        actor = get_user_row(session_factory, setup["admin_id"])
        delivery_service.authorize_credential_delivery(
            session_factory,
            grant_registry=running_broker.secret_delivery_grants,
            vault_registry=vault_unlock_registry,
            tenant_id=setup["tenant_id"],
            session_id=extra["session_id"],
            job_id=job_id,
            actor=actor,
            app_session_id=extra["app_session_id"],
        )

        client = runtime.credential_client()
        call_count = {"n": 0}
        import httpx as httpx_module

        def _fail_once(*args, **kwargs):
            call_count["n"] += 1
            raise httpx_module.ConnectError("simulated ambiguous transport failure")

        monkeypatch.setattr(client._client, "stream", _fail_once)

        with pytest.raises(CredentialFetchError) as excinfo:
            with client.fetch_credential_for_job(connection_id=runtime.connection_id, job_id=job_id):
                pass
        assert excinfo.value.code == "TRANSPORT_AMBIGUOUS"
        assert call_count["n"] == 1  # exactamente un intento -- nunca reintentado

        runtime.stop()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_U_V_duplicate_identity_process_respects_secret_lease(session_factory, running_broker, tmp_path):
    """U/V: dos instancias de runtime con la MISMA identidad -- antes de un
    lease de secreto, `NEW_REPLACES_OLD` normal aplica (el segundo HELLO
    reemplaza la conexion del primero); esto ya esta cubierto de forma
    exhaustiva en Domain D.1 (`test_broker_logical_connection_identity.py`)
    y en Domain E1-B (`test_N_hello_replacement_rejected_while_secret_
    lease_active`, `test_broker_secret_delivery_lease_races.py`) -- esta
    prueba confirma especificamente que el Agente PRODUCTIVO (no solo el
    arnes `AgentTestClient`) observa la MISMA semantica: una segunda
    conexion con la misma identidad reemplaza la primera con normalidad
    fuera de la ventana de lease."""
    setup = commit_tenant_user(session_factory)
    try:
        store = _pair_real_agent(
            session_factory, running_broker, setup["tenant_id"], setup["admin_id"], tmp_path
        )
        identity = store.load()
        ssl_factory = lambda: store.build_client_ssl_context(ca_certificate_pem=_ca_pem(running_broker))  # noqa: E731

        runtime_a = AgentRuntime(
            identity=identity, config=_config_for(running_broker), ssl_context_factory=ssl_factory
        )
        runtime_a.connect()
        connection_a = runtime_a.connection_id

        runtime_b = AgentRuntime(
            identity=identity, config=_config_for(running_broker), ssl_context_factory=ssl_factory
        )
        runtime_b.connect()  # misma identidad -- reemplaza la conexion de A
        connection_b = runtime_b.connection_id
        assert connection_b != connection_a

        # la conexion vieja (A) ya no es valida
        import httpx

        with pytest.raises(httpx.HTTPStatusError):
            runtime_a.heartbeat_once()

        runtime_b.stop()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_AN_AP_clean_shutdown_drops_in_memory_secrets_and_state(session_factory, running_broker, tmp_path):
    """AN/AP: `stop()` limpio suelta `connection_id`/estado de job/cliente
    -- estado process-local, nunca dependido para la seguridad en si
    (seccion 65 del encargo: la seguridad real es que ningun secreto vive
    mas alla del bloque `with` en primer lugar, no que `stop()` se
    ejecute)."""
    setup = commit_tenant_user(session_factory)
    try:
        store = _pair_real_agent(
            session_factory, running_broker, setup["tenant_id"], setup["admin_id"], tmp_path
        )
        identity = store.load()
        runtime = AgentRuntime(
            identity=identity,
            config=_config_for(running_broker),
            ssl_context_factory=lambda: store.build_client_ssl_context(
                ca_certificate_pem=_ca_pem(running_broker)
            ),
        )
        runtime.connect()
        assert runtime.connection_id is not None
        runtime.stop()
        assert runtime.connection_id is None
        assert runtime.state.value == "DISCONNECTED"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_AV_AW_production_package_never_references_sunat_or_playwright():
    """AV/AW: barrido estatico del paquete PRODUCTIVO (nunca las pruebas,
    que usan sentinelas ficticios con nombres similares) -- ningun host de
    SUNAT, ninguna importacion de Playwright/Chromium."""
    import windows_agent

    package_dir = Path(windows_agent.__file__).parent
    forbidden = ["sunat.gob.pe", "playwright", "chromium", "Chromium"]
    for path in package_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for term in forbidden:
            assert term.lower() not in text.lower(), f"referencia prohibida '{term}' en {path}"


def test_cli_never_exposes_a_secret_bearing_argument():
    """`SOL_CREDENTIAL_ACCEPTED_FROM_CLI=NO` -- inspecciona el parser real
    (nunca solo la documentacion) para confirmar que ningun argumento
    puede llevar RUC/usuario/clave SOL."""
    from windows_agent.__main__ import build_parser

    parser = build_parser()
    all_option_strings: list[str] = []
    for action in parser._actions:
        all_option_strings.extend(action.option_strings)
    for subparser_action in [a for a in parser._actions if hasattr(a, "choices") and a.choices]:
        for sub in subparser_action.choices.values():
            for action in sub._actions:
                all_option_strings.extend(action.option_strings)

    forbidden_fragments = ["ruc", "sol-user", "sol-password", "sol_user", "sol_password"]
    for option in all_option_strings:
        lowered = option.lower()
        for fragment in forbidden_fragments:
            assert fragment not in lowered, f"opcion de CLI sospechosa: {option}"


def test_registry_never_used_by_agent_package():
    """`SOL_SECRET_PERSISTED_IN_WINDOWS_REGISTRY=NO` -- el paquete no usa
    el modulo `winreg` en absoluto (nunca persiste nada en el Registro de
    Windows, secreto o no)."""
    import windows_agent

    package_dir = Path(windows_agent.__file__).parent
    for path in package_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "winreg" not in text, f"uso inesperado de winreg en {path}"


def test_AL_AM_broker_traffic_bypasses_system_proxy_and_never_follows_redirects(
    session_factory, running_broker, tmp_path, monkeypatch
):
    """AL/AM: el broker es siempre loopback -- una variable de entorno de
    proxy del sistema (`HTTP_PROXY`/`HTTPS_PROXY`) NUNCA debe consultarse
    (`BROKER_TRAFFIC_USES_SYSTEM_PROXY=NO`); si SI se consultara, esta
    prueba fallaria por timeout/error de conexion intentando alcanzar un
    proxy inexistente en vez de completar el pairing/HELLO reales contra
    127.0.0.1. `follow_redirects=False` (`BROKER_REDIRECT_TO_REMOTE_
    ALLOWED=NO`) se confirma por inspeccion de construccion del cliente
    (`broker_client.py`)."""
    monkeypatch.setenv("HTTP_PROXY", "http://192.0.2.1:9999")  # TEST-NET-1, nunca enrutable
    monkeypatch.setenv("HTTPS_PROXY", "http://192.0.2.1:9999")
    monkeypatch.setenv("http_proxy", "http://192.0.2.1:9999")
    monkeypatch.setenv("https_proxy", "http://192.0.2.1:9999")

    setup = commit_tenant_user(session_factory)
    try:
        store = _pair_real_agent(
            session_factory, running_broker, setup["tenant_id"], setup["admin_id"], tmp_path
        )
        identity = store.load()
        runtime = AgentRuntime(
            identity=identity,
            config=_config_for(running_broker),
            ssl_context_factory=lambda: store.build_client_ssl_context(
                ca_certificate_pem=_ca_pem(running_broker)
            ),
        )
        runtime.connect()  # si el proxy (inexistente) se consultara, esto fallaria/colgaria
        assert runtime.connection_id is not None
        runtime.stop()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])
