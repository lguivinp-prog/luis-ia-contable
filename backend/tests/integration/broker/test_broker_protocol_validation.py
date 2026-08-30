"""Fase 5, Bloque 5.2, Domain D -- seccion 7 del encargo: version de
protocolo desconocida falla cerrado, ningun parseo best-effort, payloads
sobredimensionados se rechazan ANTES de trabajo caro, y una ruta desconocida
nunca revela informacion mas alla de `NOT_FOUND`."""

from __future__ import annotations

import ssl

import httpx

from app.broker.protocol import MAX_MESSAGE_BYTES
from tests.integration.broker.agent_test_client import AgentTestClient
from tests.integration.broker.conftest import cleanup_tenant, commit_tenant_user, create_pairing_over_service


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


def test_A_unsupported_protocol_version_fails_closed(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        with agent._operational_client() as client:
            response = client.post(
                f"{agent.operational_base_url}/agent/heartbeat",
                json={"protocol_version": 999},
                headers=agent._connection_headers(),
            )
        assert response.status_code == 400
        assert response.json()["error"] == "PROTOCOL_VERSION_UNSUPPORTED"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_B_unknown_field_in_payload_is_rejected_never_ignored(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        with agent._operational_client() as client:
            response = client.post(
                f"{agent.operational_base_url}/agent/heartbeat",
                json={"protocol_version": 1, "arbitrary_free_field": "should never be accepted"},
                headers=agent._connection_headers(),
            )
        assert response.status_code == 422
        assert response.json()["error"] == "INVALID_MESSAGE"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_C_oversized_payload_is_rejected_before_json_parsing(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        oversized_body = b"{" + b"x" * (MAX_MESSAGE_BYTES + 1) + b"}"
        with agent._operational_client() as client:
            response = client.post(
                f"{agent.operational_base_url}/agent/heartbeat",
                content=oversized_body,
                headers=agent._connection_headers({"Content-Type": "application/json"}),
            )
        assert response.status_code == 413
        assert response.json()["error"] == "PAYLOAD_TOO_LARGE"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_D_unknown_route_returns_generic_not_found(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        with agent._operational_client() as client:
            response = client.get(
                f"{agent.operational_base_url}/agent/definitely-not-a-real-route",
                headers=agent._connection_headers(),
            )
        assert response.status_code == 404
        assert response.json()["error"] == "NOT_FOUND"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_E_malformed_json_body_is_rejected(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        with agent._operational_client() as client:
            response = client.post(
                f"{agent.operational_base_url}/agent/heartbeat",
                content=b"{not-valid-json:::",
                headers=agent._connection_headers({"Content-Type": "application/json"}),
            )
        assert response.status_code == 422
        assert response.json()["error"] == "INVALID_MESSAGE"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])
