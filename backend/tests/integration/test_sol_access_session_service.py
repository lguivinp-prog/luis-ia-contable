"""Ciclo de vida de la sesion SOL supervisada (Fase 5, Bloque 5.2, Domain C
-- ver docs/phase5/block5_2/BLOCK5_2_STATE_MACHINE.md)."""

from __future__ import annotations

import uuid

import pytest

from app.services import sol_access_session_service as session_service
from tests.integration._sol_agent_access_helpers import make_client, make_client_sol_credential


@pytest.fixture()
def setup(db_session, tenant, admin_user):
    client = make_client(db_session, tenant)
    credential = make_client_sol_credential(db_session, tenant, client, admin_user)
    return {"client": client, "credential": credential}


def test_create_session_initial_status_is_created(db_session, tenant, admin_user, setup):
    session_row = session_service.create_session(
        db_session,
        tenant_id=tenant.id,
        client_id=setup["client"].id,
        credential_id=setup["credential"].id,
        requested_by_user_id=admin_user.id,
        idempotency_key=uuid.uuid4(),
    )
    assert session_row.status == "CREATED"
    assert session_row.scope == "AUTHENTICATE_TO_LANDING_ONLY"
    assert session_row.version == 1


def test_create_session_rejects_unsupported_scope(db_session, tenant, admin_user, setup):
    with pytest.raises(session_service.SolAccessSessionServiceError):
        session_service.create_session(
            db_session,
            tenant_id=tenant.id,
            client_id=setup["client"].id,
            credential_id=setup["credential"].id,
            requested_by_user_id=admin_user.id,
            idempotency_key=uuid.uuid4(),
            scope="BUZON",
        )


def test_create_session_client_not_found(db_session, tenant, admin_user, setup):
    with pytest.raises(session_service.ClientNotFoundError):
        session_service.create_session(
            db_session,
            tenant_id=tenant.id,
            client_id=uuid.uuid4(),
            credential_id=setup["credential"].id,
            requested_by_user_id=admin_user.id,
            idempotency_key=uuid.uuid4(),
        )


def test_create_session_credential_not_found(db_session, tenant, admin_user, setup):
    with pytest.raises(session_service.CredentialNotFoundError):
        session_service.create_session(
            db_session,
            tenant_id=tenant.id,
            client_id=setup["client"].id,
            credential_id=uuid.uuid4(),
            requested_by_user_id=admin_user.id,
            idempotency_key=uuid.uuid4(),
        )


def test_create_session_same_key_same_payload_returns_same_session(db_session, tenant, admin_user, setup):
    key = uuid.uuid4()
    first = session_service.create_session(
        db_session,
        tenant_id=tenant.id,
        client_id=setup["client"].id,
        credential_id=setup["credential"].id,
        requested_by_user_id=admin_user.id,
        idempotency_key=key,
    )
    second = session_service.create_session(
        db_session,
        tenant_id=tenant.id,
        client_id=setup["client"].id,
        credential_id=setup["credential"].id,
        requested_by_user_id=admin_user.id,
        idempotency_key=key,
    )
    assert first.id == second.id


def test_create_session_same_key_different_payload_conflicts(db_session, tenant, admin_user, setup):
    key = uuid.uuid4()
    session_service.create_session(
        db_session,
        tenant_id=tenant.id,
        client_id=setup["client"].id,
        credential_id=setup["credential"].id,
        requested_by_user_id=admin_user.id,
        idempotency_key=key,
    )
    other_client = make_client(db_session, tenant)
    other_credential = make_client_sol_credential(db_session, tenant, other_client, admin_user)
    with pytest.raises(session_service.IdempotencyKeyConflictError):
        session_service.create_session(
            db_session,
            tenant_id=tenant.id,
            client_id=other_client.id,
            credential_id=other_credential.id,
            requested_by_user_id=admin_user.id,
            idempotency_key=key,
        )


def test_only_one_non_terminal_session_per_credential(db_session, tenant, admin_user, setup):
    session_service.create_session(
        db_session,
        tenant_id=tenant.id,
        client_id=setup["client"].id,
        credential_id=setup["credential"].id,
        requested_by_user_id=admin_user.id,
        idempotency_key=uuid.uuid4(),
    )
    with pytest.raises(session_service.ActiveSessionAlreadyExistsError):
        session_service.create_session(
            db_session,
            tenant_id=tenant.id,
            client_id=setup["client"].id,
            credential_id=setup["credential"].id,
            requested_by_user_id=admin_user.id,
            idempotency_key=uuid.uuid4(),
        )


def _create(db_session, tenant, admin_user, setup):
    return session_service.create_session(
        db_session,
        tenant_id=tenant.id,
        client_id=setup["client"].id,
        credential_id=setup["credential"].id,
        requested_by_user_id=admin_user.id,
        idempotency_key=uuid.uuid4(),
    )


def test_full_happy_path_transition_sequence(db_session, tenant, admin_user, setup):
    from tests.integration._sol_agent_access_helpers import make_sol_agent

    session_row = _create(db_session, tenant, admin_user, setup)
    agent = make_sol_agent(db_session, tenant)

    session_row = session_service.assign_agent(
        db_session, tenant_id=tenant.id, session_id=session_row.id, expected_version=session_row.version, agent_id=agent.id
    )
    assert session_row.status == "WAITING_FOR_AGENT"
    assert session_row.assigned_agent_id == agent.id
    assert session_row.agent_assigned_at is not None

    session_row = session_service.start_progress(
        db_session, tenant_id=tenant.id, session_id=session_row.id, expected_version=session_row.version
    )
    assert session_row.status == "IN_PROGRESS"
    assert session_row.started_at is not None

    session_row = session_service.report_waiting_for_user(
        db_session, tenant_id=tenant.id, session_id=session_row.id, expected_version=session_row.version
    )
    assert session_row.status == "WAITING_FOR_USER"
    assert session_row.waiting_for_user_at is not None

    session_row = session_service.confirm_taxpayer(
        db_session, tenant_id=tenant.id, session_id=session_row.id, expected_version=session_row.version
    )
    assert session_row.status == "IN_PROGRESS"

    session_row = session_service.report_succeeded(
        db_session, tenant_id=tenant.id, session_id=session_row.id, expected_version=session_row.version
    )
    assert session_row.status == "SUCCEEDED"
    assert session_row.operation_result == "LANDING_REACHED"
    assert session_row.terminal_reason_code is None
    assert session_row.completed_at is not None


def test_wrong_taxpayer_context_forces_failed_never_partial(db_session, tenant, admin_user, setup):
    session_row = _create(db_session, tenant, admin_user, setup)
    from tests.integration._sol_agent_access_helpers import make_sol_agent

    agent = make_sol_agent(db_session, tenant)
    session_row = session_service.assign_agent(
        db_session, tenant_id=tenant.id, session_id=session_row.id, expected_version=session_row.version, agent_id=agent.id
    )
    session_row = session_service.start_progress(
        db_session, tenant_id=tenant.id, session_id=session_row.id, expected_version=session_row.version
    )
    session_row = session_service.report_waiting_for_user(
        db_session, tenant_id=tenant.id, session_id=session_row.id, expected_version=session_row.version
    )
    session_row = session_service.report_wrong_taxpayer_context(
        db_session, tenant_id=tenant.id, session_id=session_row.id, expected_version=session_row.version
    )
    assert session_row.status == "FAILED"
    assert session_row.terminal_reason_code == "WRONG_TAXPAYER_CONTEXT"


def test_partial_failure_rejects_wrong_taxpayer_context_reason(db_session, tenant, admin_user, setup):
    session_row = _create(db_session, tenant, admin_user, setup)
    from tests.integration._sol_agent_access_helpers import make_sol_agent

    agent = make_sol_agent(db_session, tenant)
    session_row = session_service.assign_agent(
        db_session, tenant_id=tenant.id, session_id=session_row.id, expected_version=session_row.version, agent_id=agent.id
    )
    session_row = session_service.start_progress(
        db_session, tenant_id=tenant.id, session_id=session_row.id, expected_version=session_row.version
    )
    with pytest.raises(session_service.SolAccessSessionServiceError):
        session_service.report_partial_failure(
            db_session,
            tenant_id=tenant.id,
            session_id=session_row.id,
            expected_version=session_row.version,
            terminal_reason_code="WRONG_TAXPAYER_CONTEXT",
        )


def test_partial_failure_sets_landing_reached(db_session, tenant, admin_user, setup):
    session_row = _create(db_session, tenant, admin_user, setup)
    from tests.integration._sol_agent_access_helpers import make_sol_agent

    agent = make_sol_agent(db_session, tenant)
    session_row = session_service.assign_agent(
        db_session, tenant_id=tenant.id, session_id=session_row.id, expected_version=session_row.version, agent_id=agent.id
    )
    session_row = session_service.start_progress(
        db_session, tenant_id=tenant.id, session_id=session_row.id, expected_version=session_row.version
    )
    session_row = session_service.report_partial_failure(
        db_session,
        tenant_id=tenant.id,
        session_id=session_row.id,
        expected_version=session_row.version,
        terminal_reason_code="CLEANUP_FAILED",
    )
    assert session_row.status == "PARTIAL_FAILURE"
    assert session_row.operation_result == "LANDING_REACHED"
    assert session_row.terminal_reason_code == "CLEANUP_FAILED"


def test_terminal_state_is_immutable(db_session, tenant, admin_user, setup):
    session_row = _create(db_session, tenant, admin_user, setup)
    session_service.cancel_session(db_session, tenant_id=tenant.id, session_id=session_row.id)
    assert session_row.status == "CANCELLED"
    with pytest.raises(session_service.SessionTerminalError):
        session_service.report_failed(
            db_session,
            tenant_id=tenant.id,
            session_id=session_row.id,
            expected_version=session_row.version,
            terminal_reason_code="AUTH_FAILED",
        )
    assert session_row.status == "CANCELLED"


def test_invalid_transition_rejected(db_session, tenant, admin_user, setup):
    session_row = _create(db_session, tenant, admin_user, setup)
    with pytest.raises(session_service.InvalidSessionTransitionError):
        session_service.report_succeeded(
            db_session, tenant_id=tenant.id, session_id=session_row.id, expected_version=session_row.version
        )


def test_stale_expected_version_rejected(db_session, tenant, admin_user, setup):
    session_row = _create(db_session, tenant, admin_user, setup)
    from tests.integration._sol_agent_access_helpers import make_sol_agent

    agent = make_sol_agent(db_session, tenant)
    session_service.assign_agent(
        db_session, tenant_id=tenant.id, session_id=session_row.id, expected_version=session_row.version, agent_id=agent.id
    )
    with pytest.raises(session_service.SessionVersionConflictError):
        session_service.start_progress(
            db_session, tenant_id=tenant.id, session_id=session_row.id, expected_version=1  # ya es 2
        )


def test_cancel_session_idempotent(db_session, tenant, admin_user, setup):
    session_row = _create(db_session, tenant, admin_user, setup)
    first = session_service.cancel_session(db_session, tenant_id=tenant.id, session_id=session_row.id)
    assert first.status == "CANCELLED"
    second = session_service.cancel_session(db_session, tenant_id=tenant.id, session_id=session_row.id)
    assert second.status == "CANCELLED"


def test_cancel_session_does_not_rewrite_other_terminal(db_session, tenant, admin_user, setup):
    session_row = _create(db_session, tenant, admin_user, setup)
    from tests.integration._sol_agent_access_helpers import make_sol_agent

    agent = make_sol_agent(db_session, tenant)
    session_row = session_service.assign_agent(
        db_session, tenant_id=tenant.id, session_id=session_row.id, expected_version=session_row.version, agent_id=agent.id
    )
    session_row = session_service.start_progress(
        db_session, tenant_id=tenant.id, session_id=session_row.id, expected_version=session_row.version
    )
    session_row = session_service.report_succeeded(
        db_session, tenant_id=tenant.id, session_id=session_row.id, expected_version=session_row.version
    )
    assert session_row.status == "SUCCEEDED"
    with pytest.raises(session_service.SessionTerminalError):
        session_service.cancel_session(db_session, tenant_id=tenant.id, session_id=session_row.id)
    assert session_row.status == "SUCCEEDED"


def test_expire_session_requires_actual_expiry(db_session, tenant, admin_user, setup):
    session_row = _create(db_session, tenant, admin_user, setup)
    with pytest.raises(session_service.SessionNotExpiredError):
        session_service.expire_session(db_session, tenant_id=tenant.id, session_id=session_row.id)


def test_expire_session_explicit_operation(db_session, tenant, admin_user, setup):
    from datetime import UTC, datetime

    from app.core import clock

    session_row = _create(db_session, tenant, admin_user, setup)
    session_row.expires_at = datetime(2020, 1, 1, tzinfo=UTC)
    db_session.flush()

    expired = session_service.expire_session(db_session, tenant_id=tenant.id, session_id=session_row.id)
    assert expired.status == "EXPIRED"
    assert expired.terminal_reason_code == "SESSION_EXPIRED"

    again = session_service.expire_session(db_session, tenant_id=tenant.id, session_id=session_row.id)
    assert again.status == "EXPIRED"


def test_session_tenant_isolation(db_session, tenant, admin_user, setup):
    from app.models.tenant import Tenant

    other_tenant = Tenant(name="Otro tenant sesion")
    db_session.add(other_tenant)
    db_session.flush()

    session_row = _create(db_session, tenant, admin_user, setup)
    with pytest.raises(session_service.SessionNotFoundError):
        session_service.get_session(db_session, tenant_id=other_tenant.id, session_id=session_row.id)
