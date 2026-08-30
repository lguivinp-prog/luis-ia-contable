"""Throttle process-local de VERIFICACION de pairing (Fase 5, Bloque 5.2,
Domain C.1a/C.1b -- ver docs/phase5/block5_2/). Distinto de:

- test_sol_agent_pairing_rate_limit_service.py (rate limit de CREACION,
  basado en BD);
- test_sol_agent_pairing_service.py::test_fifth_failed_attempt_locks_pairing_permanently
  (lock durable de 5 fallos, basado en BD, autoridad final).

Estas pruebas usan una ventana pequeña real (`time.sleep`) para cruzar el
umbral de throttle -- nunca `clock.set_clock()` (esta capa usa
`time.monotonic()`, no el reloj inyectable de `app.core.clock`, ver Domain
C.1a seccion 16).

Domain C.1b añade las pruebas de seguridad de recursos (A-F mas abajo):
`pairing_id` desconocidos/arbitrarios no deben crear ni acumular estado en
el diccionario process-local, entradas caducadas deben purgarse, y las
entradas de pairings que se vuelven terminales deben evictarse."""

from __future__ import annotations

import base64
import os
import time
import uuid

import pytest

from app.core import sol_pairing_verification_throttle as throttle
from app.core.config import get_settings
from app.services import sol_agent_pairing_service as pairing_service


@pytest.fixture()
def pairing_key(monkeypatch):
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setattr(get_settings(), "pairing_verifier_key", key, raising=False)
    return key


@pytest.fixture(autouse=True)
def _short_throttle_window(monkeypatch):
    monkeypatch.setattr(get_settings(), "pairing_verification_throttle_seconds", 0.15, raising=False)
    throttle.reset_all()
    yield
    throttle.reset_all()


def _wrong_code(real_code: str) -> str:
    return "000000" if real_code != "000000" else "111111"


def test_A_first_wrong_verification_allowed_increments_failed_count(db_session, tenant, admin_user, pairing_key):
    result = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
    )
    with pytest.raises(pairing_service.PairingInvalidCodeError):
        pairing_service.confirm_pairing(
            db_session,
            tenant_id=tenant.id,
            pairing_id=result.pairing.id,
            code=_wrong_code(result.pairing_code),
            issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
            actor_user_id=admin_user.id,
        )
    assert result.pairing.failed_attempt_count == 1


def test_B_immediate_second_verification_throttled_count_unchanged(db_session, tenant, admin_user, pairing_key):
    result = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
    )
    with pytest.raises(pairing_service.PairingInvalidCodeError):
        pairing_service.confirm_pairing(
            db_session,
            tenant_id=tenant.id,
            pairing_id=result.pairing.id,
            code=_wrong_code(result.pairing_code),
            issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
            actor_user_id=admin_user.id,
        )
    assert result.pairing.failed_attempt_count == 1

    with pytest.raises(pairing_service.PairingVerificationRateLimitedError):
        pairing_service.confirm_pairing(
            db_session,
            tenant_id=tenant.id,
            pairing_id=result.pairing.id,
            code=_wrong_code(result.pairing_code),
            issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
            actor_user_id=admin_user.id,
        )
    # El intento throttled NUNCA llego a verify_pairing_code -- el contador
    # durable sigue exactamente en 1 (Domain C.1a, seccion 9).
    assert result.pairing.failed_attempt_count == 1
    assert result.pairing.status == "PENDING"


def test_C_next_verification_allowed_after_interval(db_session, tenant, admin_user, pairing_key):
    result = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
    )
    with pytest.raises(pairing_service.PairingInvalidCodeError):
        pairing_service.confirm_pairing(
            db_session,
            tenant_id=tenant.id,
            pairing_id=result.pairing.id,
            code=_wrong_code(result.pairing_code),
            issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
            actor_user_id=admin_user.id,
        )
    time.sleep(0.2)  # > 0.15s de la ventana configurada
    with pytest.raises(pairing_service.PairingInvalidCodeError):
        pairing_service.confirm_pairing(
            db_session,
            tenant_id=tenant.id,
            pairing_id=result.pairing.id,
            code=_wrong_code(result.pairing_code),
            issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
            actor_user_id=admin_user.id,
        )
    assert result.pairing.failed_attempt_count == 2


def test_D_correct_code_during_throttle_rejected_without_oracle(db_session, tenant, admin_user, pairing_key):
    result = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
    )
    with pytest.raises(pairing_service.PairingInvalidCodeError):
        pairing_service.confirm_pairing(
            db_session,
            tenant_id=tenant.id,
            pairing_id=result.pairing.id,
            code=_wrong_code(result.pairing_code),
            issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
            actor_user_id=admin_user.id,
        )

    # El SEGUNDO intento presenta el codigo CORRECTO, pero llega dentro de
    # la ventana de throttle -- debe rechazarse como RATE_LIMITED, nunca
    # revelar (ni completar) que el codigo era correcto.
    with pytest.raises(pairing_service.PairingVerificationRateLimitedError):
        pairing_service.confirm_pairing(
            db_session,
            tenant_id=tenant.id,
            pairing_id=result.pairing.id,
            code=result.pairing_code,
            issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
            actor_user_id=admin_user.id,
        )
    assert result.pairing.status == "PENDING"
    assert result.pairing.confirmed_agent_id is None
    assert result.pairing.failed_attempt_count == 1


def test_E_correct_code_after_interval_completes_normally(db_session, tenant, admin_user, pairing_key):
    result = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
    )
    with pytest.raises(pairing_service.PairingInvalidCodeError):
        pairing_service.confirm_pairing(
            db_session,
            tenant_id=tenant.id,
            pairing_id=result.pairing.id,
            code=_wrong_code(result.pairing_code),
            issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
            actor_user_id=admin_user.id,
        )
    time.sleep(0.2)
    confirmed = pairing_service.confirm_pairing(
        db_session,
        tenant_id=tenant.id,
        pairing_id=result.pairing.id,
        code=result.pairing_code,
        issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
        actor_user_id=admin_user.id,
    )
    assert confirmed.status == "COMPLETED"
    assert confirmed.confirmed_agent_id is not None


def test_F_limiter_reset_does_not_clear_durable_failed_count(db_session, tenant, admin_user, pairing_key):
    result = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
    )
    with pytest.raises(pairing_service.PairingInvalidCodeError):
        pairing_service.confirm_pairing(
            db_session,
            tenant_id=tenant.id,
            pairing_id=result.pairing.id,
            code=_wrong_code(result.pairing_code),
            issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
            actor_user_id=admin_user.id,
        )
    assert result.pairing.failed_attempt_count == 1

    # "Reinicio del proceso backend" simulado -- limpia el throttle local
    # sin tocar PostgreSQL en absoluto.
    throttle.reset_all()

    # El throttle ya no bloquea (fue reseteado), pero el conteo durable de
    # BD sigue exactamente donde estaba.
    with pytest.raises(pairing_service.PairingInvalidCodeError):
        pairing_service.confirm_pairing(
            db_session,
            tenant_id=tenant.id,
            pairing_id=result.pairing.id,
            code=_wrong_code(result.pairing_code),
            issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
            actor_user_id=admin_user.id,
        )
    assert result.pairing.failed_attempt_count == 2
    assert result.pairing.status == "PENDING"


def test_G_fifth_verified_wrong_attempt_locks_exactly_at_five(db_session, tenant, admin_user, pairing_key):
    result = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
    )
    wrong_code = _wrong_code(result.pairing_code)
    for _ in range(4):
        with pytest.raises(pairing_service.PairingInvalidCodeError):
            pairing_service.confirm_pairing(
                db_session,
                tenant_id=tenant.id,
                pairing_id=result.pairing.id,
                code=wrong_code,
                issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
                actor_user_id=admin_user.id,
            )
        time.sleep(0.2)  # cruza la ventana antes del siguiente intento REAL
    assert result.pairing.failed_attempt_count == 4
    assert result.pairing.status == "PENDING"

    with pytest.raises(pairing_service.PairingLockedError):
        pairing_service.confirm_pairing(
            db_session,
            tenant_id=tenant.id,
            pairing_id=result.pairing.id,
            code=wrong_code,
            issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
            actor_user_id=admin_user.id,
        )
    assert result.pairing.failed_attempt_count == 5
    assert result.pairing.status == "LOCKED"
    assert result.pairing.pairing_code_verifier is None


def test_I_no_secret_material_stored_by_limiter(db_session, tenant, admin_user, pairing_key):
    result = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
    )
    with pytest.raises(pairing_service.PairingInvalidCodeError):
        pairing_service.confirm_pairing(
            db_session,
            tenant_id=tenant.id,
            pairing_id=result.pairing.id,
            code=_wrong_code(result.pairing_code),
            issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
            actor_user_id=admin_user.id,
        )

    # El unico estado interno del throttle es un dict (tenant_id, pairing_id)
    # -> float monotonico -- nunca codigo, verificador, ni PAIRING_VERIFIER_KEY.
    state = throttle._last_allowed_attempt_monotonic
    assert len(state) >= 1
    stored_verifier = result.pairing.pairing_code_verifier
    for key, value in state.items():
        assert isinstance(key, tuple) and len(key) == 2
        assert all(isinstance(part, uuid.UUID) for part in key)
        assert isinstance(value, float)
        assert result.pairing_code not in repr(key)
        if stored_verifier:
            assert stored_verifier not in repr(key)
        assert get_settings().pairing_verifier_key not in repr(key)
    # El repr/str completo del modulo tampoco expone el codigo/clave.
    assert result.pairing_code not in repr(state)
    assert get_settings().pairing_verifier_key not in repr(state)


# ============================================================
# Domain C.1b -- resource-safety: cardinalidad/eviccion del throttle
# ============================================================


def test_A_unknown_pairing_ids_do_not_create_throttle_entries(db_session, tenant, admin_user, pairing_key):
    assert throttle.current_cardinality() == 0
    for _ in range(20):
        with pytest.raises(pairing_service.PairingNotFoundError):
            pairing_service.confirm_pairing(
                db_session,
                tenant_id=tenant.id,
                pairing_id=uuid.uuid4(),  # nunca existio
                code="042017",
                issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
                actor_user_id=admin_user.id,
            )
    assert throttle.current_cardinality() == 0


def test_B_large_sequence_of_unknown_pairing_ids_does_not_grow_cardinality(db_session, tenant, admin_user, pairing_key):
    """Numero razonable (no gigante) de UUIDs aleatorios desconocidos --
    suficiente para demostrar la propiedad sin una prueba lenta."""
    assert throttle.current_cardinality() == 0
    for _ in range(200):
        with pytest.raises(pairing_service.PairingNotFoundError):
            pairing_service.confirm_pairing(
                db_session,
                tenant_id=tenant.id,
                pairing_id=uuid.uuid4(),
                code="042017",
                issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
                actor_user_id=admin_user.id,
            )
    assert throttle.current_cardinality() == 0


def test_C_valid_pending_pairing_creates_exactly_one_throttle_entry(db_session, tenant, admin_user, pairing_key):
    assert throttle.current_cardinality() == 0
    result = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
    )
    assert throttle.current_cardinality() == 0  # create_pairing no toca el throttle de verificacion

    with pytest.raises(pairing_service.PairingInvalidCodeError):
        pairing_service.confirm_pairing(
            db_session,
            tenant_id=tenant.id,
            pairing_id=result.pairing.id,
            code=_wrong_code(result.pairing_code),
            issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
            actor_user_id=admin_user.id,
        )
    assert throttle.current_cardinality() == 1


def test_D_stale_entry_is_pruned_lazily(db_session, tenant, admin_user, pairing_key):
    """Una entrada mucho mas vieja que `_STALE_AFTER_SECONDS` debe purgarse
    en la siguiente llamada a `check_and_register_attempt` de CUALQUIER
    clave -- sin scheduler, purga perezosa (Domain C.1b, seccion 6)."""
    fake_old_key = (tenant.id, uuid.uuid4())
    with throttle._lock:
        # Muy por encima de `_STALE_AFTER_SECONDS` -- simula una entrada
        # abandonada hace mucho tiempo, sin esperar minutos reales.
        throttle._last_allowed_attempt_monotonic[fake_old_key] = (
            time.monotonic() - throttle._STALE_AFTER_SECONDS - 10.0
        )
    assert throttle.current_cardinality() == 1

    result = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
    )
    with pytest.raises(pairing_service.PairingInvalidCodeError):
        pairing_service.confirm_pairing(
            db_session,
            tenant_id=tenant.id,
            pairing_id=result.pairing.id,
            code=_wrong_code(result.pairing_code),
            issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
            actor_user_id=admin_user.id,
        )
    # La entrada vieja fue purgada; solo queda la del pairing valido de
    # arriba -- nunca las dos.
    assert throttle.current_cardinality() == 1
    assert fake_old_key not in throttle._last_allowed_attempt_monotonic


def test_bounded_cardinality_hard_cap_never_exceeded(monkeypatch):
    """Cota dura independiente de la purga por antiguedad -- inserta mas
    entradas de las que caben y confirma que el diccionario nunca supera
    `_MAX_ENTRIES` (Domain C.1b, seccion 7)."""
    monkeypatch.setattr(throttle, "_MAX_ENTRIES", 50, raising=False)
    tenant_id = uuid.uuid4()
    for _ in range(200):
        throttle.check_and_register_attempt(tenant_id=tenant_id, pairing_id=uuid.uuid4())
        assert throttle.current_cardinality() <= 50


def test_E_terminal_pairing_does_not_leave_reusable_throttle_state(db_session, tenant, admin_user, pairing_key):
    """Al volverse terminal por cualquier via (COMPLETED/LOCKED/CANCELLED/
    EXPIRED), la entrada de throttle de ESE pairing se evicta -- no queda
    reutilizable indefinidamente en memoria."""
    # COMPLETED
    result = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
    )
    pairing_service.confirm_pairing(
        db_session,
        tenant_id=tenant.id,
        pairing_id=result.pairing.id,
        code=result.pairing_code,
        issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
        actor_user_id=admin_user.id,
    )
    assert (tenant.id, result.pairing.id) not in throttle._last_allowed_attempt_monotonic

    # CANCELLED
    result2 = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="B"
    )
    with pytest.raises(pairing_service.PairingInvalidCodeError):
        pairing_service.confirm_pairing(
            db_session,
            tenant_id=tenant.id,
            pairing_id=result2.pairing.id,
            code=_wrong_code(result2.pairing_code),
            issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
            actor_user_id=admin_user.id,
        )
    assert throttle.current_cardinality() == 1
    pairing_service.cancel_pairing(db_session, tenant_id=tenant.id, pairing_id=result2.pairing.id)
    assert (tenant.id, result2.pairing.id) not in throttle._last_allowed_attempt_monotonic

    # EXPIRED (explicito)
    result3 = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="C"
    )
    with pytest.raises(pairing_service.PairingInvalidCodeError):
        pairing_service.confirm_pairing(
            db_session,
            tenant_id=tenant.id,
            pairing_id=result3.pairing.id,
            code=_wrong_code(result3.pairing_code),
            issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
            actor_user_id=admin_user.id,
        )
    result3.pairing.created_at = result3.pairing.created_at.replace(year=2020)
    result3.pairing.expires_at = result3.pairing.expires_at.replace(year=2020)
    db_session.flush()
    pairing_service.expire_pairing(db_session, tenant_id=tenant.id, pairing_id=result3.pairing.id)
    assert (tenant.id, result3.pairing.id) not in throttle._last_allowed_attempt_monotonic


def test_F_two_threads_same_key_cannot_both_bypass_throttle(monkeypatch):
    """Prueba de concurrencia PROCESS-LOCAL (no PostgreSQL) -- dos hilos
    reales atacando la MISMA clave simultaneamente: a lo sumo uno debe
    ganar el intento, nunca ambos (Domain C.1b, seccion 9). No sustituye la
    prueba de PostgreSQL del quinto fallo (ver
    test_sol_agent_access_service_concurrency.py::test_D_*)."""
    import threading

    monkeypatch.setattr(get_settings(), "pairing_verification_throttle_seconds", 5.0, raising=False)
    throttle.reset_all()
    tenant_id = uuid.uuid4()
    pairing_id = uuid.uuid4()

    results: dict[str, bool] = {}
    barrier = threading.Barrier(20)

    def worker(name: str) -> None:
        barrier.wait(timeout=5)
        try:
            throttle.check_and_register_attempt(tenant_id=tenant_id, pairing_id=pairing_id)
            results[name] = True
        except throttle.PairingVerificationRateLimitedError:
            results[name] = False

    threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    allowed = [name for name, ok in results.items() if ok]
    assert len(allowed) == 1, f"exactamente un hilo debia pasar el throttle: {results}"
    assert throttle.current_cardinality() == 1
