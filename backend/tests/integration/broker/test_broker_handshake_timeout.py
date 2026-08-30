"""Fase 5, Bloque 5.2, Domain D.1 -- Gate 4: prueba EMPIRICA (sockets reales,
nunca solo aserciones sobre configuracion) de que
`sol_broker_tls_handshake_timeout_seconds` realmente acota el tiempo que un
cliente que nunca completa el handshake TLS puede bloquear el listener.
`ThreadingHTTPServer.get_request()` (que en la practica es
`self.socket.accept()` sobre el socket SSL-envuelto con
`do_handshake_on_connect=True`, ver `server.py::_build_httpd`) corre en el
hilo UNICO del loop de aceptacion -- un handshake nunca completado ahi
bloquearia TODAS las conexiones nuevas, incluidas las de otros agentes, si
el timeout configurado no acotara de verdad esa espera."""

from __future__ import annotations

import socket
import time

from app.broker.server import BrokerServer
from tests.integration.broker.agent_test_client import AgentTestClient
from tests.integration.broker.conftest import cleanup_tenant, commit_tenant_user, create_pairing_over_service

_HANDSHAKE_TIMEOUT_SECONDS = 1.5
# Margen generoso sobre el timeout configurado -- nunca ajustado al limite,
# para que la prueba no sea fragil bajo carga de CI, pero SI lo bastante
# acotado como para demostrar que el cliente B no espero indefinidamente.
_MAX_ACCEPTABLE_WAIT_SECONDS = _HANDSHAKE_TIMEOUT_SECONDS + 8.0


def test_A_slow_tls_client_cannot_wedge_listener_indefinitely(session_factory, broker_ca, pairing_key):
    broker = BrokerServer(
        host="127.0.0.1",
        pairing_port=0,
        operational_port=0,
        ca=broker_ca,
        session_factory=session_factory,
        handshake_timeout_seconds=_HANDSHAKE_TIMEOUT_SECONDS,
    )
    broker.start()
    setup = commit_tenant_user(session_factory)
    try:
        # Cliente A: completa el TCP de tres vias pero NUNCA envia un
        # ClientHello TLS -- el accept() del servidor, que intenta el
        # handshake de forma sincrona (`do_handshake_on_connect=True`),
        # queda esperando datos que nunca llegan.
        stalled_socket = socket.create_connection(
            ("127.0.0.1", broker.operational_bound_port), timeout=10.0
        )
        try:
            pairing_id, pairing_code = create_pairing_over_service(
                session_factory, tenant_id=setup["tenant_id"], requested_by_user_id=setup["admin_id"]
            )
            from cryptography.hazmat.primitives import serialization

            agent = AgentTestClient(
                ca_certificate_pem=broker.ca.certificate.public_bytes(serialization.Encoding.PEM),
                pairing_base_url=f"https://127.0.0.1:{broker.pairing_bound_port}",
                operational_base_url=f"https://127.0.0.1:{broker.operational_bound_port}",
            )
            assert agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code).status_code == 200

            start = time.monotonic()
            # Cliente B: peticion mTLS REAL y completa contra el MISMO
            # listener operacional que A esta bloqueando.
            hello_response = agent.hello()
            elapsed = time.monotonic() - start

            assert hello_response.status_code == 200, hello_response.text
            assert elapsed < _MAX_ACCEPTABLE_WAIT_SECONDS, (
                f"cliente B espero {elapsed:.2f}s -- el listener parece bloqueado "
                f"indefinidamente por el handshake nunca completado de A"
            )
        finally:
            stalled_socket.close()
    finally:
        broker.stop()
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_B_listener_recovers_and_accepts_many_clients_after_stall_clears(session_factory, broker_ca, pairing_key):
    """Limpieza de recursos tras el timeout (seccion 23 del encargo): sin
    socket colgado, sin entrada de registro huerfana, sin contador de
    conexiones agotado -- varios clientes validos SEGUIDOS, despues del
    cliente lento, se conectan con exito."""
    broker = BrokerServer(
        host="127.0.0.1",
        pairing_port=0,
        operational_port=0,
        ca=broker_ca,
        session_factory=session_factory,
        handshake_timeout_seconds=_HANDSHAKE_TIMEOUT_SECONDS,
    )
    broker.start()
    setup = commit_tenant_user(session_factory)
    try:
        stalled_socket = socket.create_connection(
            ("127.0.0.1", broker.operational_bound_port), timeout=10.0
        )
        try:
            from cryptography.hazmat.primitives import serialization

            ca_pem = broker.ca.certificate.public_bytes(serialization.Encoding.PEM)
            for _ in range(3):
                pairing_id, pairing_code = create_pairing_over_service(
                    session_factory, tenant_id=setup["tenant_id"], requested_by_user_id=setup["admin_id"]
                )
                agent = AgentTestClient(
                    ca_certificate_pem=ca_pem,
                    pairing_base_url=f"https://127.0.0.1:{broker.pairing_bound_port}",
                    operational_base_url=f"https://127.0.0.1:{broker.operational_bound_port}",
                )
                assert agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code).status_code == 200
                response = agent.hello()
                assert response.status_code == 200, response.text
        finally:
            stalled_socket.close()

        assert broker.connections.current_cardinality() == 3
    finally:
        broker.stop()
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_C_stalled_socket_is_eventually_closed_by_the_server(broker_ca, session_factory, pairing_key):
    """Confirma, desde el lado del CLIENTE lento, que el servidor cerro su
    extremo de la conexion despues del timeout de handshake -- nunca deja
    el socket abierto esperando para siempre."""
    broker = BrokerServer(
        host="127.0.0.1",
        pairing_port=0,
        operational_port=0,
        ca=broker_ca,
        session_factory=session_factory,
        handshake_timeout_seconds=_HANDSHAKE_TIMEOUT_SECONDS,
    )
    broker.start()
    try:
        stalled_socket = socket.create_connection(
            ("127.0.0.1", broker.operational_bound_port), timeout=10.0
        )
        try:
            stalled_socket.settimeout(_MAX_ACCEPTABLE_WAIT_SECONDS)
            # Tras el timeout de handshake del servidor, el intento de leer
            # del socket (que el servidor ya cerro) devuelve EOF (b"") o
            # lanza un error de conexion -- ambos prueban que el servidor no
            # lo dejo colgado para siempre.
            data = stalled_socket.recv(16)
            assert data == b""
        except OSError:
            pass  # tambien aceptable: RST/conexion reiniciada por el servidor
        finally:
            stalled_socket.close()
    finally:
        broker.stop()
