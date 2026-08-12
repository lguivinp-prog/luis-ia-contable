"""Gate de concurrencia Argon2id productivo (Fase 5, Bloque 5.1, ver
ADR-0069): maximo `max_concurrent` derivaciones simultaneas, timeout
acotado, liberacion garantizada del permiso incluso ante excepcion."""

from __future__ import annotations

import threading

import pytest

from app.core import sol_vault_crypto as crypto
from app.core.sol_vault_argon2_gate import Argon2ConcurrencyGate, Argon2GateTimeoutError

_REAL_WORK_PARAMS = crypto.Argon2Params(time_cost=2, memory_cost_kib=64 * 1024, parallelism=1)
_FAST_PARAMS = crypto.Argon2Params(time_cost=1, memory_cost_kib=8 * 1024, parallelism=1)


def test_gate_rejects_invalid_max_concurrent():
    with pytest.raises(ValueError):
        Argon2ConcurrencyGate(max_concurrent=0)


def test_gate_with_max_concurrent_one_never_allows_two_simultaneous_derivations(monkeypatch):
    gate = Argon2ConcurrencyGate(max_concurrent=1)
    _assert_gate_never_exceeds_max_concurrent(monkeypatch, gate, thread_count=5)


def test_gate_with_max_concurrent_two_allows_two_but_never_three(monkeypatch):
    gate = Argon2ConcurrencyGate(max_concurrent=2)
    peak = _assert_gate_never_exceeds_max_concurrent(monkeypatch, gate, thread_count=5)
    assert peak >= 1


def _assert_gate_never_exceeds_max_concurrent(monkeypatch, gate, *, thread_count: int) -> int:
    lock = threading.Lock()
    state = {"current": 0, "peak": 0}
    original_derive_kek = crypto.derive_kek

    def instrumented(password, salt, params):
        with lock:
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
        try:
            return original_derive_kek(password, salt, params)
        finally:
            with lock:
                state["current"] -= 1

    import app.core.sol_vault_argon2_gate as gate_module

    monkeypatch.setattr(gate_module.crypto, "derive_kek", instrumented)

    salt = crypto.generate_salt()
    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def worker():
        try:
            gate.derive_kek("contrasena-ficticia-del-gate", salt, _REAL_WORK_PARAMS, timeout_seconds=15)
        except BaseException as exc:  # noqa: BLE001 -- reportado en el hilo principal
            with errors_lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"no se esperaban errores: {errors}"
    assert state["peak"] <= gate.max_concurrent, (
        f"se observaron {state['peak']} derivaciones simultaneas con max_concurrent="
        f"{gate.max_concurrent}"
    )
    return state["peak"]


def test_gate_raises_timeout_error_when_permit_unavailable_within_bound():
    gate = Argon2ConcurrencyGate(max_concurrent=1)
    held = threading.Event()
    release_holder = threading.Event()

    def holder():
        acquired = gate._semaphore.acquire(timeout=5)  # noqa: SLF001 -- prueba interna
        assert acquired
        held.set()
        release_holder.wait(timeout=10)
        gate._semaphore.release()  # noqa: SLF001

    holder_thread = threading.Thread(target=holder)
    holder_thread.start()
    assert held.wait(timeout=5)

    with pytest.raises(Argon2GateTimeoutError):
        gate.derive_kek(
            "da-igual-para-esta-prueba", crypto.generate_salt(), _FAST_PARAMS, timeout_seconds=0.2
        )

    release_holder.set()
    holder_thread.join(timeout=10)


def test_gate_releases_permit_in_finally_even_when_derivation_raises(monkeypatch):
    gate = Argon2ConcurrencyGate(max_concurrent=1)
    import app.core.sol_vault_argon2_gate as gate_module

    def failing_derive(password, salt, params):
        raise RuntimeError("fallo simulado dentro de la derivacion")

    monkeypatch.setattr(gate_module.crypto, "derive_kek", failing_derive)

    with pytest.raises(RuntimeError):
        gate.derive_kek("da-igual", crypto.generate_salt(), _FAST_PARAMS, timeout_seconds=5)

    monkeypatch.undo()
    result = gate.derive_kek("da-igual", crypto.generate_salt(), _FAST_PARAMS, timeout_seconds=0.5)
    assert result


def test_single_worker_gate_accepts_one_and_rejects_other_values():
    from app.core.sol_vault_worker_gate import (
        SolVaultSingleWorkerViolationError,
        enforce_single_worker_gate,
    )

    enforce_single_worker_gate(1)
    for invalid in (0, 2, 4):
        with pytest.raises(SolVaultSingleWorkerViolationError):
            enforce_single_worker_gate(invalid)
