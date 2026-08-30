"""Fase 5, Bloque 5.2, Domain D -- seccion 61 del encargo: prueba de
bind-a-loopback via inspeccion real del socket del SO (nunca solo lectura de
configuracion) + rechazo en CONSTRUCTOR de cualquier host distinto de
127.0.0.1/localhost (falla en arranque, nunca en tiempo de peticion)."""

from __future__ import annotations

import socket

import pytest

from app.broker.server import BrokerServer


def test_A_both_listeners_bind_only_to_loopback_verified_via_os_socket(running_broker):
    for port in (running_broker.pairing_bound_port, running_broker.operational_bound_port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(2.0)
            result = probe.connect_ex(("127.0.0.1", port))
            assert result == 0, f"esperado: listener alcanzable en 127.0.0.1:{port}"

    # Verificacion negativa REAL (no solo de configuracion): un intento de
    # conexion a la direccion IP publica de esta maquina en el mismo puerto
    # nunca debe alcanzar el listener -- se prueba indirectamente
    # confirmando que el socket subyacente esta ligado a 127.0.0.1 y no a
    # 0.0.0.0/INADDR_ANY, inspeccionando `getsockname()` del propio listener.
    assert running_broker._pairing_httpd.socket.getsockname()[0] == "127.0.0.1"
    assert running_broker._operational_httpd.socket.getsockname()[0] == "127.0.0.1"


def test_B_constructor_refuses_non_loopback_host(broker_ca, session_factory):
    with pytest.raises(ValueError, match="SOL_BROKER_REFUSES_NON_LOOPBACK_HOST"):
        BrokerServer(
            host="0.0.0.0",
            pairing_port=0,
            operational_port=0,
            ca=broker_ca,
            session_factory=session_factory,
        )


def test_C_constructor_refuses_wildcard_ipv6_host(broker_ca, session_factory):
    with pytest.raises(ValueError, match="SOL_BROKER_REFUSES_NON_LOOPBACK_HOST"):
        BrokerServer(
            host="::",
            pairing_port=0,
            operational_port=0,
            ca=broker_ca,
            session_factory=session_factory,
        )
