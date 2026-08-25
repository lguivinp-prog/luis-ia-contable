"""Fabricas compartidas para las pruebas de Fase 5, Bloque 5.2 (acceso SOL
supervisado). Construyen filas minimas validas directamente via ORM --
nunca via los servicios reales de la boveda (Bloque 5.1) porque estas
pruebas ejercitan DDL/constraints de Bloque 5.2, no criptografia real: los
bytes cifrados son deliberadamente dummy, nunca datos sensibles reales."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.core.security import hash_session_token
from app.models.client import Client
from app.models.client_sol_credential import ClientSolCredential
from app.models.sol_agent_access import (
    SolAccessSession,
    SolAgent,
    SolAgentJob,
    SolAgentPairing,
)
from app.models.sol_vault import SolVault, SolVaultDataKey
from app.services.ruc_validator import compute_check_digit
from sqlalchemy import select


def fake_ruc() -> str:
    base8 = f"{uuid.uuid4().int % 10**8:08d}"
    first_ten = "20" + base8
    return first_ten + str(compute_check_digit(first_ten))


def make_client(db_session, tenant) -> Client:
    client = Client(
        tenant_id=tenant.id,
        ruc=fake_ruc(),
        business_name="Cliente de prueba (acceso SOL)",
        person_type="juridica",
        ruc_status="ACTIVO",
        fiscal_address_condition="HABIDO",
        tax_regime="GENERAL",
        service_status="activo",
    )
    db_session.add(client)
    db_session.flush()
    return client


def _get_or_create_vault_with_active_data_key(db_session, tenant, admin_user) -> SolVault:
    """`sol_vaults` tiene `UNIQUE(tenant_id)` -- como mucho UNA boveda por
    tenant, real desde Bloque 5.1. Reutiliza la existente si esta prueba
    (o una llamada previa de este helper para el mismo tenant) ya la creo."""
    now = datetime.now(UTC)
    vault = db_session.execute(
        select(SolVault).where(SolVault.tenant_id == tenant.id)
    ).scalar_one_or_none()
    if vault is not None:
        return vault

    vault = SolVault(tenant_id=tenant.id, initialized_at=now, initialized_by=admin_user.id)
    db_session.add(vault)
    db_session.flush()

    data_key = SolVaultDataKey(
        tenant_id=tenant.id,
        vault_id=vault.id,
        data_key_version=1,
        wrapped_data_key=b"dummy-wrapped-data-key-not-real",
        activated_at=now,
        created_by=admin_user.id,
        created_at=now,
    )
    db_session.add(data_key)
    db_session.flush()
    return vault


def make_client_sol_credential(db_session, tenant, client, admin_user) -> ClientSolCredential:
    """Crea una credencial minima y valida a nivel de constraint -- bytes
    cifrados dummy, nunca criptografia real (fuera de alcance de estas
    pruebas de DDL). Reutiliza la (unica) boveda del tenant si ya existe."""
    now = datetime.now(UTC)
    vault = _get_or_create_vault_with_active_data_key(db_session, tenant, admin_user)

    credential = ClientSolCredential(
        tenant_id=tenant.id,
        client_id=client.id,
        vault_id=vault.id,
        credential_kind="SECONDARY",
        sol_username_encrypted=b"dummy-encrypted-username",
        sol_password_encrypted=b"dummy-encrypted-password",
        data_key_version=1,
        authorization_basis="Autorizacion de prueba (Bloque 5.2, DDL)",
        authorized_at=now,
        created_by=admin_user.id,
    )
    db_session.add(credential)
    db_session.flush()
    return credential


def make_sol_agent(db_session, tenant, *, certificate_fingerprint: str | None = None) -> SolAgent:
    agent = SolAgent(
        tenant_id=tenant.id,
        display_name="Agente de prueba",
        certificate_fingerprint=certificate_fingerprint or f"fp-{uuid.uuid4().hex}",
        paired_at=datetime.now(UTC),
    )
    db_session.add(agent)
    db_session.flush()
    return agent


def make_sol_access_session(
    db_session,
    tenant,
    client,
    credential,
    requester,
    *,
    status: str = "CREATED",
    idempotency_key: uuid.UUID | None = None,
    assigned_agent_id=None,
) -> SolAccessSession:
    now = datetime.now(UTC)
    session_row = SolAccessSession(
        tenant_id=tenant.id,
        client_id=client.id,
        credential_id=credential.id,
        requested_by_user_id=requester.id,
        assigned_agent_id=assigned_agent_id,
        scope="AUTHENTICATE_TO_LANDING_ONLY",
        status=status,
        idempotency_key=idempotency_key or uuid.uuid4(),
        expires_at=now + timedelta(minutes=10),
    )
    db_session.add(session_row)
    db_session.flush()
    return session_row


def make_sol_agent_job(
    db_session,
    tenant,
    session_row,
    agent,
    *,
    status: str = "PENDING",
    job_token_hash: str | None = None,
    credential_delivered_at: datetime | None = None,
) -> SolAgentJob:
    job = SolAgentJob(
        tenant_id=tenant.id,
        session_id=session_row.id,
        agent_id=agent.id,
        status=status,
        job_token_hash=job_token_hash or hash_session_token(uuid.uuid4().hex),
        credential_delivered_at=credential_delivered_at,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(job)
    db_session.flush()
    return job


def make_sol_agent_pairing(
    db_session,
    tenant,
    requester,
    *,
    status: str = "PENDING",
    pairing_code_verifier: str | None = "verifier-placeholder-not-real",
) -> SolAgentPairing:
    now = datetime.now(UTC)
    pairing = SolAgentPairing(
        tenant_id=tenant.id,
        requested_by_user_id=requester.id,
        agent_display_name_candidate="Agente candidato de prueba",
        pairing_code_verifier=pairing_code_verifier,
        status=status,
        expires_at=now + timedelta(seconds=120),
    )
    db_session.add(pairing)
    db_session.flush()
    return pairing
