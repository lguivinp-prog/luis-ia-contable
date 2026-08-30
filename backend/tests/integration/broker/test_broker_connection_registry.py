"""Fase 5, Bloque 5.2, Domain D -- registro de conexiones (seccion 63 del
encargo): conectar/desconectar/reconectar/duplicado/revocado-tras-conectar/
limpieza en apagado, sin fuga de entradas de conexion."""

from __future__ import annotations

from tests.integration.broker.agent_test_client import AgentTestClient
from tests.integration.broker.conftest import cleanup_tenant, commit_tenant_user, create_pairing_over_service


def _paired_agent(session_factory, running_broker, tenant_id, admin_id) -> AgentTestClient:
    from cryptography.hazmat.primitives import serialization

    pairing_id, pairing_code = create_pairing_over_service(
        session_factory, tenant_id=tenant_id, requested_by_user_id=admin_id
    )
    ca_cert_pem = running_broker.ca.certificate.public_bytes(serialization.Encoding.PEM)
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


def test_A_hello_registers_exactly_one_connection_entry(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        # `_paired_agent` ya llama `hello()` una vez (establece la conexion
        # inmediatamente despues del pairing, como haria el agente real).
        assert running_broker.connections.current_cardinality() == 1
        agent.hello()
        assert running_broker.connections.current_cardinality() == 1
        record = running_broker.connections.get(agent_id=agent.agent_id)
        assert record is not None
        assert record.tenant_id == setup["tenant_id"]
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_B_reconnect_replaces_the_previous_handle_never_duplicates(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        agent.hello()
        first_handle = running_broker.connections.get(agent_id=agent.agent_id).connection_handle

        agent.hello()
        second_handle = running_broker.connections.get(agent_id=agent.agent_id).connection_handle

        assert first_handle != second_handle
        assert running_broker.connections.current_cardinality() == 1
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_C_duplicate_simultaneous_client_never_leaves_two_entries(session_factory, running_broker):
    """Politica elegida (seccion 63/informe final): una nueva conexion mTLS
    valida del MISMO agente reemplaza la anterior -- nunca dos entradas
    simultaneas para el mismo `agent_id`."""
    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        for _ in range(5):
            response = agent.hello()
            assert response.status_code == 200
        assert running_broker.connections.current_cardinality() == 1
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_D_revoked_after_connect_is_removed_from_registry_on_next_message(session_factory, running_broker):
    from app.services import sol_agent_service

    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        agent.hello()
        assert running_broker.connections.is_connected(agent_id=agent.agent_id)

        session = session_factory()
        try:
            sol_agent_service.revoke_agent(
                session, tenant_id=setup["tenant_id"], agent_id=agent.agent_id, actor_user_id=setup["admin_id"]
            )
            session.commit()
        finally:
            session.close()

        response = agent.heartbeat()
        assert response.status_code == 401
        assert response.json()["error"] == "AGENT_REVOKED"
        assert not running_broker.connections.is_connected(agent_id=agent.agent_id)
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_E_broker_shutdown_clears_the_registry_without_mutating_durable_state(session_factory, running_broker):
    from app.models.sol_agent_access import SolAgent

    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        agent.hello()
        assert running_broker.connections.current_cardinality() == 1

        running_broker.stop()
        assert running_broker.connections.current_cardinality() == 0

        session = session_factory()
        try:
            row = session.get(SolAgent, agent.agent_id)
            assert row.status == "ACTIVE"  # el apagado del broker nunca muta estado durable
        finally:
            session.close()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_F_unregister_ignores_a_stale_handle_from_an_already_replaced_connection(session_factory, running_broker):
    """Prueba unitaria del registro en si: un `unregister` tardio con un
    handle VIEJO nunca borra la entrada de una conexion mas nueva -- ver el
    docstring de `unregister` (proteccion contra la carrera desconexion-
    vieja vs reemplazo)."""
    import uuid

    registry = running_broker.connections
    agent_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    old_handle = registry.register(agent_id=agent_id, tenant_id=tenant_id, connection_handle="handle-old", protocol_version=1)
    assert old_handle is None

    new_handle_prev = registry.register(
        agent_id=agent_id, tenant_id=tenant_id, connection_handle="handle-new", protocol_version=1
    )
    assert new_handle_prev == "handle-old"

    registry.unregister(agent_id=agent_id, connection_handle="handle-old")
    assert registry.is_connected(agent_id=agent_id)  # la entrada NUEVA sigue viva

    registry.unregister(agent_id=agent_id, connection_handle="handle-new")
    assert not registry.is_connected(agent_id=agent_id)
