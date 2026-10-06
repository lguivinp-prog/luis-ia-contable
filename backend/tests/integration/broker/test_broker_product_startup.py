"""Fase 5, Bloque 5.2, Domain E2 -- prueba del punto de entrada PRODUCTIVO
de arranque del broker (`app/broker/product_startup.py`), no solo del
constructor `BrokerServer` de pruebas (seccion 72 del encargo: "No
test-only constructor as sole proof"). Cubre AI/AJ/AK de la matriz E2."""

from __future__ import annotations

import socket

import pytest

from app.broker import pki
from app.broker.product_startup import BrokerStartupError, start_production_broker, stop_production_broker
from app.core.config import get_settings


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_AI_product_startup_loads_real_ca_binds_loopback_wires_explicit_timeouts(session_factory, tmp_path):
    """AI/AJ/AK: arranque productivo real -- carga una CA YA PROVISIONADA
    (nunca autogenerada), arranca ambos listeners en loopback, y el timeout
    de lectura efectivamente aplicado por `BrokerServer` es exactamente el
    valor de `settings.sol_broker_read_timeout_seconds` pasado
    explicitamente (`BROKER_TIMEOUT_EXPLICITLY_WIRED_FROM_SETTINGS=YES`),
    nunca el default del constructor."""
    ca_dir = tmp_path / "sol_broker_pki"
    ca = pki.generate_ca(common_name="Domain E2 product startup test CA")
    pki.save_ca(ca, ca_dir=ca_dir)

    custom_timeout = 7.25  # deliberadamente distinto del default del constructor (15.0)
    settings = get_settings().model_copy(
        update={
            "sol_broker_ca_dir": str(ca_dir),
            "sol_broker_pairing_port": _free_loopback_port(),
            "sol_broker_operational_port": _free_loopback_port(),
            "sol_broker_read_timeout_seconds": custom_timeout,
            "backend_workers": 1,
        }
    )

    broker = start_production_broker(settings, session_factory)
    try:
        assert broker.host == "127.0.0.1"
        assert broker.read_timeout_seconds == custom_timeout

        # loopback real -- confirmado con dos metodos independientes (mismo
        # rigor ya usado en Spike B seccion 8): conexion TCP real exitosa a
        # 127.0.0.1, y el propio atributo de host congelado.
        with socket.create_connection(("127.0.0.1", broker.pairing_bound_port), timeout=2.0):
            pass
        with socket.create_connection(("127.0.0.1", broker.operational_bound_port), timeout=2.0):
            pass
    finally:
        stop_production_broker(broker)


def test_AI_product_startup_fails_closed_on_missing_ca_never_autogenerates(session_factory, tmp_path):
    """`PRODUCTION_CA_AUTOGENERATION=NO`: un directorio de CA vacio debe
    abortar el arranque con un error accionable -- nunca generar una CA de
    respaldo silenciosamente."""
    empty_ca_dir = tmp_path / "empty_sol_broker_pki"
    settings = get_settings().model_copy(
        update={
            "sol_broker_ca_dir": str(empty_ca_dir),
            "sol_broker_pairing_port": _free_loopback_port(),
            "sol_broker_operational_port": _free_loopback_port(),
            "backend_workers": 1,
        }
    )

    with pytest.raises(BrokerStartupError, match="BROKER_CA_MATERIAL_MISSING"):
        start_production_broker(settings, session_factory)

    # nunca se creo material de CA como efecto secundario del intento fallido
    assert not (empty_ca_dir / "ca.key.pem").exists()
    assert not (empty_ca_dir / "ca.cert.pem").exists()


def test_AI_product_startup_fails_closed_on_invalid_ca_material(session_factory, tmp_path):
    """Material presente pero corrupto -- tambien falla cerrado, nunca
    autogenera un reemplazo."""
    corrupt_ca_dir = tmp_path / "corrupt_sol_broker_pki"
    corrupt_ca_dir.mkdir(parents=True)
    (corrupt_ca_dir / "ca.key.pem").write_bytes(b"not a real PEM key")
    (corrupt_ca_dir / "ca.cert.pem").write_bytes(b"not a real PEM cert")

    settings = get_settings().model_copy(
        update={
            "sol_broker_ca_dir": str(corrupt_ca_dir),
            "sol_broker_pairing_port": _free_loopback_port(),
            "sol_broker_operational_port": _free_loopback_port(),
            "backend_workers": 1,
        }
    )

    with pytest.raises(BrokerStartupError, match="BROKER_CA_MATERIAL_INVALID"):
        start_production_broker(settings, session_factory)


def test_AI_product_startup_enforces_single_worker_gate(session_factory, tmp_path):
    """El arranque productivo reafirma el gate de un unico worker -- nunca
    arranca el broker si `backend_workers != 1`."""
    from app.core.sol_vault_worker_gate import SolVaultSingleWorkerViolationError

    ca_dir = tmp_path / "sol_broker_pki_multiworker"
    ca = pki.generate_ca(common_name="Domain E2 multiworker test CA")
    pki.save_ca(ca, ca_dir=ca_dir)

    settings = get_settings().model_copy(
        update={
            "sol_broker_ca_dir": str(ca_dir),
            "sol_broker_pairing_port": _free_loopback_port(),
            "sol_broker_operational_port": _free_loopback_port(),
            "backend_workers": 2,
        }
    )

    with pytest.raises(SolVaultSingleWorkerViolationError):
        start_production_broker(settings, session_factory)
