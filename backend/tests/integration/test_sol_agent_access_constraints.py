"""Fase 5, Bloque 5.2 (ver docs/phase5/block5_2/): pruebas de constraint a
nivel de fila -- una sola conexion/transaccion (`db_session`), verifican
que PostgreSQL realmente rechaza cada violacion via `IntegrityError`. Las
carreras de concurrencia real (2 conexiones) viven en
test_sol_agent_access_concurrency.py."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.sol_agent_access import SolAccessSession, SolAgent, SolAgentJob, SolAgentPairing

from tests.integration._sol_agent_access_helpers import (
    make_client,
    make_client_sol_credential,
    make_sol_agent,
    make_sol_agent_job,
    make_sol_agent_pairing,
    make_sol_access_session,
)


def test_certificate_fingerprint_is_globally_unique(db_session, tenant, admin_user):
    other_tenant = _second_tenant(db_session)
    fp = f"shared-fp-{uuid.uuid4().hex}"
    make_sol_agent(db_session, tenant, certificate_fingerprint=fp)

    db_session.add(
        SolAgent(
            tenant_id=other_tenant.id,
            display_name="Agente duplicado",
            certificate_fingerprint=fp,
            paired_at=datetime.now(UTC),
        )
    )
    with pytest.raises(IntegrityError):
        # Mismo fingerprint, tenant DISTINTO -- sigue siendo un choque
        # porque la unique es global, no por tenant.
        db_session.flush()


def test_agent_revoked_coherence_requires_revoked_at(db_session, tenant):
    agent = make_sol_agent(db_session, tenant)
    agent.status = "REVOKED"
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_agent_revoked_at_forbidden_while_active(db_session, tenant):
    agent = SolAgent(
        tenant_id=tenant.id,
        display_name="Agente coherencia",
        certificate_fingerprint=f"fp-{uuid.uuid4().hex}",
        paired_at=datetime.now(UTC),
        revoked_at=datetime.now(UTC),
    )
    db_session.add(agent)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_session_idempotency_key_unique_per_tenant(db_session, tenant, admin_user):
    client = make_client(db_session, tenant)
    credential = make_client_sol_credential(db_session, tenant, client, admin_user)
    key = uuid.uuid4()
    make_sol_access_session(db_session, tenant, client, credential, admin_user, idempotency_key=key)

    client2 = make_client(db_session, tenant)
    credential2 = make_client_sol_credential(db_session, tenant, client2, admin_user)
    with pytest.raises(IntegrityError):
        make_sol_access_session(
            db_session, tenant, client2, credential2, admin_user, idempotency_key=key
        )


def test_session_credential_must_belong_to_same_client_and_tenant(db_session, tenant, admin_user):
    """La FK compuesta (credential_id, tenant_id, client_id) impide que una
    sesion referencie una credencial de OTRO cliente, incluso dentro del
    mismo tenant -- Domain A.2 Gate 1."""
    client_a = make_client(db_session, tenant)
    client_b = make_client(db_session, tenant)
    credential_of_b = make_client_sol_credential(db_session, tenant, client_b, admin_user)

    session_row = SolAccessSession(
        tenant_id=tenant.id,
        client_id=client_a.id,
        credential_id=credential_of_b.id,
        requested_by_user_id=admin_user.id,
        scope="AUTHENTICATE_TO_LANDING_ONLY",
        idempotency_key=uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    db_session.add(session_row)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_session_requester_must_belong_to_same_tenant(db_session, tenant, admin_user):
    other_tenant = _second_tenant(db_session)
    client = make_client(db_session, tenant)
    credential = make_client_sol_credential(db_session, tenant, client, admin_user)

    # requested_by_user_id de OTRO tenant -- la FK compuesta
    # (requested_by_user_id, tenant_id) debe rechazarlo.
    from app.core.security import hash_password
    from app.models.user import User

    foreign_user = User(
        tenant_id=other_tenant.id,
        email=f"foreign-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
        full_name="Usuario de otro tenant",
        password_hash=hash_password("TestPassword123!"),
        role="admin",
        is_active=True,
    )
    db_session.add(foreign_user)
    db_session.flush()

    session_row = SolAccessSession(
        tenant_id=tenant.id,
        client_id=client.id,
        credential_id=credential.id,
        requested_by_user_id=foreign_user.id,
        scope="AUTHENTICATE_TO_LANDING_ONLY",
        idempotency_key=uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    db_session.add(session_row)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_job_session_must_belong_to_same_tenant(db_session, tenant, admin_user):
    other_tenant = _second_tenant(db_session)
    client = make_client(db_session, tenant)
    credential = make_client_sol_credential(db_session, tenant, client, admin_user)
    session_row = make_sol_access_session(db_session, tenant, client, credential, admin_user)
    agent = make_sol_agent(db_session, tenant)

    job = SolAgentJob(
        tenant_id=other_tenant.id,  # tenant distinto al de session_row
        session_id=session_row.id,
        agent_id=agent.id,
        job_token_hash=f"tok-{uuid.uuid4().hex}",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(job)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_job_agent_must_belong_to_same_tenant(db_session, tenant, admin_user):
    other_tenant = _second_tenant(db_session)
    client = make_client(db_session, tenant)
    credential = make_client_sol_credential(db_session, tenant, client, admin_user)
    session_row = make_sol_access_session(db_session, tenant, client, credential, admin_user)
    foreign_agent = make_sol_agent(db_session, other_tenant)

    job = SolAgentJob(
        tenant_id=tenant.id,
        session_id=session_row.id,
        agent_id=foreign_agent.id,  # agente de OTRO tenant
        job_token_hash=f"tok-{uuid.uuid4().hex}",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(job)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_job_token_hash_globally_unique(db_session, tenant, admin_user):
    client = make_client(db_session, tenant)
    credential = make_client_sol_credential(db_session, tenant, client, admin_user)
    session_a = make_sol_access_session(db_session, tenant, client, credential, admin_user)
    session_b = make_sol_access_session(
        db_session, tenant, client, credential, admin_user, status="FAILED"
    )
    agent = make_sol_agent(db_session, tenant)
    shared_token_hash = f"tok-{uuid.uuid4().hex}"
    make_sol_agent_job(db_session, tenant, session_a, agent, job_token_hash=shared_token_hash)

    with pytest.raises(IntegrityError):
        make_sol_agent_job(
            db_session, tenant, session_b, agent, status="CANCELLED", job_token_hash=shared_token_hash
        )


def test_only_one_active_job_per_session(db_session, tenant, admin_user):
    client = make_client(db_session, tenant)
    credential = make_client_sol_credential(db_session, tenant, client, admin_user)
    session_row = make_sol_access_session(db_session, tenant, client, credential, admin_user)
    agent_a = make_sol_agent(db_session, tenant)
    agent_b = make_sol_agent(db_session, tenant)
    make_sol_agent_job(db_session, tenant, session_row, agent_a, status="PENDING")

    with pytest.raises(IntegrityError):
        make_sol_agent_job(db_session, tenant, session_row, agent_b, status="CLAIMED")


def test_only_one_active_job_per_agent(db_session, tenant, admin_user):
    client = make_client(db_session, tenant)
    credential = make_client_sol_credential(db_session, tenant, client, admin_user)
    session_a = make_sol_access_session(db_session, tenant, client, credential, admin_user)
    session_b = make_sol_access_session(
        db_session, tenant, client, credential, admin_user, status="FAILED"
    )
    agent = make_sol_agent(db_session, tenant)
    make_sol_agent_job(db_session, tenant, session_a, agent, status="PENDING")

    with pytest.raises(IntegrityError):
        make_sol_agent_job(db_session, tenant, session_b, agent, status="CLAIMED")


def test_only_one_credential_delivery_per_session(db_session, tenant, admin_user):
    client = make_client(db_session, tenant)
    credential = make_client_sol_credential(db_session, tenant, client, admin_user)
    session_row = make_sol_access_session(db_session, tenant, client, credential, admin_user)
    agent = make_sol_agent(db_session, tenant)
    now = datetime.now(UTC)
    make_sol_agent_job(
        db_session, tenant, session_row, agent, status="COMPLETED", credential_delivered_at=now
    )

    with pytest.raises(IntegrityError):
        # Job HISTORICO distinto de la MISMA sesion, tambien con
        # credential_delivered_at poblado -- debe rechazarse aunque el
        # primer job ya este en estado terminal (Domain A.2 Gate 3/4).
        make_sol_agent_job(
            db_session, tenant, session_row, agent, status="FAILED", credential_delivered_at=now
        )


def test_pairing_verifier_required_while_pending(db_session, tenant, admin_user):
    pairing = SolAgentPairing(
        tenant_id=tenant.id,
        requested_by_user_id=admin_user.id,
        agent_display_name_candidate="Agente candidato",
        pairing_code_verifier=None,
        status="PENDING",
        expires_at=datetime.now(UTC) + timedelta(seconds=120),
    )
    db_session.add(pairing)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_pairing_verifier_forbidden_in_terminal_state(db_session, tenant, admin_user):
    pairing = make_sol_agent_pairing(db_session, tenant, admin_user, status="PENDING")
    pairing.status = "EXPIRED"
    # pairing_code_verifier sigue poblado -- la coherencia debe rechazarlo.
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_pairing_requester_must_belong_to_same_tenant(db_session, tenant):
    other_tenant = _second_tenant(db_session)
    from app.core.security import hash_password
    from app.models.user import User

    foreign_user = User(
        tenant_id=other_tenant.id,
        email=f"foreign-pairing-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
        full_name="Usuario de otro tenant",
        password_hash=hash_password("TestPassword123!"),
        role="admin",
        is_active=True,
    )
    db_session.add(foreign_user)
    db_session.flush()

    pairing = SolAgentPairing(
        tenant_id=tenant.id,
        requested_by_user_id=foreign_user.id,
        agent_display_name_candidate="Agente candidato",
        pairing_code_verifier="verifier-placeholder",
        status="PENDING",
        expires_at=datetime.now(UTC) + timedelta(seconds=120),
    )
    db_session.add(pairing)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_confirmed_agent_must_belong_to_same_tenant(db_session, tenant, admin_user):
    other_tenant = _second_tenant(db_session)
    foreign_agent = make_sol_agent(db_session, other_tenant)

    pairing = SolAgentPairing(
        tenant_id=tenant.id,
        requested_by_user_id=admin_user.id,
        agent_display_name_candidate="Agente candidato",
        pairing_code_verifier=None,
        status="COMPLETED",
        expires_at=datetime.now(UTC) + timedelta(seconds=120),
        confirmed_agent_id=foreign_agent.id,
    )
    db_session.add(pairing)
    with pytest.raises(IntegrityError):
        db_session.flush()


def _second_tenant(db_session):
    from app.models.tenant import Tenant

    t = Tenant(name=f"Tenant secundario {uuid.uuid4().hex[:6]}")
    db_session.add(t)
    db_session.flush()
    return t
