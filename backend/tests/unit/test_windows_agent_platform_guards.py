"""Fase 5, Bloque 5.2, Domain E2 -- confirma que las implementaciones
EXPLICITAS de Windows (`identity.WindowsDpapiIdentityStore`,
`single_instance.WindowsSingleInstanceGuard`) fallan de forma clara y
controlada fuera de Windows -- nunca una abstraccion multiplataforma
falsa (seccion 7 del encargo), y el modulo en si SIGUE SIENDO importable
(los binding ctypes.windll viven solo dentro de las funciones, nunca a
nivel de modulo)."""

from __future__ import annotations

import sys
import uuid

import pytest

from windows_agent.identity import PlatformNotSupportedError as IdentityPlatformError
from windows_agent.identity import WindowsDpapiIdentityStore
from windows_agent.single_instance import PlatformNotSupportedError as MutexPlatformError
from windows_agent.single_instance import WindowsSingleInstanceGuard


@pytest.fixture
def off_windows(monkeypatch):
    """Simula un anfitrion NO Windows de forma HERMETICA: antes estas pruebas dependian del sistema real (en Windows con el agente en vivo corriendo
    fallaban por el mutex ocupado o por una clave PEM invalida, no por el contrato 'fallar cerrado fuera de Windows')."""
    from windows_agent import identity, single_instance

    monkeypatch.setattr(identity, "_is_windows", lambda: False)
    monkeypatch.setattr(single_instance, "_is_windows", lambda: False)
    monkeypatch.setattr(WindowsSingleInstanceGuard, "_is_windows", staticmethod(lambda: False), raising=False)


def test_identity_store_fails_closed_off_windows(tmp_path, off_windows):
    store = WindowsDpapiIdentityStore(tmp_path)
    with pytest.raises(IdentityPlatformError):
        store.save(agent_id=__import__("uuid").uuid4(), certificate_pem=b"x", private_key_pem=b"y")
    with pytest.raises(IdentityPlatformError):
        store.load()
    with pytest.raises(IdentityPlatformError):
        store.build_client_ssl_context(ca_certificate_pem=b"x")
    assert not list(tmp_path.iterdir())  # fallar cerrado no deja archivos de identidad


def test_single_instance_guard_fails_closed_off_windows(off_windows):
    guard = WindowsSingleInstanceGuard()
    with pytest.raises(MutexPlatformError):
        guard.acquire()


@pytest.mark.skipif(sys.platform != "win32", reason="mutex nombrado: solo Windows")
def test_single_instance_guard_is_exclusive_on_windows_with_a_private_mutex_name():
    """Contraparte NATIVA y hermetica: un nombre de mutex propio de la prueba (no el del agente en vivo)."""
    from windows_agent.single_instance import SingleInstanceError

    name = "Local\\luis-ia-contable-test-" + uuid.uuid4().hex
    first, second = WindowsSingleInstanceGuard(name), WindowsSingleInstanceGuard(name)
    first.acquire()
    try:
        with pytest.raises(SingleInstanceError):
            second.acquire()
    finally:
        first.release()
    second.acquire()  # liberado el primero, el segundo puede tomar el mutex
    second.release()
