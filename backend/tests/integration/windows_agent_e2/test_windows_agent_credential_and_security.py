"""Fase 5, Bloque 5.2, Domain E2 -- matriz X/Y/Z/AA/AB/AQ/AH/R/S/T/U/V del
encargo: capacidad EXPLICITA de solicitud de credencial contra el
endpoint REAL de Domain E1-B (`E2_REAL_E1B_SECRET_ENDPOINT_INTEGRATION=
PASS`), y los bordes de seguridad de conexion/identidad (revocado,
certificado desconocido, identidad duplicada, no exfiltracion de la
clave privada)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

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
from windows_agent.runtime import AgentRevokedError, AgentRuntime


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
    return {"app_session_id": app_session_id, "client_id": client_id, "ruc": ruc, "session_id": session_id}


def _connect_and_claim(session_factory, running_broker, setup, monkeypatch, data_dir):
    store = _pair_real_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"], data_dir)
    identity = store.load()
    config = _config_for(running_broker)
    ca_pem = _ca_pem(running_broker)
    runtime = AgentRuntime(
        identity=identity,
        config=config,
        ssl_context_factory=lambda: store.build_client_ssl_context(ca_certificate_pem=ca_pem),
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
    claimed = runtime.poll_and_claim_once()
    assert claimed == job_id
    return runtime, store, extra, job_id


def test_X_AQ_explicit_credential_fetch_against_real_e1b_endpoint(
    session_factory, running_broker, tmp_path, monkeypatch
):
    """X/AQ: la capacidad de solicitud de credencial, invocada
    EXPLICITAMENTE (nunca por el bucle normal -- ver
    `test_N_O_P_W_...` en el otro archivo), consume el endpoint REAL de
    Domain E1-B con una credencial de boveda FALSA pero cifrada de verdad.
    `E2_REAL_E1B_SECRET_ENDPOINT_INTEGRATION=PASS`."""
    setup = commit_tenant_user(session_factory)
    try:
        runtime, store, extra, job_id = _connect_and_claim(
            session_factory, running_broker, setup, monkeypatch, tmp_path
        )
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
            assert credential.ruc == extra["ruc"]
            assert credential.sol_username == TEST_SOL_USER
            assert credential.sol_password == TEST_SOL_PASSWORD_DO_NOT_USE

        # AU: tras el bloque `with`, las referencias se sueltan.
        assert credential.ruc is None
        assert credential.sol_username is None
        assert credential.sol_password is None

        # AA: un segundo intento (gate ya consumido) falla, nunca se
        # reintenta automaticamente desde el propio metodo.
        with pytest.raises(CredentialFetchError):
            with client.fetch_credential_for_job(connection_id=runtime.connection_id, job_id=job_id):
                pass

        runtime.stop()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_R_revoked_agent_fails_closed(session_factory, running_broker, tmp_path):
    """R: un agente revocado tras conectar es rechazado en la siguiente
    operacion -- transiciona a `REVOKED`, nunca se re-empareja
    automaticamente."""
    from app.services import sol_agent_service

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

        revoke_session = session_factory()
        try:
            sol_agent_service.revoke_agent(
                revoke_session,
                tenant_id=setup["tenant_id"],
                agent_id=identity.agent_id,
                actor_user_id=setup["admin_id"],
            )
            revoke_session.commit()
        finally:
            revoke_session.close()

        with pytest.raises(AgentRevokedError):
            runtime.heartbeat_once()
        assert runtime.state.value == "REVOKED"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_S_expired_certificate_fails_closed(session_factory, running_broker, tmp_path):
    """S: un certificado ya vencido nunca completa el handshake mTLS --
    fallo cerrado de TLS, sin bypass de pairing."""
    from cryptography.hazmat.primitives import serialization

    from app.broker import pki as pki_module

    setup = commit_tenant_user(session_factory)
    try:
        # construye un certificado YA vencido firmado por la MISMA CA del
        # broker (nunca autogenerada aparte) para aislar exactamente la
        # condicion de expiracion.
        key, csr_pem = pki_module.build_agent_csr(common_name="expired-test-agent")
        csr = pki_module.parse_and_validate_csr(csr_pem)
        import datetime as dt

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.x509.oid import NameOID

        now = dt.datetime.now(dt.UTC)
        builder = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "expired-test-agent")]))
            .issuer_name(running_broker.ca.certificate.subject)
            .public_key(csr.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=10))
            .not_valid_after(now - dt.timedelta(days=1))  # YA VENCIDO
        )
        expired_cert = builder.sign(running_broker.ca.key, hashes.SHA256())

        cert_path = tmp_path / "agent.cert.pem"
        key_path = tmp_path / "agent.key.pem"
        cert_path.write_bytes(expired_cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

        import ssl

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(cadata=_ca_pem(running_broker).decode("ascii"))
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))

        import httpx

        with httpx.Client(verify=ctx, timeout=5.0) as client:
            with pytest.raises(httpx.TransportError):
                client.post(
                    f"{_config_for(running_broker).operational_base_url}/agent/hello",
                    json={"protocol_version": 1},
                    headers={"X-Sol-Agent-Protocol": "1"},
                )
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_T_unknown_certificate_fails_closed(session_factory, running_broker, tmp_path):
    """T: un certificado firmado por una CA DISTINTA (nunca visto por el
    broker) es rechazado -- fallo cerrado de confianza TLS, sin
    auto-registro."""
    from app.broker import pki as pki_module

    setup = commit_tenant_user(session_factory)
    try:
        rogue_ca = pki_module.generate_ca(common_name="rogue test CA")
        key, csr_pem = pki_module.build_agent_csr(common_name="unknown-cert-agent")
        rogue_cert = pki_module.sign_agent_csr(
            ca=rogue_ca, csr_pem=csr_pem, agent_common_name="unknown-cert-agent"
        )

        from cryptography.hazmat.primitives import serialization

        cert_path = tmp_path / "agent.cert.pem"
        key_path = tmp_path / "agent.key.pem"
        cert_path.write_bytes(rogue_cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

        import ssl

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(cadata=_ca_pem(running_broker).decode("ascii"))
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))

        import httpx

        with httpx.Client(verify=ctx, timeout=5.0) as client:
            with pytest.raises(httpx.TransportError):
                client.post(
                    f"{_config_for(running_broker).operational_base_url}/agent/hello",
                    json={"protocol_version": 1},
                    headers={"X-Sol-Agent-Protocol": "1"},
                )
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_AH_agent_private_key_never_transported_to_backend(session_factory, running_broker, tmp_path):
    """AH: barrido de todas las peticiones reales enviadas durante un
    pairing+HELLO completo -- la clave privada del agente NUNCA aparece en
    ningun cuerpo de peticion. `AGENT_PRIVATE_KEY_TRANSPORTED_TO_BACKEND=
    NO`."""
    import httpx

    captured_bodies: list[bytes] = []
    original_post = httpx.Client.post

    def _spy_post(self, *args, **kwargs):
        response = original_post(self, *args, **kwargs)
        if "content" in kwargs:
            captured_bodies.append(kwargs["content"] if isinstance(kwargs["content"], bytes) else b"")
        if "json" in kwargs:
            import json as _json

            captured_bodies.append(_json.dumps(kwargs["json"]).encode("utf-8"))
        return response

    setup = commit_tenant_user(session_factory)
    try:
        import unittest.mock as mock

        with mock.patch.object(httpx.Client, "post", _spy_post):
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
            runtime.stop()

        raw_key_pem = (tmp_path / "agent.key.pem").read_bytes()
        assert b"ENCRYPTED PRIVATE KEY" in raw_key_pem  # E2.1-A: nunca PEM en claro en disco
        for body in captured_bodies:
            assert raw_key_pem not in body
            assert b"PRIVATE KEY" not in body
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])
