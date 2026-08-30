"""Fase 5, Bloque 5.2, Domain D -- matriz de mTLS real exigida por el
encargo (seccion 59): TLS/mTLS real de extremo a extremo en cada escenario,
nunca solo SSL mockeado. Material criptografico efimero generado en
directorios temporales, nunca comprometido al repositorio."""

from __future__ import annotations

import datetime
import ssl

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID

from app.broker import pki
from tests.integration.broker.agent_test_client import AgentTestClient
from tests.integration.broker.conftest import (
    cleanup_tenant,
    commit_credential,
    commit_session,
    commit_tenant_user,
    create_pairing_over_service,
)


def _paired_agent(session_factory, running_broker, tenant_id, admin_id) -> AgentTestClient:
    from cryptography.hazmat.primitives import serialization as ser

    pairing_id, pairing_code = create_pairing_over_service(
        session_factory, tenant_id=tenant_id, requested_by_user_id=admin_id
    )
    ca_cert_pem = running_broker.ca.certificate.public_bytes(ser.Encoding.PEM)
    agent = AgentTestClient(
        ca_certificate_pem=ca_cert_pem,
        pairing_base_url=f"https://127.0.0.1:{running_broker.pairing_bound_port}",
        operational_base_url=f"https://127.0.0.1:{running_broker.operational_bound_port}",
    )
    response = agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code)
    assert response.status_code == 200, response.text
    hello_response = agent.hello()
    assert hello_response.status_code == 200, hello_response.text
    return agent


def _issue_offbook_agent_cert(*, ca: pki.LocalCa, not_before_offset_days: int = 0, valid_days: int = 90):
    """Firma un certificado de agente REAL con `ca`, saltandose
    deliberadamente el flujo de pairing (nunca pasa por `confirm_pairing`,
    nunca registra fila en `sol_agents`) -- usado UNICAMENTE para construir
    los escenarios D (huella desconocida) y F (vencido/no-vigente-aun) de la
    matriz, que por definicion no pueden nacer de un pairing real."""
    key, csr_pem = pki.build_agent_csr(common_name="offbook-test-agent")
    csr = x509.load_pem_x509_csr(csr_pem)
    now = datetime.datetime.now(datetime.UTC)
    not_before = now + datetime.timedelta(days=not_before_offset_days)
    not_after = not_before + datetime.timedelta(days=valid_days)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "offbook-test-agent")]))
        .issuer_name(ca.certificate.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False
        )
        .sign(ca.key, hashes.SHA256())
    )
    return key, cert


def _mtls_context(*, ca_cert_pem: bytes, client_key, client_cert, tmp_path) -> ssl.SSLContext:
    key_path = tmp_path / "offbook.key.pem"
    cert_path = tmp_path / "offbook.cert.pem"
    key_path.write_bytes(
        client_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(client_cert.public_bytes(serialization.Encoding.PEM))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cadata=ca_cert_pem.decode("ascii"))
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    return ctx


def test_A_valid_ca_and_active_agent_cert_is_authenticated(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        response = agent.hello()
        assert response.status_code == 200, response.text
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_B_no_client_certificate_is_rejected(running_broker):
    from cryptography.hazmat.primitives import serialization as ser

    ca_cert_pem = running_broker.ca.certificate.public_bytes(ser.Encoding.PEM)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cadata=ca_cert_pem.decode("ascii"))
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2

    with pytest.raises((httpx.TransportError, ssl.SSLError, OSError)):
        with httpx.Client(verify=ctx, timeout=5.0) as client:
            client.post(
                f"https://127.0.0.1:{running_broker.operational_bound_port}/agent/hello",
                json={"protocol_version": 1},
            )


def test_C_untrusted_ca_client_certificate_is_rejected(running_broker, tmp_path):
    other_ca = pki.generate_ca(common_name="Untrusted rogue CA")
    key, cert = _issue_offbook_agent_cert(ca=other_ca)
    from cryptography.hazmat.primitives import serialization as ser

    broker_ca_cert_pem = running_broker.ca.certificate.public_bytes(ser.Encoding.PEM)
    ctx = _mtls_context(ca_cert_pem=broker_ca_cert_pem, client_key=key, client_cert=cert, tmp_path=tmp_path)

    with pytest.raises((httpx.TransportError, ssl.SSLError, OSError)):
        with httpx.Client(verify=ctx, timeout=5.0) as client:
            client.post(
                f"https://127.0.0.1:{running_broker.operational_bound_port}/agent/hello",
                json={"protocol_version": 1},
            )


def test_D_valid_ca_but_unknown_fingerprint_is_rejected(running_broker, tmp_path):
    """Certificado firmado por la CA REAL del broker (la cadena TLS es
    valida) pero que NUNCA paso por `confirm_pairing` -- ninguna fila en
    `sol_agents` tiene esa huella. El handshake TLS tiene exito; el rechazo
    ocurre en la capa de aplicacion (`AGENT_UNKNOWN`)."""
    key, cert = _issue_offbook_agent_cert(ca=running_broker.ca)
    from cryptography.hazmat.primitives import serialization as ser

    broker_ca_cert_pem = running_broker.ca.certificate.public_bytes(ser.Encoding.PEM)
    ctx = _mtls_context(ca_cert_pem=broker_ca_cert_pem, client_key=key, client_cert=cert, tmp_path=tmp_path)

    with httpx.Client(verify=ctx, timeout=5.0) as client:
        response = client.post(
            f"https://127.0.0.1:{running_broker.operational_bound_port}/agent/hello",
            json={"protocol_version": 1},
            headers={"X-Sol-Agent-Protocol": "1"},
        )
    assert response.status_code == 401
    assert response.json()["error"] == "AGENT_UNKNOWN"


def test_E_known_fingerprint_but_revoked_agent_is_rejected(session_factory, running_broker):
    from app.services import sol_agent_service

    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        assert agent.hello().status_code == 200

        session = session_factory()
        try:
            sol_agent_service.revoke_agent(
                session, tenant_id=setup["tenant_id"], agent_id=agent.agent_id, actor_user_id=setup["admin_id"]
            )
            session.commit()
        finally:
            session.close()

        response = agent.hello()
        assert response.status_code == 401
        assert response.json()["error"] == "AGENT_REVOKED"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_F_expired_certificate_is_rejected(running_broker, tmp_path):
    key, cert = _issue_offbook_agent_cert(ca=running_broker.ca, not_before_offset_days=-30, valid_days=10)
    from cryptography.hazmat.primitives import serialization as ser

    broker_ca_cert_pem = running_broker.ca.certificate.public_bytes(ser.Encoding.PEM)
    ctx = _mtls_context(ca_cert_pem=broker_ca_cert_pem, client_key=key, client_cert=cert, tmp_path=tmp_path)

    with pytest.raises((httpx.TransportError, ssl.SSLError, OSError)):
        with httpx.Client(verify=ctx, timeout=5.0) as client:
            client.post(
                f"https://127.0.0.1:{running_broker.operational_bound_port}/agent/hello",
                json={"protocol_version": 1},
            )


def test_F2_not_yet_valid_certificate_is_rejected(running_broker, tmp_path):
    key, cert = _issue_offbook_agent_cert(ca=running_broker.ca, not_before_offset_days=30, valid_days=10)
    from cryptography.hazmat.primitives import serialization as ser

    broker_ca_cert_pem = running_broker.ca.certificate.public_bytes(ser.Encoding.PEM)
    ctx = _mtls_context(ca_cert_pem=broker_ca_cert_pem, client_key=key, client_cert=cert, tmp_path=tmp_path)

    with pytest.raises((httpx.TransportError, ssl.SSLError, OSError)):
        with httpx.Client(verify=ctx, timeout=5.0) as client:
            client.post(
                f"https://127.0.0.1:{running_broker.operational_bound_port}/agent/hello",
                json={"protocol_version": 1},
            )


def test_G_authenticated_agent_cannot_access_another_tenants_job(session_factory, running_broker):
    """La huella de certificado es 1:1 con un `sol_agents` de UN tenant (ver
    `mtls.resolve_agent_identity`) -- por lo que este escenario prueba el
    binding de tenant en la capa de APLICACION (nunca en la capa TLS, que ya
    autentico correctamente al agente real de su propio tenant): un agente
    conectado y autenticado de Tenant A nunca puede descubrir/reclamar un
    job cuyo `session_id`/`job_id` pertenece a Tenant B."""
    setup_a = commit_tenant_user(session_factory)
    setup_b = commit_tenant_user(session_factory)
    try:
        agent_a = _paired_agent(session_factory, running_broker, setup_a["tenant_id"], setup_a["admin_id"])
        agent_b = _paired_agent(session_factory, running_broker, setup_b["tenant_id"], setup_b["admin_id"])

        client_id_b, credential_id_b = commit_credential(session_factory, setup_b["tenant_id"], setup_b["admin_id"])
        session_id_b = commit_session(
            session_factory,
            tenant_id=setup_b["tenant_id"],
            client_id=client_id_b,
            credential_id=credential_id_b,
            requester_id=setup_b["admin_id"],
        )

        from app.broker.dispatch import dispatch_job_to_agent

        dispatch_session = session_factory()
        try:
            job_id_b = dispatch_job_to_agent(
                dispatch_session,
                tenant_id=setup_b["tenant_id"],
                session_id=session_id_b,
                agent_id=agent_b.agent_id,
                pending_dispatch=running_broker.pending_dispatch,
            )
            dispatch_session.commit()
        finally:
            dispatch_session.close()

        # Agente A (autenticado, tenant A) intenta reclamar el job de B.
        claim_response = agent_a.claim(job_id_b)
        assert claim_response.status_code == 404
        assert claim_response.json()["error"] == "JOB_UNAVAILABLE"

        # El propio descubrimiento de A nunca ofrece el job de B.
        next_response = agent_a.jobs_next()
        assert next_response.json()["job_id"] is None
    finally:
        cleanup_tenant(session_factory, setup_a["tenant_id"])
        cleanup_tenant(session_factory, setup_b["tenant_id"])
