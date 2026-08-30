"""Ciclo de vida del job dirigido a un agente (Fase 5, Bloque 5.2, Domain C
-- ver docs/phase5/block5_2/)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.core import clock
from app.services import sol_agent_job_service as job_service
from app.services import sol_access_session_service as session_service
from tests.integration._sol_agent_access_helpers import (
    make_client,
    make_client_sol_credential,
    make_sol_agent,
)


@pytest.fixture()
def setup(db_session, tenant, admin_user):
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
    return {"client": client, "credential": credential, "session": session_row, "agent": agent}


def test_create_job_transitions_session_to_waiting_for_agent(db_session, tenant, setup):
    result = job_service.create_job(
        db_session, tenant_id=tenant.id, session_id=setup["session"].id, agent_id=setup["agent"].id
    )
    assert result.job.status == "PENDING"
    assert len(result.job_token) > 20
    assert setup["session"].status == "WAITING_FOR_AGENT"
    assert setup["session"].assigned_agent_id == setup["agent"].id


def test_job_create_result_repr_redacts_token(db_session, tenant, setup):
    result = job_service.create_job(
        db_session, tenant_id=tenant.id, session_id=setup["session"].id, agent_id=setup["agent"].id
    )
    assert result.job_token not in repr(result)
    assert "REDACTED" in repr(result)


def test_create_job_requires_active_agent(db_session, tenant, setup):
    from app.services import sol_agent_service

    sol_agent_service.revoke_agent(db_session, tenant_id=tenant.id, agent_id=setup["agent"].id, actor_user_id=None)
    with pytest.raises(job_service.AgentNotEligibleError):
        job_service.create_job(db_session, tenant_id=tenant.id, session_id=setup["session"].id, agent_id=setup["agent"].id)


def test_create_job_rejects_second_active_job_for_session(db_session, tenant, setup):
    job_service.create_job(db_session, tenant_id=tenant.id, session_id=setup["session"].id, agent_id=setup["agent"].id)
    other_agent = make_sol_agent(db_session, tenant)
    with pytest.raises(job_service.ActiveJobAlreadyExistsError):
        job_service.create_job(db_session, tenant_id=tenant.id, session_id=setup["session"].id, agent_id=other_agent.id)


def test_create_job_rejects_second_active_job_for_agent(db_session, tenant, admin_user, setup):
    job_service.create_job(db_session, tenant_id=tenant.id, session_id=setup["session"].id, agent_id=setup["agent"].id)

    other_client = make_client(db_session, tenant)
    other_credential = make_client_sol_credential(db_session, tenant, other_client, admin_user)
    other_session = session_service.create_session(
        db_session,
        tenant_id=tenant.id,
        client_id=other_client.id,
        credential_id=other_credential.id,
        requested_by_user_id=admin_user.id,
        idempotency_key=uuid.uuid4(),
    )
    with pytest.raises(job_service.ActiveJobAlreadyExistsError):
        job_service.create_job(db_session, tenant_id=tenant.id, session_id=other_session.id, agent_id=setup["agent"].id)


def test_claim_job_success(db_session, tenant, setup):
    result = job_service.create_job(
        db_session, tenant_id=tenant.id, session_id=setup["session"].id, agent_id=setup["agent"].id
    )
    claimed = job_service.claim_job(db_session, job_token=result.job_token)
    assert claimed.status == "CLAIMED"
    assert claimed.claimed_at is not None


def test_claim_job_replay_rejected(db_session, tenant, setup):
    result = job_service.create_job(
        db_session, tenant_id=tenant.id, session_id=setup["session"].id, agent_id=setup["agent"].id
    )
    job_service.claim_job(db_session, job_token=result.job_token)
    with pytest.raises(job_service.JobClaimInvalidError):
        job_service.claim_job(db_session, job_token=result.job_token)


def test_claim_job_wrong_token_rejected(db_session, tenant, setup):
    job_service.create_job(db_session, tenant_id=tenant.id, session_id=setup["session"].id, agent_id=setup["agent"].id)
    with pytest.raises(job_service.JobClaimInvalidError):
        job_service.claim_job(db_session, job_token="not-a-real-token")


def test_claim_job_rejects_revoked_agent(db_session, tenant, setup):
    from app.services import sol_agent_service

    result = job_service.create_job(
        db_session, tenant_id=tenant.id, session_id=setup["session"].id, agent_id=setup["agent"].id
    )
    sol_agent_service.revoke_agent(db_session, tenant_id=tenant.id, agent_id=setup["agent"].id, actor_user_id=None)
    with pytest.raises(job_service.JobClaimInvalidError):
        job_service.claim_job(db_session, job_token=result.job_token)


def test_complete_job(db_session, tenant, setup):
    result = job_service.create_job(
        db_session, tenant_id=tenant.id, session_id=setup["session"].id, agent_id=setup["agent"].id
    )
    claimed = job_service.claim_job(db_session, job_token=result.job_token)
    completed = job_service.complete_job(
        db_session, tenant_id=tenant.id, job_id=claimed.id, expected_version=claimed.version
    )
    assert completed.status == "COMPLETED"
    assert completed.completed_at is not None


def test_complete_job_requires_claimed(db_session, tenant, setup):
    result = job_service.create_job(
        db_session, tenant_id=tenant.id, session_id=setup["session"].id, agent_id=setup["agent"].id
    )
    with pytest.raises(job_service.InvalidJobTransitionError):
        job_service.complete_job(
            db_session, tenant_id=tenant.id, job_id=result.job.id, expected_version=result.job.version
        )


def test_fail_job_from_pending(db_session, tenant, setup):
    result = job_service.create_job(
        db_session, tenant_id=tenant.id, session_id=setup["session"].id, agent_id=setup["agent"].id
    )
    failed = job_service.fail_job(
        db_session, tenant_id=tenant.id, job_id=result.job.id, expected_version=result.job.version
    )
    assert failed.status == "FAILED"


def test_expire_job_requires_actual_expiry(db_session, tenant, setup):
    result = job_service.create_job(
        db_session, tenant_id=tenant.id, session_id=setup["session"].id, agent_id=setup["agent"].id
    )
    with pytest.raises(job_service.JobNotExpiredError):
        job_service.expire_job(db_session, tenant_id=tenant.id, job_id=result.job.id)


def test_expire_job_explicit_operation(db_session, tenant, setup):
    result = job_service.create_job(
        db_session, tenant_id=tenant.id, session_id=setup["session"].id, agent_id=setup["agent"].id
    )
    result.job.expires_at = datetime(2020, 1, 1, tzinfo=UTC)
    db_session.flush()

    expired = job_service.expire_job(db_session, tenant_id=tenant.id, job_id=result.job.id)
    assert expired.status == "EXPIRED"
    again = job_service.expire_job(db_session, tenant_id=tenant.id, job_id=result.job.id)
    assert again.status == "EXPIRED"


def test_mark_credential_delivered_requires_claimed(db_session, tenant, setup):
    result = job_service.create_job(
        db_session, tenant_id=tenant.id, session_id=setup["session"].id, agent_id=setup["agent"].id
    )
    with pytest.raises(job_service.JobClaimInvalidError):
        job_service.mark_credential_delivered(
            db_session, tenant_id=tenant.id, job_id=result.job.id, expected_version=result.job.version
        )


def test_mark_credential_delivered_success_and_blocks_new_job(db_session, tenant, setup):
    result = job_service.create_job(
        db_session, tenant_id=tenant.id, session_id=setup["session"].id, agent_id=setup["agent"].id
    )
    claimed = job_service.claim_job(db_session, job_token=result.job_token)
    delivered = job_service.mark_credential_delivered(
        db_session, tenant_id=tenant.id, job_id=claimed.id, expected_version=claimed.version
    )
    assert delivered.credential_delivered_at is not None

    job_service.complete_job(db_session, tenant_id=tenant.id, job_id=delivered.id, expected_version=delivered.version)

    other_agent = make_sol_agent(db_session, tenant)
    with pytest.raises(job_service.CredentialAlreadyDeliveredError):
        job_service.create_job(db_session, tenant_id=tenant.id, session_id=setup["session"].id, agent_id=other_agent.id)


def test_mark_credential_delivered_emits_gate_consumed_audit_with_no_transport_claim(db_session, tenant, setup):
    """Domain C.1, Gate 4/seccion 9: el evento de auditoria emitido
    significa unicamente 'gate consumido', nunca 'entregado al agente por
    la red' -- ni el `action` ni el payload deben sugerir exito de
    transporte."""
    from sqlalchemy import select

    from app.models.audit_log import AuditAction, AuditLog

    result = job_service.create_job(
        db_session, tenant_id=tenant.id, session_id=setup["session"].id, agent_id=setup["agent"].id
    )
    claimed = job_service.claim_job(db_session, job_token=result.job_token)
    delivered = job_service.mark_credential_delivered(
        db_session, tenant_id=tenant.id, job_id=claimed.id, expected_version=claimed.version
    )

    log_row = db_session.execute(
        select(AuditLog).where(
            AuditLog.tenant_id == tenant.id,
            AuditLog.entity_id == delivered.id,
            AuditLog.action == AuditAction.CREDENTIAL_DELIVERY_GATE_CONSUMED.value,
        )
    ).scalar_one()
    assert log_row.action == "CREDENTIAL_DELIVERY_GATE_CONSUMED"
    payload_text = str(log_row.log_metadata)
    for forbidden in ("delivered_to_agent", "transport", "network", "success"):
        assert forbidden not in payload_text.lower()


def test_mark_credential_delivered_twice_same_job_rejected(db_session, tenant, setup):
    result = job_service.create_job(
        db_session, tenant_id=tenant.id, session_id=setup["session"].id, agent_id=setup["agent"].id
    )
    claimed = job_service.claim_job(db_session, job_token=result.job_token)
    delivered = job_service.mark_credential_delivered(
        db_session, tenant_id=tenant.id, job_id=claimed.id, expected_version=claimed.version
    )
    with pytest.raises(job_service.CredentialAlreadyDeliveredError):
        job_service.mark_credential_delivered(
            db_session, tenant_id=tenant.id, job_id=delivered.id, expected_version=delivered.version
        )


def test_cancel_session_cancels_active_job(db_session, tenant, setup):
    result = job_service.create_job(
        db_session, tenant_id=tenant.id, session_id=setup["session"].id, agent_id=setup["agent"].id
    )
    session_service.cancel_session(db_session, tenant_id=tenant.id, session_id=setup["session"].id)
    db_session.refresh(result.job)
    assert result.job.status == "CANCELLED"
    assert result.job.cancelled_at is not None


def test_job_tenant_isolation(db_session, tenant, setup):
    from app.models.tenant import Tenant

    other_tenant = Tenant(name="Otro tenant job")
    db_session.add(other_tenant)
    db_session.flush()

    result = job_service.create_job(
        db_session, tenant_id=tenant.id, session_id=setup["session"].id, agent_id=setup["agent"].id
    )
    with pytest.raises(job_service.JobNotFoundError):
        job_service.get_job(db_session, tenant_id=other_tenant.id, job_id=result.job.id)
