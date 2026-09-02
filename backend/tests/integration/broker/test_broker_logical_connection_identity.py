"""Fase 5, Bloque 5.2, Domain D.1 -- Gate 2: identidad logica de conexion.
La huella mTLS por si sola NUNCA prueba UNA conexion logica viva (dos
procesos podrian compartir el mismo certificado bajo un transporte de
peticion/respuesta discreto) -- `AGENT_HELLO` emite un identificador de alta
entropia, process-local, que las peticiones posteriores deben presentar y
que debe coincidir EXACTAMENTE con la entrada actual del registro."""

from __future__ import annotations

import time

from app.broker.server import BrokerServer
from tests.integration.broker.agent_test_client import CONNECTION_ID_HEADER, AgentTestClient
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
    response = agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code)
    assert response.status_code == 200, response.text
    hello_response = agent.hello()
    assert hello_response.status_code == 200, hello_response.text
    return agent


def _second_instance_with_same_identity(agent: AgentTestClient) -> AgentTestClient:
    """Construye OTRO cliente de prueba que comparte el MISMO certificado y
    clave que `agent` ya emparejo -- simula dos procesos de agente distintos
    presentando la MISMA identidad mTLS (seccion 10 del encargo: "instancia
    B con el MISMO certificado")."""
    clone = AgentTestClient(
        ca_certificate_pem=agent._ca_cert_pem,
        pairing_base_url=agent.pairing_base_url,
        operational_base_url=agent.operational_base_url,
    )
    clone.agent_id = agent.agent_id
    clone._client_cert_pem = agent._client_cert_pem
    clone._client_key_pem = agent._client_key_pem
    # reutiliza los MISMOS archivos ya escritos por `agent` -- Domain
    # E1-B cambio `_mtls_ssl_context()` para leer de una ruta ya escrita
    # una sola vez (nunca reescribirla en cada llamada, hallazgo de una
    # carrera real de escritura de archivo bajo peticiones concurrentes),
    # asi que un clon que comparte identidad debe apuntar a los mismos
    # archivos en vez de dejarlos en `None`.
    clone._client_cert_path = agent._client_cert_path
    clone._client_key_path = agent._client_key_path
    return clone


def test_A_connection_id_is_high_entropy_and_unique_per_hello(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        first_id = agent.connection_id
        assert first_id is not None
        assert len(first_id) >= 32

        second_response = agent.hello()
        assert second_response.status_code == 200
        second_id = agent.connection_id
        assert second_id is not None
        assert second_id != first_id
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_B_connection_id_never_persisted_in_sol_agents_row(session_factory, running_broker):
    from app.models.sol_agent_access import SolAgent

    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        connection_id = agent.connection_id
        assert connection_id

        session = session_factory()
        try:
            row = session.get(SolAgent, agent.agent_id)
            row_values = [str(getattr(row, col.name)) for col in SolAgent.__table__.columns]
            assert connection_id not in row_values
        finally:
            session.close()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_C_new_hello_from_same_certificate_replaces_old_connection(session_factory, running_broker):
    """Prueba literal de la seccion 10 del encargo: instancia A hace HELLO
    (conexion A); instancia B con el MISMO certificado hace HELLO (conexion
    B reemplaza A); un heartbeat posterior de A con su `connection_id` viejo
    debe ser rechazado; B debe ser aceptado. Esta es la prueba real de
    `ONE_LIVE_LOGICAL_CONNECTION_PER_AGENT=YES`."""
    setup = commit_tenant_user(session_factory)
    try:
        instance_a = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        connection_id_a = instance_a.connection_id
        assert connection_id_a

        instance_b = _second_instance_with_same_identity(instance_a)
        hello_b = instance_b.hello()
        assert hello_b.status_code == 200
        connection_id_b = instance_b.connection_id
        assert connection_id_b
        assert connection_id_b != connection_id_a

        # A todavia cree que su connection_id viejo es valido -- no lo es.
        stale_heartbeat = instance_a.heartbeat(headers={CONNECTION_ID_HEADER: connection_id_a})
        assert stale_heartbeat.status_code == 409
        assert stale_heartbeat.json()["error"] == "CONNECTION_REPLACED"

        fresh_heartbeat = instance_b.heartbeat()
        assert fresh_heartbeat.status_code == 200

        assert running_broker.connections.current_cardinality() == 1
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_D_missing_connection_id_header_is_rejected(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        agent.connection_id = None  # simula un agente que nunca guardo el connection_id
        response = agent.heartbeat()
        assert response.status_code == 409
        assert response.json()["error"] == "CONNECTION_NOT_ESTABLISHED"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_E_wrong_connection_id_is_rejected_never_reveals_reason(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    try:
        agent = _paired_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        response = agent.heartbeat(headers={CONNECTION_ID_HEADER: "totally-made-up-connection-id"})
        assert response.status_code == 409
        assert response.json()["error"] == "CONNECTION_REPLACED"
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_F_idle_connection_expires_and_is_evicted(session_factory, broker_ca, pairing_key):
    """`connection_idle_timeout_seconds` deliberadamente corto para una
    prueba rapida y determinista -- reloj MONOTONICO de proceso, nunca
    `clock.db_now`/`clock.utcnow` (esta es una decision de liveness de
    transporte, no de expiracion durable de sesion/job)."""
    broker = BrokerServer(
        host="127.0.0.1",
        pairing_port=0,
        operational_port=0,
        ca=broker_ca,
        session_factory=session_factory,
        idle_timeout_seconds=0.5,
    )
    broker.start()
    setup = commit_tenant_user(session_factory)
    try:
        from cryptography.hazmat.primitives import serialization

        pairing_id, pairing_code = create_pairing_over_service(
            session_factory, tenant_id=setup["tenant_id"], requested_by_user_id=setup["admin_id"]
        )
        agent = AgentTestClient(
            ca_certificate_pem=broker.ca.certificate.public_bytes(serialization.Encoding.PEM),
            pairing_base_url=f"https://127.0.0.1:{broker.pairing_bound_port}",
            operational_base_url=f"https://127.0.0.1:{broker.operational_bound_port}",
        )
        assert agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code).status_code == 200
        assert agent.hello().status_code == 200
        assert broker.connections.current_cardinality() == 1

        time.sleep(1.0)  # supera holgadamente el idle_timeout_seconds=0.5

        response = agent.heartbeat()
        assert response.status_code == 409
        assert response.json()["error"] == "CONNECTION_REPLACED"
        assert broker.connections.current_cardinality() == 0  # purgada, no "zombie"
    finally:
        broker.stop()
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_G_registry_never_grows_for_unknown_or_rejected_hello(session_factory, running_broker, tmp_path):
    """Ninguna conexion sin certificado, con certificado desconocido, o
    revocado llega a crear una entrada -- seccion 11 del encargo."""
    import ssl

    import httpx

    from app.broker import pki

    setup = commit_tenant_user(session_factory)
    try:
        baseline = running_broker.connections.current_cardinality()

        other_ca = pki.generate_ca(common_name="rogue")
        key, csr_pem = pki.build_agent_csr(common_name="rogue-agent")
        cert = pki.sign_agent_csr(ca=other_ca, csr_pem=csr_pem, agent_common_name="rogue-agent")

        from cryptography.hazmat.primitives import serialization

        key_path = tmp_path / "rogue.key.pem"
        cert_path = tmp_path / "rogue.cert.pem"
        key_path.write_bytes(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(cadata=running_broker.ca.certificate.public_bytes(serialization.Encoding.PEM).decode())
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        try:
            ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        except Exception:
            pass

        try:
            with httpx.Client(verify=ctx, timeout=3.0) as client:
                client.post(
                    f"https://127.0.0.1:{running_broker.operational_bound_port}/agent/hello",
                    json={"protocol_version": 1},
                )
        except (httpx.TransportError, ssl.SSLError, OSError):
            pass  # esperado -- CA no confiable, la conexion nunca deberia registrar nada

        assert running_broker.connections.current_cardinality() == baseline
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])
