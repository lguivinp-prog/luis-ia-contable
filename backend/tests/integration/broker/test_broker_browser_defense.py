"""Fase 5, Bloque 5.2, Domain D.1 -- Gate 3: defensa loopback contra
trafico tipo-navegador. "Sin CORS" NO prueba por si solo "no invocable
desde un navegador" -- estas pruebas ejercitan peticiones HTTP reales
(nunca automatizacion de navegador) que imitan lo que un navegador
enviaria: `Host` inesperado, `Origin` presente, `Sec-Fetch-Site`, ausencia
del header nativo de protocolo, y `Content-Type` fuera de
`application/json`. Ninguna de estas defensas sustituye mTLS/pairing
(seccion 19-20 del encargo) -- son defensa en profundidad SOBRE el listener
loopback."""

from __future__ import annotations

import httpx
from cryptography.hazmat.primitives import serialization

from tests.integration.broker.agent_test_client import AgentTestClient
from tests.integration.broker.conftest import (
    cleanup_tenant,
    commit_tenant_user,
    create_pairing_over_service,
)


def _paired_agent(session_factory, running_broker, tenant_id, admin_id) -> AgentTestClient:
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


def test_A_unexpected_host_header_is_rejected(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        with agent._operational_client() as client:
            response = client.post(
                f"{agent.operational_base_url}/agent/heartbeat",
                json={"protocol_version": 1},
                headers={**agent._connection_headers(), "Host": "evil.example.com"},
            )
        assert response.status_code == 403
        assert response.json()["error"] == "REQUEST_NOT_ALLOWED"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_A2_dns_rebinding_style_host_is_rejected(session_factory, running_broker):
    """Un `Host` que resuelve a 127.0.0.1 pero cuyo TEXTO no es la forma
    canonica esperada (ataque de estilo DNS-rebinding) tambien se rechaza --
    nunca se confia en el `Host` para determinar identidad, solo se
    compara contra un valor exacto conocido."""
    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        with agent._operational_client() as client:
            response = client.post(
                f"{agent.operational_base_url}/agent/heartbeat",
                json={"protocol_version": 1},
                headers={**agent._connection_headers(), "Host": "rebind.attacker.example"},
            )
        assert response.status_code == 403
        assert response.json()["error"] == "REQUEST_NOT_ALLOWED"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_B_origin_header_present_is_rejected(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        with agent._operational_client() as client:
            response = client.post(
                f"{agent.operational_base_url}/agent/heartbeat",
                json={"protocol_version": 1},
                headers={**agent._connection_headers(), "Origin": "https://attacker.example"},
            )
        assert response.status_code == 403
        assert response.json()["error"] == "REQUEST_NOT_ALLOWED"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_C_sec_fetch_site_cross_site_is_rejected(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        with agent._operational_client() as client:
            response = client.post(
                f"{agent.operational_base_url}/agent/heartbeat",
                json={"protocol_version": 1},
                headers={**agent._connection_headers(), "Sec-Fetch-Site": "cross-site"},
            )
        assert response.status_code == 403
        assert response.json()["error"] == "REQUEST_NOT_ALLOWED"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_D_missing_protocol_header_is_rejected(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        with agent._operational_client() as client:
            response = client.post(
                f"{agent.operational_base_url}/agent/heartbeat",
                json={"protocol_version": 1},
                # Sin `X-Sol-Agent-Protocol` -- exactamente lo que enviaria
                # un `fetch()` de navegador con headers "simples".
            )
        assert response.status_code == 403
        assert response.json()["error"] == "REQUEST_NOT_ALLOWED"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_E_text_plain_content_type_is_rejected(session_factory, running_broker):
    """`text/plain` es un Content-Type "simple" que NO dispara preflight
    CORS -- un formulario/`fetch` de navegador podria intentar abusarlo
    para evitar el preflight. Se rechaza explicitamente."""
    import json as json_module

    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        with agent._operational_client() as client:
            response = client.post(
                f"{agent.operational_base_url}/agent/heartbeat",
                content=json_module.dumps({"protocol_version": 1}).encode("utf-8"),
                headers=agent._connection_headers({"Content-Type": "text/plain"}),
            )
        assert response.status_code == 403
        assert response.json()["error"] == "REQUEST_NOT_ALLOWED"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_E2_form_urlencoded_content_type_is_rejected(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        with agent._operational_client() as client:
            response = client.post(
                f"{agent.operational_base_url}/agent/heartbeat",
                data={"protocol_version": "1"},
                headers=agent._connection_headers(),
            )
        assert response.status_code == 403
        assert response.json()["error"] == "REQUEST_NOT_ALLOWED"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_F_valid_native_style_request_is_accepted(session_factory, running_broker):
    """Camino feliz -- una peticion nativa completa (Host correcto, sin
    Origin/Sec-Fetch-Site, header de protocolo presente, Content-Type
    correcto) pasa Gate 3 y llega a la logica normal de mTLS/conexion."""
    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        response = agent.heartbeat()
        assert response.status_code == 200, response.text
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_G_pairing_bootstrap_listener_also_enforces_browser_defense(session_factory, running_broker):
    """Gate 3 aplica a AMBOS listeners -- el de pairing (`CERT_NONE`) es el
    mas expuesto a un navegador, ya que no requiere certificado de
    cliente."""
    import uuid

    setup = commit_tenant_user(session_factory)
    try:
        pairing_id, pairing_code = create_pairing_over_service(
            session_factory, tenant_id=setup["tenant_id"], requested_by_user_id=setup["admin_id"]
        )
        agent = AgentTestClient(
            ca_certificate_pem=running_broker.ca.certificate.public_bytes(serialization.Encoding.PEM),
            pairing_base_url=f"https://127.0.0.1:{running_broker.pairing_bound_port}",
            operational_base_url=f"https://127.0.0.1:{running_broker.operational_bound_port}",
        )
        agent.generate_key_and_csr()
        with httpx.Client(verify=agent._trust_only_ssl_context(), timeout=5.0) as client:
            response = client.post(
                f"{agent.pairing_base_url}/pairing/csr",
                json={
                    "protocol_version": 1,
                    "pairing_id": str(pairing_id),
                    "pairing_code": pairing_code,
                    "csr_pem": agent.raw_csr_pem().decode("ascii"),
                },
                headers={"Origin": "https://attacker.example", "X-Sol-Agent-Protocol": "1"},
            )
        assert response.status_code == 403
        assert response.json()["error"] == "REQUEST_NOT_ALLOWED"

        # Confirma que el pairing SIGUE PENDING (el intento tipo-navegador
        # nunca llego a tocar la logica de pairing).
        from app.models.sol_agent_access import SolAgentPairing

        verify_session = session_factory()
        try:
            pairing_row = verify_session.get(SolAgentPairing, pairing_id)
            assert pairing_row.status == "PENDING"
        finally:
            verify_session.close()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])
