"""Segunda capa de rate limit sobre la CREACION de pairings (Fase 5, Bloque
5.2, Domain C.1, Gate 3 -- ver docs/phase5/block5_2/). Distinta del lock
durable de 5 fallos DENTRO de un pairing ya existente (ver
test_sol_agent_pairing_service.py::test_fifth_failed_attempt_locks_pairing_permanently),
que sigue siendo la autoridad durable y no se ve afectada por esta capa."""

from __future__ import annotations

import base64
import os
import uuid
from datetime import UTC, datetime

import pytest

from app.core.config import get_settings
from app.services import sol_agent_pairing_rate_limit_service as rate_limit_service
from app.services import sol_agent_pairing_service as pairing_service


@pytest.fixture()
def pairing_key(monkeypatch):
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setattr(get_settings(), "pairing_verifier_key", key, raising=False)
    # Este archivo prueba el rate limit de CREACION (BD), no el throttle de
    # VERIFICACION (process-local) -- desactivado para no interferir con
    # las repeticiones de confirm_pairing() de estas pruebas.
    monkeypatch.setattr(get_settings(), "pairing_verification_throttle_seconds", 0.0, raising=False)
    return key


def _cancel(db_session, tenant, pairing_id):
    pairing_service.cancel_pairing(db_session, tenant_id=tenant.id, pairing_id=pairing_id)


def test_burst_of_pairing_creation_attempts_is_rate_limited(db_session, tenant, admin_user, pairing_key):
    max_attempts = get_settings().pairing_creation_max_attempts
    for _ in range(max_attempts):
        result = pairing_service.create_pairing(
            db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
        )
        _cancel(db_session, tenant, result.pairing.id)

    with pytest.raises(pairing_service.PairingCreationRateLimitedError):
        pairing_service.create_pairing(
            db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
        )


def test_rate_limit_scope_is_per_tenant_and_requester_not_pairing_code(db_session, tenant, admin_user, pairing_key):
    """La clave de la segunda capa es (tenant_id, requested_by_user_id) --
    nunca el codigo/verificador (encargo Domain C.1, seccion 7)."""
    max_attempts = get_settings().pairing_creation_max_attempts
    for _ in range(max_attempts):
        result = pairing_service.create_pairing(
            db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
        )
        _cancel(db_session, tenant, result.pairing.id)

    from app.core.security import hash_password
    from app.models.user import User

    other_user = User(
        tenant_id=tenant.id,
        email=f"other-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
        full_name="Otro usuario",
        password_hash=hash_password("TestPassword123!"),
        role="admin",
        is_active=True,
    )
    db_session.add(other_user)
    db_session.flush()

    # Otro usuario del mismo tenant NO esta afectado por el rate limit del
    # primero -- confirma que el scope es (tenant, requester), no solo tenant.
    result = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=other_user.id, agent_display_name_candidate="B"
    )
    assert result.pairing.status == "PENDING"


def test_correct_code_after_permitted_window_works(db_session, tenant, admin_user, pairing_key):
    """Una vez que la ventana de backoff vence, la creacion vuelve a
    funcionar y el pairing resultante se confirma normalmente con el
    codigo correcto."""
    max_attempts = get_settings().pairing_creation_max_attempts
    pairing_ids = []
    for _ in range(max_attempts):
        result = pairing_service.create_pairing(
            db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
        )
        pairing_ids.append(result.pairing.id)
        _cancel(db_session, tenant, result.pairing.id)

    with pytest.raises(pairing_service.PairingCreationRateLimitedError):
        pairing_service.create_pairing(
            db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
        )

    # Simula que la ventana de backoff ya vencio: empuja `created_at` de
    # todos los intentos recientes bien al pasado (sin usar clock.set_clock
    # -- Domain C.1 Gate 2 exige que esta decision se rija por tiempo real
    # de Postgres, asi que se manipula el dato real, no el reloj de app).
    from app.models.sol_agent_access import SolAgentPairing

    long_ago = datetime(2020, 1, 1, tzinfo=UTC)
    for pid in pairing_ids:
        row = db_session.get(SolAgentPairing, pid)
        row.created_at = long_ago
    db_session.flush()

    result = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
    )
    confirmed = pairing_service.confirm_pairing(
        db_session,
        tenant_id=tenant.id,
        pairing_id=result.pairing.id,
        code=result.pairing_code,
        issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
        actor_user_id=admin_user.id,
    )
    assert confirmed.status == "COMPLETED"


def test_successful_pairing_resets_the_creation_counter(db_session, tenant, admin_user, pairing_key):
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
    status = rate_limit_service.get_pairing_creation_lockout_status(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id
    )
    assert status.is_locked is False
    assert status.recent_count == 0


def test_durable_five_failure_lock_unaffected_by_creation_rate_limiter(db_session, tenant, admin_user, pairing_key):
    """El lock durable de 5 fallos DENTRO de un pairing sigue funcionando
    exactamente igual, sin importar cuantos pairings previos haya creado
    el mismo (tenant, requester) -- las dos capas son independientes."""
    result = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
    )
    wrong_code = "000000" if result.pairing_code != "000000" else "111111"
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
    with pytest.raises(pairing_service.PairingLockedError):
        pairing_service.confirm_pairing(
            db_session,
            tenant_id=tenant.id,
            pairing_id=result.pairing.id,
            code=wrong_code,
            issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
            actor_user_id=admin_user.id,
        )
    assert result.pairing.status == "LOCKED"
    assert result.pairing.failed_attempt_count == 5


def test_rate_limiter_state_survives_process_restart_because_it_is_db_backed(db_session, tenant, admin_user, pairing_key):
    """A diferencia de un limitador puramente en memoria de proceso, esta
    capa lee `sol_agent_pairings` (tabla ya persistida) -- reiniciar el
    proceso backend (simulado aqui invocando la funcion de estado sin
    ningun estado en memoria previo) no borra la proteccion, a diferencia
    de lo que ocurriria con un limitador in-process."""
    max_attempts = get_settings().pairing_creation_max_attempts
    for _ in range(max_attempts):
        result = pairing_service.create_pairing(
            db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
        )
        _cancel(db_session, tenant, result.pairing.id)

    # "Reinicio de proceso" simulado: una llamada de estado completamente
    # nueva, sin ningun objeto Python compartido con las llamadas de arriba
    # (equivalente a un proceso backend nuevo) -- el estado se lee integro
    # desde la BD.
    status = rate_limit_service.get_pairing_creation_lockout_status(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id
    )
    assert status.is_locked is True
