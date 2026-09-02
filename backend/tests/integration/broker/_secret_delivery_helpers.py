"""Fabricas compartidas para las pruebas de Domain E1-B (entrega segura de
credencial SOL). Construye la cadena completa real: tenant/usuario/sesion de
aplicacion -> boveda SOL inicializada y desbloqueada -> credencial SOL
cifrada real -> sesion de acceso SOL supervisado -> agente emparejado real
(mTLS) -> job despachado y reclamado -- lista para autorizar/entregar.

Usa SIEMPRE `session_factory` (conexiones PostgreSQL reales e
independientes, nunca el `db_session` de savepoint) porque el broker mismo
corre en hilos de servidor con sus propias sesiones -- deben ver filas ya
comprometidas por otra conexion, mismo patron ya productivo en
`tests/integration/broker/conftest.py` (Domain D)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core import sol_vault_crypto as crypto
from app.core.security import hash_password
from app.core.sol_vault_registry import vault_unlock_registry
from app.models.client import Client
from app.models.session import Session as AppSession
from app.models.sol_agent_access import SolAccessSession
from app.models.tenant import Tenant
from app.models.user import User
from app.services import client_sol_credential_service
from app.services import sol_vault_service
from app.services.ruc_validator import compute_check_digit

# Sentinelas de prueba UNICAMENTE -- nunca credenciales reales (seccion 47
# del encargo E1-A, seccion 68 del encargo E1-B).
TEST_SOL_USER = "TEST_SOL_USER"
TEST_SOL_PASSWORD_DO_NOT_USE = "TEST_SOL_PASSWORD_DO_NOT_USE"

FAST_ARGON2_PARAMS = crypto.Argon2Params(time_cost=1, memory_cost_kib=8 * 1024, parallelism=1)
_VAULT_PASSWORD = "contrasena-vault-ficticia-de-prueba-e1b"


def fake_ruc() -> str:
    base8 = f"{uuid.uuid4().int % 10**8:08d}"
    first_ten = "20" + base8
    return first_ten + str(compute_check_digit(first_ten))


@dataclass
class SecretDeliverySetup:
    tenant_id: uuid.UUID
    admin_user_id: uuid.UUID
    app_session_id: uuid.UUID
    client_id: uuid.UUID
    vault_id: uuid.UUID
    credential_id: uuid.UUID
    ruc: str
    session_id: uuid.UUID
    agent_id: uuid.UUID


def commit_tenant_admin_with_app_session(session_factory, *, role: str = "admin") -> dict:
    """Tenant + usuario (admin o contador, ambos con
    `sol_session:create`+`sol_credential:read`) + una fila REAL de
    `sessions` (`app_session_id`) -- `VaultUnlockRegistry.resolve` exige
    que esta fila exista, no revocada, no vencida."""
    session = session_factory()
    try:
        tenant = Tenant(name="Tenant Domain E1-B")
        session.add(tenant)
        session.flush()
        user = User(
            tenant_id=tenant.id,
            email=f"actor-e1b-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
            full_name="Actor pruebas E1-B",
            password_hash=hash_password("TestPassword123!"),
            role=role,
            is_active=True,
        )
        session.add(user)
        session.flush()
        now = datetime.now(UTC)
        app_session = AppSession(
            tenant_id=tenant.id,
            user_id=user.id,
            token_hash=uuid.uuid4().hex,
            csrf_token_hash=uuid.uuid4().hex,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(hours=8),
        )
        session.add(app_session)
        session.flush()
        session.commit()
        return {"tenant_id": tenant.id, "user_id": user.id, "app_session_id": app_session.id}
    finally:
        session.close()


def get_user_row(session_factory, user_id: uuid.UUID) -> User:
    session = session_factory()
    try:
        return session.get(User, user_id)
    finally:
        session.close()


def init_vault_and_confirm_recovery(session_factory, monkeypatch, *, tenant_id, user_id, app_session_id) -> uuid.UUID:
    """Inicializa la boveda REAL (Argon2 rapido en pruebas), la deja
    desbloqueada en el singleton de proceso `vault_unlock_registry` (el
    MISMO objeto que usa `app/broker/server.py`), y confirma el slot de
    recuperacion (precondicion real de `create_credential`)."""
    monkeypatch.setattr(crypto, "production_argon2_params", lambda: FAST_ARGON2_PARAMS)
    session = session_factory()
    try:
        actor = session.get(User, user_id)
        result = sol_vault_service.initialize_vault(
            session,
            vault_unlock_registry,
            tenant_id=tenant_id,
            actor=actor,
            app_session_id=app_session_id,
            personal_vault_password=_VAULT_PASSWORD,
        )
        session.commit()
        vault_id = result.vault.id
        recovery_code = result.recovery_code
    finally:
        session.close()

    session = session_factory()
    try:
        sol_vault_service.confirm_recovery(session, tenant_id=tenant_id, vault_id=vault_id, recovery_code=recovery_code)
        session.commit()
    finally:
        session.close()
    return vault_id


def commit_client(session_factory, tenant_id: uuid.UUID) -> tuple[uuid.UUID, str]:
    session = session_factory()
    try:
        ruc = fake_ruc()
        client = Client(
            tenant_id=tenant_id,
            ruc=ruc,
            business_name="Cliente pruebas Domain E1-B",
            person_type="juridica",
            ruc_status="ACTIVO",
            fiscal_address_condition="HABIDO",
            tax_regime="GENERAL",
            service_status="activo",
        )
        session.add(client)
        session.flush()
        session.commit()
        return client.id, ruc
    finally:
        session.close()


def create_real_credential(
    session_factory, monkeypatch, *, tenant_id, vault_id, client_id, user_id, app_session_id
) -> uuid.UUID:
    """Credencial SOL cifrada REAL (nunca dummy bytes) -- usa
    `client_sol_credential_service.create_credential`, el mismo camino de
    cifrado ya productivo de Bloque 5.1, con los sentinelas de prueba
    `TEST_SOL_USER`/`TEST_SOL_PASSWORD_DO_NOT_USE`."""
    monkeypatch.setattr(crypto, "production_argon2_params", lambda: FAST_ARGON2_PARAMS)
    session = session_factory()
    try:
        actor = session.get(User, user_id)
        credential = client_sol_credential_service.create_credential(
            session,
            vault_unlock_registry,
            tenant_id=tenant_id,
            vault_id=vault_id,
            client_id=client_id,
            actor=actor,
            app_session_id=app_session_id,
            credential_kind="SECONDARY",
            sol_username=TEST_SOL_USER,
            sol_password=TEST_SOL_PASSWORD_DO_NOT_USE,
            authorization_basis="Autorizacion de prueba Domain E1-B",
        )
        session.commit()
        return credential.id
    finally:
        session.close()


def commit_sol_access_session(session_factory, *, tenant_id, client_id, credential_id, requester_id) -> uuid.UUID:
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
