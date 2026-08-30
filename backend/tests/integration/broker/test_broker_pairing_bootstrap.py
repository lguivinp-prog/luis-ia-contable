"""Fase 5, Bloque 5.2, Domain D -- pruebas de emision de certificado de
agente durante el bootstrap de pairing (seccion 60 del encargo): CSR
valido+confirmacion correcta -> exactamente un certificado/agente;
doble-confirmacion/carrera -> nunca certificado/agente duplicado; CSR
invalido -> ningun certificado; pairing terminal -> ninguna emision; la
clave privada del agente NUNCA llega al backend."""

from __future__ import annotations

import threading
import uuid

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from sqlalchemy import select

from app.broker import pki
from app.models.sol_agent_access import SolAgent
from tests.integration.broker.agent_test_client import AgentTestClient
from tests.integration.broker.conftest import (
    cleanup_tenant,
    commit_tenant_user,
    create_pairing_over_service,
)


def _new_agent(running_broker) -> AgentTestClient:
    from cryptography.hazmat.primitives import serialization

    ca_cert_pem = running_broker.ca.certificate.public_bytes(serialization.Encoding.PEM)
    return AgentTestClient(
        ca_certificate_pem=ca_cert_pem,
        pairing_base_url=f"https://127.0.0.1:{running_broker.pairing_bound_port}",
        operational_base_url=f"https://127.0.0.1:{running_broker.operational_bound_port}",
    )


def _count_agents(session_factory, tenant_id) -> int:
    session = session_factory()
    try:
        return len(session.execute(select(SolAgent.id).where(SolAgent.tenant_id == tenant_id)).all())
    finally:
        session.close()


def test_A_valid_csr_and_correct_code_issue_exactly_one_agent_certificate(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    pairing_id, pairing_code = create_pairing_over_service(
        session_factory, tenant_id=setup["tenant_id"], requested_by_user_id=setup["admin_id"]
    )
    try:
        agent = _new_agent(running_broker)
        response = agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code)
        assert response.status_code == 200, response.text
        assert _count_agents(session_factory, setup["tenant_id"]) == 1

        session = session_factory()
        try:
            row = session.get(SolAgent, agent.agent_id)
            assert row.status == "ACTIVE"
            assert row.certificate_fingerprint == pki.fingerprint_sha256_hex(
                x509.load_pem_x509_certificate(agent.client_cert_pem())
            )
        finally:
            session.close()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_B_two_concurrent_confirmations_at_most_one_agent(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    pairing_id, pairing_code = create_pairing_over_service(
        session_factory, tenant_id=setup["tenant_id"], requested_by_user_id=setup["admin_id"]
    )
    try:
        agent_a = _new_agent(running_broker)
        agent_b = _new_agent(running_broker)
        agent_a.generate_key_and_csr()
        agent_b.generate_key_and_csr()

        start_barrier = threading.Barrier(2)
        results: dict[str, int] = {}

        def _attempt(agent: AgentTestClient, key: str) -> None:
            start_barrier.wait(timeout=5.0)
            response = agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code)
            results[key] = response.status_code

        t1 = threading.Thread(target=_attempt, args=(agent_a, "a"))
        t2 = threading.Thread(target=_attempt, args=(agent_b, "b"))
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)

        assert sorted(results.values()) == [200, 422]
        assert _count_agents(session_factory, setup["tenant_id"]) == 1
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_C_csr_with_invalid_signature_issues_no_certificate(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    pairing_id, pairing_code = create_pairing_over_service(
        session_factory, tenant_id=setup["tenant_id"], requested_by_user_id=setup["admin_id"]
    )
    try:
        agent = _new_agent(running_broker)
        agent.generate_key_and_csr()
        corrupted = bytearray(agent.raw_csr_pem())
        # Corrompe un byte dentro del cuerpo base64 (nunca las lineas
        # BEGIN/END) para invalidar la firma sin romper el parseo PEM.
        for i, byte in enumerate(corrupted):
            if byte not in (0x0A, 0x2D) and 40 < i < len(corrupted) - 40:
                corrupted[i] = (byte + 1) % 256
                break
        agent._csr_pem = bytes(corrupted)

        response = agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code)
        assert response.status_code in (422,)
        assert response.json()["error"] in ("CERTIFICATE_INVALID", "INVALID_MESSAGE")
        assert _count_agents(session_factory, setup["tenant_id"]) == 0
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_D_csr_requesting_extensions_is_rejected(session_factory, running_broker):
    """Seccion 18 del encargo: un CSR que solicita CUALQUIER extension
    (incluida una inofensiva) se rechaza -- nunca se copian extensiones
    solicitadas por el agente al certificado final."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    setup = commit_tenant_user(session_factory)
    pairing_id, pairing_code = create_pairing_over_service(
        session_factory, tenant_id=setup["tenant_id"], requested_by_user_id=setup["admin_id"]
    )
    try:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        csr = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "malicious-agent")]))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )
        agent = _new_agent(running_broker)
        agent._key = key
        agent._csr_pem = csr.public_bytes(serialization.Encoding.PEM)

        response = agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code)
        assert response.status_code == 422
        assert response.json()["error"] == "CERTIFICATE_INVALID"
        assert _count_agents(session_factory, setup["tenant_id"]) == 0
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_E_terminal_pairing_never_issues_a_certificate(session_factory, running_broker):
    from app.services import sol_agent_pairing_service as pairing_service

    setup = commit_tenant_user(session_factory)
    pairing_id, pairing_code = create_pairing_over_service(
        session_factory, tenant_id=setup["tenant_id"], requested_by_user_id=setup["admin_id"]
    )
    session = session_factory()
    try:
        pairing_service.cancel_pairing(session, tenant_id=setup["tenant_id"], pairing_id=pairing_id)
        session.commit()
    finally:
        session.close()

    try:
        agent = _new_agent(running_broker)
        response = agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code)
        assert response.status_code == 422
        assert _count_agents(session_factory, setup["tenant_id"]) == 0
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_F_wrong_pairing_code_issues_no_certificate(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    pairing_id, _pairing_code = create_pairing_over_service(
        session_factory, tenant_id=setup["tenant_id"], requested_by_user_id=setup["admin_id"]
    )
    try:
        agent = _new_agent(running_broker)
        response = agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code="000000")
        assert response.status_code == 422
        assert _count_agents(session_factory, setup["tenant_id"]) == 0
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_G_pairing_csr_request_schema_has_no_private_key_field():
    """Prueba de CONTRATO (seccion 60: "ninguna prueba debe asumir --
    verificar programaticamente que el esquema del broker no tiene un campo
    para la clave privada"). `PairingCsrSubmitRequest` es `extra='forbid'`
    (`protocol.py`) -- intentar colar un campo `private_key`/`client_key`
    hace que la VALIDACION del propio esquema lo rechace."""
    from pydantic import ValidationError

    from app.broker.protocol import PairingCsrSubmitRequest

    field_names = set(PairingCsrSubmitRequest.model_fields.keys())
    assert "private_key" not in field_names
    assert "client_key" not in field_names
    assert "key" not in field_names

    with pytest.raises(ValidationError):
        PairingCsrSubmitRequest.model_validate(
            {
                "protocol_version": 1,
                "pairing_id": str(uuid.uuid4()),
                "pairing_code": "123456",
                "csr_pem": "not-a-real-csr",
                "private_key_pem": "should-never-be-accepted",
            }
        )
