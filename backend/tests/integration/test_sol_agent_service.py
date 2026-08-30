"""Ciclo de vida del agente Windows (Fase 5, Bloque 5.2, Domain C)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services import sol_agent_service
from tests.integration._sol_agent_access_helpers import make_sol_agent


def test_revoke_agent_sets_status_and_revoked_at(db_session, tenant, admin_user):
    agent = make_sol_agent(db_session, tenant)
    revoked = sol_agent_service.revoke_agent(
        db_session, tenant_id=tenant.id, agent_id=agent.id, actor_user_id=admin_user.id
    )
    assert revoked.status == "REVOKED"
    assert revoked.revoked_at is not None


def test_revoke_agent_is_idempotent(db_session, tenant, admin_user):
    agent = make_sol_agent(db_session, tenant)
    first = sol_agent_service.revoke_agent(
        db_session, tenant_id=tenant.id, agent_id=agent.id, actor_user_id=admin_user.id
    )
    first_revoked_at = first.revoked_at
    second = sol_agent_service.revoke_agent(
        db_session, tenant_id=tenant.id, agent_id=agent.id, actor_user_id=admin_user.id
    )
    assert second.status == "REVOKED"
    assert second.revoked_at == first_revoked_at


def test_revoke_agent_cross_tenant_not_found(db_session, tenant, admin_user):
    from app.models.tenant import Tenant

    other_tenant = Tenant(name="Otro tenant")
    db_session.add(other_tenant)
    db_session.flush()
    agent = make_sol_agent(db_session, other_tenant)

    with pytest.raises(sol_agent_service.AgentNotFoundError):
        sol_agent_service.revoke_agent(
            db_session, tenant_id=tenant.id, agent_id=agent.id, actor_user_id=admin_user.id
        )


def test_heartbeat_requires_active_agent(db_session, tenant, admin_user):
    agent = make_sol_agent(db_session, tenant)
    sol_agent_service.revoke_agent(db_session, tenant_id=tenant.id, agent_id=agent.id, actor_user_id=admin_user.id)
    with pytest.raises(sol_agent_service.SolAgentServiceError):
        sol_agent_service.record_heartbeat(db_session, tenant_id=tenant.id, agent_id=agent.id)


def test_heartbeat_updates_last_seen_at(db_session, tenant):
    agent = make_sol_agent(db_session, tenant)
    assert agent.last_seen_at is None
    updated = sol_agent_service.record_heartbeat(db_session, tenant_id=tenant.id, agent_id=agent.id)
    assert updated.last_seen_at is not None


def test_is_online_derived_from_last_seen_at_threshold(db_session, tenant):
    agent = make_sol_agent(db_session, tenant)
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    agent.last_seen_at = now - timedelta(seconds=10)
    assert sol_agent_service.is_online(agent, now=now) is True

    agent.last_seen_at = now - timedelta(seconds=3600)
    assert sol_agent_service.is_online(agent, now=now) is False


def test_is_online_false_when_never_seen(db_session, tenant):
    agent = make_sol_agent(db_session, tenant)
    assert sol_agent_service.is_online(agent) is False
