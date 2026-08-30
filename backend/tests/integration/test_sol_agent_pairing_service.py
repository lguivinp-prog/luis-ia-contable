"""Ciclo de vida del pairing (Fase 5, Bloque 5.2, Domain C -- ver
docs/phase5/block5_2/, Domain A.3)."""

from __future__ import annotations

import base64
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import get_settings
from app.services import sol_agent_pairing_service as pairing_service


@pytest.fixture()
def pairing_key(monkeypatch):
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setattr(get_settings(), "pairing_verifier_key", key, raising=False)
    # Este archivo no prueba el throttle de verificacion en si (ver
    # test_sol_agent_pairing_verification_throttle.py, Domain C.1a) --
    # varios tests llaman confirm_pairing() repetidas veces seguidas contra
    # el MISMO pairing (ej. el quinto fallo), lo que el throttle por
    # defecto (1s) bloquearia sin relacion con lo que se esta probando.
    monkeypatch.setattr(get_settings(), "pairing_verification_throttle_seconds", 0.0, raising=False)
    return key


def test_create_pairing_returns_plaintext_code_once_and_persists_only_verifier(db_session, tenant, admin_user, pairing_key):
    result = pairing_service.create_pairing(
        db_session,
        tenant_id=tenant.id,
        requested_by_user_id=admin_user.id,
        agent_display_name_candidate="Agente de prueba",
    )
    assert len(result.pairing_code) == 6
    assert result.pairing_code.isdigit()
    assert result.pairing.pairing_code_verifier is not None
    assert result.pairing.pairing_code_verifier != result.pairing_code
    assert result.pairing.status == "PENDING"


def test_pairing_create_result_repr_redacts_code(db_session, tenant, admin_user, pairing_key):
    result = pairing_service.create_pairing(
        db_session,
        tenant_id=tenant.id,
        requested_by_user_id=admin_user.id,
        agent_display_name_candidate="Agente de prueba",
    )
    assert result.pairing_code not in repr(result)
    assert "REDACTED" in repr(result)


def test_create_pairing_fails_closed_without_key(db_session, tenant, admin_user, monkeypatch):
    monkeypatch.setattr(get_settings(), "pairing_verifier_key", "", raising=False)
    with pytest.raises(Exception) as exc_info:
        pairing_service.create_pairing(
            db_session,
            tenant_id=tenant.id,
            requested_by_user_id=admin_user.id,
            agent_display_name_candidate="Agente de prueba",
        )
    from app.core.sol_pairing_crypto import PairingVerifierKeyMisconfiguredError

    assert isinstance(exc_info.value, PairingVerifierKeyMisconfiguredError)


def test_create_pairing_rejects_second_pending_for_same_requester(db_session, tenant, admin_user, pairing_key):
    pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
    )
    with pytest.raises(pairing_service.PairingAlreadyPendingError):
        pairing_service.create_pairing(
            db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="B"
        )


def test_confirm_pairing_with_correct_code_creates_active_agent(db_session, tenant, admin_user, pairing_key):
    result = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="Agente X"
    )
    confirmed = pairing_service.confirm_pairing(
        db_session,
        tenant_id=tenant.id,
        pairing_id=result.pairing.id,
        code=result.pairing_code,
        certificate_fingerprint=f"fp-{uuid.uuid4().hex}",
        actor_user_id=admin_user.id,
    )
    assert confirmed.status == "COMPLETED"
    assert confirmed.pairing_code_verifier is None
    assert confirmed.confirmed_agent_id is not None

    from app.models.sol_agent_access import SolAgent

    agent = db_session.get(SolAgent, confirmed.confirmed_agent_id)
    assert agent.status == "ACTIVE"
    assert agent.display_name == "Agente X"
    assert agent.tenant_id == tenant.id


def test_confirm_pairing_with_wrong_code_increments_failed_attempts(db_session, tenant, admin_user, pairing_key):
    result = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
    )
    with pytest.raises(pairing_service.PairingInvalidCodeError):
        pairing_service.confirm_pairing(
            db_session,
            tenant_id=tenant.id,
            pairing_id=result.pairing.id,
            code="000000" if result.pairing_code != "000000" else "111111",
            certificate_fingerprint=f"fp-{uuid.uuid4().hex}",
            actor_user_id=admin_user.id,
        )
    assert result.pairing.failed_attempt_count == 1
    assert result.pairing.status == "PENDING"


def test_fifth_failed_attempt_locks_pairing_permanently(db_session, tenant, admin_user, pairing_key):
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
                certificate_fingerprint=f"fp-{uuid.uuid4().hex}",
                actor_user_id=admin_user.id,
            )
    assert result.pairing.status == "PENDING"
    assert result.pairing.failed_attempt_count == 4

    with pytest.raises(pairing_service.PairingLockedError):
        pairing_service.confirm_pairing(
            db_session,
            tenant_id=tenant.id,
            pairing_id=result.pairing.id,
            code=wrong_code,
            certificate_fingerprint=f"fp-{uuid.uuid4().hex}",
            actor_user_id=admin_user.id,
        )
    assert result.pairing.status == "LOCKED"
    assert result.pairing.failed_attempt_count == 5
    assert result.pairing.pairing_code_verifier is None
    assert result.pairing.locked_at is not None

    # No reset: un sexto intento, incluso con el codigo correcto, sigue LOCKED.
    with pytest.raises(pairing_service.PairingLockedError):
        pairing_service.confirm_pairing(
            db_session,
            tenant_id=tenant.id,
            pairing_id=result.pairing.id,
            code=result.pairing_code,
            certificate_fingerprint=f"fp-{uuid.uuid4().hex}",
            actor_user_id=admin_user.id,
        )


def test_confirm_pairing_rejects_expired(db_session, tenant, admin_user, pairing_key):
    """Domain C.1, Gate 2: la expiracion se decide con `clock.db_now(db)`
    (tiempo real de PostgreSQL), no con `clock.utcnow()` -- por eso esta
    prueba fuerza el vencimiento mutando `expires_at` directamente en la
    fila (mismo patron que test_expire_session_explicit_operation en
    Domain C), en vez de congelar el reloj de aplicacion alrededor de
    `create_pairing`: un `clock.set_clock()` sesgado ya NO puede influir en
    el resultado, que es exactamente la garantia que este gate exige."""
    result = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
    )
    # `expires_at > created_at` es una CheckConstraint real (Domain B) --
    # ambas deben moverse juntas al pasado para poder simular un vencimiento
    # sin violarla.
    result.pairing.created_at = datetime(2020, 1, 1, tzinfo=UTC)
    result.pairing.expires_at = datetime(2020, 1, 1, 0, 2, tzinfo=UTC)
    db_session.flush()

    with pytest.raises(pairing_service.PairingExpiredError):
        pairing_service.confirm_pairing(
            db_session,
            tenant_id=tenant.id,
            pairing_id=result.pairing.id,
            code=result.pairing_code,
            certificate_fingerprint=f"fp-{uuid.uuid4().hex}",
            actor_user_id=admin_user.id,
        )
    assert result.pairing.status == "EXPIRED"
    assert result.pairing.pairing_code_verifier is None


def test_expire_pairing_explicit_operation(db_session, tenant, admin_user, pairing_key):
    result = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
    )
    with pytest.raises(pairing_service.SolAgentPairingServiceError):
        pairing_service.expire_pairing(db_session, tenant_id=tenant.id, pairing_id=result.pairing.id)

    # `expires_at > created_at` es una CheckConstraint real (Domain B) --
    # ambas deben moverse juntas al pasado para poder simular un vencimiento
    # sin violarla.
    result.pairing.created_at = datetime(2020, 1, 1, tzinfo=UTC)
    result.pairing.expires_at = datetime(2020, 1, 1, 0, 2, tzinfo=UTC)
    db_session.flush()
    expired = pairing_service.expire_pairing(db_session, tenant_id=tenant.id, pairing_id=result.pairing.id)
    assert expired.status == "EXPIRED"
    assert expired.pairing_code_verifier is None
    # idempotente
    again = pairing_service.expire_pairing(db_session, tenant_id=tenant.id, pairing_id=result.pairing.id)
    assert again.status == "EXPIRED"


def test_cancel_pairing_idempotent(db_session, tenant, admin_user, pairing_key):
    result = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
    )
    cancelled = pairing_service.cancel_pairing(db_session, tenant_id=tenant.id, pairing_id=result.pairing.id)
    assert cancelled.status == "CANCELLED"
    assert cancelled.pairing_code_verifier is None

    again = pairing_service.cancel_pairing(db_session, tenant_id=tenant.id, pairing_id=result.pairing.id)
    assert again.status == "CANCELLED"


def test_cancel_pairing_does_not_rewrite_other_terminal_states(db_session, tenant, admin_user, pairing_key):
    result = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
    )
    pairing_service.confirm_pairing(
        db_session,
        tenant_id=tenant.id,
        pairing_id=result.pairing.id,
        code=result.pairing_code,
        certificate_fingerprint=f"fp-{uuid.uuid4().hex}",
        actor_user_id=admin_user.id,
    )
    assert result.pairing.status == "COMPLETED"
    with pytest.raises(pairing_service.PairingNotPendingError):
        pairing_service.cancel_pairing(db_session, tenant_id=tenant.id, pairing_id=result.pairing.id)
    assert result.pairing.status == "COMPLETED"


def test_pairing_tenant_isolation(db_session, tenant, admin_user, pairing_key):
    from app.models.tenant import Tenant

    other_tenant = Tenant(name="Otro tenant pairing")
    db_session.add(other_tenant)
    db_session.flush()

    result = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
    )
    with pytest.raises(pairing_service.PairingNotFoundError):
        pairing_service.get_pairing(db_session, tenant_id=other_tenant.id, pairing_id=result.pairing.id)


def test_confirm_pairing_rejects_duplicate_certificate_fingerprint(db_session, tenant, admin_user, pairing_key):
    fingerprint = f"fp-{uuid.uuid4().hex}"
    first = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
    )
    pairing_service.confirm_pairing(
        db_session,
        tenant_id=tenant.id,
        pairing_id=first.pairing.id,
        code=first.pairing_code,
        certificate_fingerprint=fingerprint,
        actor_user_id=admin_user.id,
    )

    from app.models.user import User

    second_user = User(
        tenant_id=tenant.id,
        email=f"second-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
        full_name="Segundo usuario",
        password_hash="x",
        role="admin",
        is_active=True,
    )
    db_session.add(second_user)
    db_session.flush()

    second = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=second_user.id, agent_display_name_candidate="B"
    )
    with pytest.raises(pairing_service.AgentCertificateFingerprintConflictError):
        pairing_service.confirm_pairing(
            db_session,
            tenant_id=tenant.id,
            pairing_id=second.pairing.id,
            code=second.pairing_code,
            certificate_fingerprint=fingerprint,
            actor_user_id=admin_user.id,
        )
