"""Fase 5, Bloque 5.2, Domain E2 -- confirma que las implementaciones
EXPLICITAS de Windows (`identity.WindowsDpapiIdentityStore`,
`single_instance.WindowsSingleInstanceGuard`) fallan de forma clara y
controlada fuera de Windows -- nunca una abstraccion multiplataforma
falsa (seccion 7 del encargo), y el modulo en si SIGUE SIENDO importable
(los binding ctypes.windll viven solo dentro de las funciones, nunca a
nivel de modulo)."""

from __future__ import annotations

import pytest

from windows_agent.identity import PlatformNotSupportedError as IdentityPlatformError
from windows_agent.identity import WindowsDpapiIdentityStore
from windows_agent.single_instance import PlatformNotSupportedError as MutexPlatformError
from windows_agent.single_instance import WindowsSingleInstanceGuard


def test_identity_store_fails_closed_off_windows(tmp_path):
    store = WindowsDpapiIdentityStore(tmp_path)
    with pytest.raises(IdentityPlatformError):
        store.save(agent_id=__import__("uuid").uuid4(), certificate_pem=b"x", private_key_pem=b"y")
    with pytest.raises(IdentityPlatformError):
        store.load()
    with pytest.raises(IdentityPlatformError):
        store.build_client_ssl_context(ca_certificate_pem=b"x")


def test_single_instance_guard_fails_closed_off_windows():
    guard = WindowsSingleInstanceGuard()
    with pytest.raises(MutexPlatformError):
        guard.acquire()
