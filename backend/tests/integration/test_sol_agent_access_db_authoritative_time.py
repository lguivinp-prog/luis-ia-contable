"""Fase 5, Bloque 5.2, Domain C.1, Gate 2 (ver docs/phase5/block5_2/): las
decisiones CRITICAS de expiracion/seguridad usan `clock.db_now(db)`
(`SELECT now()` real de PostgreSQL dentro de la transaccion), nunca
`clock.utcnow()` (reloj inyectable del proceso backend, valido solo para
timestamps no criticos). Estas pruebas demuestran que un `clock.set_clock()`
sesgado -- intentando simular que "todavia no paso el tiempo" -- NO puede
extender artificialmente una sesion/job/pairing cuyo `expires_at` real ya
quedo en el pasado: la decision sigue gobernada por el reloj real de
Postgres, no por el proceso Python."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.core import clock
from app.services import sol_access_session_service as session_service
from app.services import sol_agent_job_service as job_service
from app.services import sol_agent_pairing_service as pairing_service
from tests.integration._sol_agent_access_helpers import (
    make_client,
    make_client_sol_credential,
    make_sol_agent,
)

_REAL_PAST = datetime(2020, 1, 1, tzinfo=UTC)
# El reloj de aplicacion se sesga a un instante TODAVIA MAS ANTIGUO que
# `_REAL_PAST` -- si el codigo comparara contra este reloj sesgado en vez
# del tiempo real de Postgres, `expires_at` (2020) pareceria "en el futuro"
# respecto al reloj mentiroso (2019) y la fila parecería NO vencida.
_SKEWED_APP_CLOCK = lambda: datetime(2019, 1, 1, tzinfo=UTC)  # noqa: E731


@pytest.fixture(autouse=True)
def _reset_clock_after_each_test():
    yield
    clock.reset_clock()


def test_session_expiry_not_overridable_by_skewed_app_clock(db_session, tenant, admin_user):
    client = make_client(db_session, tenant)
    credential = make_client_sol_credential(db_session, tenant, client, admin_user)
    session_row = session_service.create_session(
        db_session,
        tenant_id=tenant.id,
        client_id=client.id,
        credential_id=credential.id,
        requested_by_user_id=admin_user.id,
        idempotency_key=uuid.uuid4(),
    )
    session_row.expires_at = _REAL_PAST
    db_session.flush()

    clock.set_clock(_SKEWED_APP_CLOCK)
    try:
        # Si `expire_session` comparara con `clock.utcnow()` (sesgado a
        # 2019), `expires_at` (2020) parecería futuro -> `SessionNotExpiredError`.
        # Con `clock.db_now(db)` (tiempo real de Postgres, ~2026), la sesion
        # SI se reconoce como vencida.
        expired = session_service.expire_session(db_session, tenant_id=tenant.id, session_id=session_row.id)
    finally:
        clock.reset_clock()
    assert expired.status == "EXPIRED"


def test_job_expiry_not_overridable_by_skewed_app_clock(db_session, tenant, admin_user):
    client = make_client(db_session, tenant)
    credential = make_client_sol_credential(db_session, tenant, client, admin_user)
    session_row = session_service.create_session(
        db_session,
        tenant_id=tenant.id,
        client_id=client.id,
        credential_id=credential.id,
        requested_by_user_id=admin_user.id,
        idempotency_key=uuid.uuid4(),
    )
    agent = make_sol_agent(db_session, tenant)
    result = job_service.create_job(db_session, tenant_id=tenant.id, session_id=session_row.id, agent_id=agent.id)
    result.job.expires_at = _REAL_PAST
    db_session.flush()

    clock.set_clock(_SKEWED_APP_CLOCK)
    try:
        expired = job_service.expire_job(db_session, tenant_id=tenant.id, job_id=result.job.id)
    finally:
        clock.reset_clock()
    assert expired.status == "EXPIRED"


def test_pairing_expiry_not_overridable_by_skewed_app_clock(db_session, tenant, admin_user, pairing_key):
    result = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
    )
    result.pairing.created_at = _REAL_PAST
    result.pairing.expires_at = datetime(2020, 1, 1, 0, 2, tzinfo=UTC)
    db_session.flush()

    clock.set_clock(_SKEWED_APP_CLOCK)
    try:
        expired = pairing_service.expire_pairing(db_session, tenant_id=tenant.id, pairing_id=result.pairing.id)
    finally:
        clock.reset_clock()
    assert expired.status == "EXPIRED"


def test_claim_rejects_really_expired_job_despite_skewed_app_clock(db_session, tenant, admin_user):
    client = make_client(db_session, tenant)
    credential = make_client_sol_credential(db_session, tenant, client, admin_user)
    session_row = session_service.create_session(
        db_session,
        tenant_id=tenant.id,
        client_id=client.id,
        credential_id=credential.id,
        requested_by_user_id=admin_user.id,
        idempotency_key=uuid.uuid4(),
    )
    agent = make_sol_agent(db_session, tenant)
    result = job_service.create_job(db_session, tenant_id=tenant.id, session_id=session_row.id, agent_id=agent.id)
    result.job.expires_at = _REAL_PAST
    db_session.flush()

    clock.set_clock(_SKEWED_APP_CLOCK)
    try:
        with pytest.raises(job_service.JobClaimInvalidError):
            job_service.claim_job(db_session, job_token=result.job_token)
    finally:
        clock.reset_clock()


def test_confirm_pairing_rejects_really_expired_despite_skewed_app_clock(db_session, tenant, admin_user, pairing_key):
    result = pairing_service.create_pairing(
        db_session, tenant_id=tenant.id, requested_by_user_id=admin_user.id, agent_display_name_candidate="A"
    )
    result.pairing.created_at = _REAL_PAST
    result.pairing.expires_at = datetime(2020, 1, 1, 0, 2, tzinfo=UTC)
    db_session.flush()

    clock.set_clock(_SKEWED_APP_CLOCK)
    try:
        with pytest.raises(pairing_service.PairingExpiredError):
            pairing_service.confirm_pairing(
                db_session,
                tenant_id=tenant.id,
                pairing_id=result.pairing.id,
                code=result.pairing_code,
                certificate_fingerprint=f"fp-{uuid.uuid4().hex}",
                actor_user_id=admin_user.id,
            )
    finally:
        clock.reset_clock()


def test_mark_credential_delivered_rejects_really_expired_job_despite_skewed_app_clock(
    db_session, tenant, admin_user
):
    client = make_client(db_session, tenant)
    credential = make_client_sol_credential(db_session, tenant, client, admin_user)
    session_row = session_service.create_session(
        db_session,
        tenant_id=tenant.id,
        client_id=client.id,
        credential_id=credential.id,
        requested_by_user_id=admin_user.id,
        idempotency_key=uuid.uuid4(),
    )
    agent = make_sol_agent(db_session, tenant)
    result = job_service.create_job(db_session, tenant_id=tenant.id, session_id=session_row.id, agent_id=agent.id)
    claimed = job_service.claim_job(db_session, job_token=result.job_token)
    claimed.expires_at = _REAL_PAST
    db_session.flush()

    clock.set_clock(_SKEWED_APP_CLOCK)
    try:
        with pytest.raises(job_service.JobClaimInvalidError):
            job_service.mark_credential_delivered(
                db_session, tenant_id=tenant.id, job_id=claimed.id, expected_version=claimed.version
            )
    finally:
        clock.reset_clock()


@pytest.fixture()
def pairing_key(monkeypatch):
    import base64
    import os

    from app.core.config import get_settings

    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setattr(get_settings(), "pairing_verifier_key", key, raising=False)
    monkeypatch.setattr(get_settings(), "pairing_verification_throttle_seconds", 0.0, raising=False)
    return key
