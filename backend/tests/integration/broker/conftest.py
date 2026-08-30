"""Fixtures compartidas de las pruebas de Domain D (broker/mTLS/PKI, ver
docs/phase5/block5_2/). Mismo patron de DOS conexiones reales que
`test_sol_agent_access_service_concurrency.py` (Domain C): `session_factory`
crea sesiones SQLAlchemy independientes con commits reales, nunca el
`db_session` de savepoint del resto de la suite -- necesario porque el
propio `BrokerServer` corre en hilos de servidor separados y necesita ver
filas ya comprometidas por otra conexion."""

from __future__ import annotations

import base64
import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import sessionmaker

from app.broker import pki
from app.broker.server import BrokerServer
from app.core.config import get_settings
from app.core.security import hash_password
from app.models.audit_log import AuditLog
from app.models.client import Client
from app.models.client_sol_credential import ClientSolCredential
from app.models.sol_agent_access import SolAccessSession, SolAgent, SolAgentJob, SolAgentPairing
from app.models.sol_vault import SolVault, SolVaultDataKey
from app.models.tenant import Tenant
from app.models.user import User
from app.services.ruc_validator import compute_check_digit


@pytest.fixture()
def session_factory(test_engine):
    return sessionmaker(bind=test_engine, future=True)


@pytest.fixture()
def pairing_key(monkeypatch):
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setattr(get_settings(), "pairing_verifier_key", key, raising=False)
    monkeypatch.setattr(get_settings(), "pairing_verification_throttle_seconds", 0.0, raising=False)
    return key


def _fake_ruc() -> str:
    base8 = f"{uuid.uuid4().int % 10**8:08d}"
    first_ten = "20" + base8
    return first_ten + str(compute_check_digit(first_ten))


def commit_tenant_user(session_factory) -> dict:
    session = session_factory()
    try:
        tenant = Tenant(name="Tenant Domain D broker")
        session.add(tenant)
        session.flush()
        admin = User(
            tenant_id=tenant.id,
            email=f"admin-brokertest-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
            full_name="Admin pruebas broker",
            password_hash=hash_password("TestPassword123!"),
            role="admin",
            is_active=True,
        )
        session.add(admin)
        session.flush()
        session.commit()
        return {"tenant_id": tenant.id, "admin_id": admin.id}
    finally:
        session.close()


def commit_credential(session_factory, tenant_id, admin_id):
    session = session_factory()
    try:
        client = Client(
            tenant_id=tenant_id,
            ruc=_fake_ruc(),
            business_name="Cliente pruebas broker",
            person_type="juridica",
            ruc_status="ACTIVO",
            fiscal_address_condition="HABIDO",
            tax_regime="GENERAL",
            service_status="activo",
        )
        session.add(client)
        session.flush()

        now = datetime.now(UTC)
        vault = session.query(SolVault).filter(SolVault.tenant_id == tenant_id).one_or_none()
        if vault is None:
            vault = SolVault(tenant_id=tenant_id, initialized_at=now, initialized_by=admin_id)
            session.add(vault)
            session.flush()
            data_key = SolVaultDataKey(
                tenant_id=tenant_id,
                vault_id=vault.id,
                data_key_version=1,
                wrapped_data_key=b"dummy-wrapped-data-key-not-real",
                activated_at=now,
                created_by=admin_id,
                created_at=now,
            )
            session.add(data_key)
            session.flush()

        credential = ClientSolCredential(
            tenant_id=tenant_id,
            client_id=client.id,
            vault_id=vault.id,
            credential_kind="SECONDARY",
            sol_username_encrypted=b"dummy-encrypted-username",
            sol_password_encrypted=b"dummy-encrypted-password",
            data_key_version=1,
            authorization_basis="Autorizacion de prueba (Domain D broker)",
            authorized_at=now,
            created_by=admin_id,
        )
        session.add(credential)
        session.flush()
        session.commit()
        return client.id, credential.id
    finally:
        session.close()


def commit_session(session_factory, *, tenant_id, client_id, credential_id, requester_id):
    from datetime import timedelta

    session = session_factory()
    try:
        row = SolAccessSession(
            tenant_id=tenant_id,
            client_id=client_id,
            credential_id=credential_id,
            requested_by_user_id=requester_id,
            scope="AUTHENTICATE_TO_LANDING_ONLY",
            status="CREATED",
            idempotency_key=uuid.uuid4(),
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        session.add(row)
        session.flush()
        session.commit()
        return row.id
    finally:
        session.close()


def create_pairing_over_service(session_factory, *, tenant_id, requested_by_user_id):
    """Crea un pairing PENDING real (Domain C, `create_pairing`) y devuelve
    `(pairing_id, pairing_code)` -- el codigo en claro solo existe en este
    retorno, nunca se persiste (Domain A.3)."""
    from app.services import sol_agent_pairing_service as pairing_service

    session = session_factory()
    try:
        result = pairing_service.create_pairing(
            session,
            tenant_id=tenant_id,
            requested_by_user_id=requested_by_user_id,
            agent_display_name_candidate="Agente de prueba Domain D",
        )
        pairing_id = result.pairing.id
        pairing_code = result.pairing_code
        session.commit()
        return pairing_id, pairing_code
    finally:
        session.close()


def cleanup_tenant(session_factory, tenant_id: uuid.UUID) -> None:
    session = session_factory()
    try:
        session.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
        session.execute(delete(SolAgentJob).where(SolAgentJob.tenant_id == tenant_id))
        session.execute(delete(SolAgentPairing).where(SolAgentPairing.tenant_id == tenant_id))
        session.execute(delete(SolAccessSession).where(SolAccessSession.tenant_id == tenant_id))
        session.execute(delete(SolAgent).where(SolAgent.tenant_id == tenant_id))
        session.execute(delete(ClientSolCredential).where(ClientSolCredential.tenant_id == tenant_id))
        session.execute(delete(Client).where(Client.tenant_id == tenant_id))
        session.execute(delete(SolVaultDataKey).where(SolVaultDataKey.tenant_id == tenant_id))
        session.execute(delete(SolVault).where(SolVault.tenant_id == tenant_id))
        session.execute(delete(User).where(User.tenant_id == tenant_id))
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        session.commit()
    finally:
        session.close()


@pytest.fixture()
def broker_ca():
    return pki.generate_ca(common_name="Domain D test CA")


@pytest.fixture()
def running_broker(session_factory, broker_ca, pairing_key):
    server = BrokerServer(
        host="127.0.0.1",
        pairing_port=0,
        operational_port=0,
        ca=broker_ca,
        session_factory=session_factory,
        handshake_timeout_seconds=5.0,
        idle_timeout_seconds=5.0,
        read_timeout_seconds=5.0,
    )
    server.start()
    try:
        yield server
    finally:
        server.stop()
