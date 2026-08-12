"""Boveda de credenciales SOL (Fase 5, Bloque 5.1, ver ADR-0068 a
ADR-0071): flujos de servicio productivo -- inicializacion, recuperacion,
desbloqueo/bloqueo, invitaciones, rotacion, y credenciales por cliente.
Comportamientos demostrados por el Spike C (rama
`spike/phase5-sol-vault-crypto`), adaptados aqui a los servicios reales."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core import clock as clock_module
from app.core import sol_vault_crypto as crypto
from app.core.security import hash_password
from app.core.sol_vault_registry import ALLOWED_OPERATIONS, VaultUnlockRegistry
from app.models.client import Client
from app.models.client_sol_credential import ClientSolCredential
from app.models.session import Session as AppSession
from app.models.sol_vault import SolVault, SolVaultDataKey, SolVaultInvitation, SolVaultKeySlot
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories import sol_vault_repository as repo
from app.services import client_sol_credential_service
from app.services import sol_vault_service as svc
from app.services.ruc_validator import compute_check_digit

_NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
_FAST_PARAMS = crypto.Argon2Params(time_cost=1, memory_cost_kib=8 * 1024, parallelism=1)


@pytest.fixture(autouse=True)
def _fast_argon2(monkeypatch):
    """Los parametros productivos (~700ms) harian esta suite lentisima --
    se sustituyen por parametros rapidos exclusivamente en las pruebas,
    sin tocar el valor productivo real usado por la aplicacion."""
    monkeypatch.setattr(crypto, "production_argon2_params", lambda: _FAST_PARAMS)


@pytest.fixture()
def clock_state():
    """El servicio llama internamente a `clock.utcnow()` (ADR-0008) -- se
    fija a `_NOW` (avanzable via `clock_state['now'] += timedelta(...)`)
    para que coincida con los timestamps de las filas que esta suite crea
    directamente (sesiones, etc.), y para poder simular el paso del tiempo
    despues de un backoff de intentos fallidos."""
    state = {"now": _NOW}
    clock_module.set_clock(lambda: state["now"])
    yield state
    clock_module.reset_clock()


@pytest.fixture(autouse=True)
def _frozen_clock(clock_state):
    yield


@pytest.fixture()
def registry() -> VaultUnlockRegistry:
    return VaultUnlockRegistry()


def _fake_ruc() -> str:
    base8 = f"{uuid.uuid4().int % 10**8:08d}"
    first_ten = "20" + base8
    return first_ten + str(compute_check_digit(first_ten))


def _make_user(db_session, tenant_id, *, role: str = "contador") -> User:
    user = User(
        tenant_id=tenant_id,
        email=f"user-{uuid.uuid4().hex[:8]}@sol-vault-tests.dev",
        full_name="Usuario de prueba",
        password_hash=hash_password("TestPassword123!"),
        role=role,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _make_session(db_session, tenant_id, user_id) -> AppSession:
    session_row = AppSession(
        tenant_id=tenant_id,
        user_id=user_id,
        token_hash=uuid.uuid4().hex,
        csrf_token_hash=uuid.uuid4().hex,
        created_at=_NOW,
        last_seen_at=_NOW,
        expires_at=_NOW + timedelta(hours=8),
    )
    db_session.add(session_row)
    db_session.flush()
    return session_row


@pytest.fixture()
def actor(db_session):
    tenant = Tenant(name="Tenant SOL vault")
    db_session.add(tenant)
    db_session.flush()
    user = _make_user(db_session, tenant.id, role="admin")
    session_row = _make_session(db_session, tenant.id, user.id)
    return {"tenant_id": tenant.id, "user": user, "app_session_id": session_row.id}


@pytest.fixture()
def make_client(db_session):
    def _make(tenant_id) -> Client:
        client = Client(
            tenant_id=tenant_id,
            ruc=_fake_ruc(),
            business_name=f"Cliente ficticio {uuid.uuid4().hex[:6]}",
            person_type="juridica",
            ruc_status="ACTIVO",
            fiscal_address_condition="HABIDO",
            tax_regime="GENERAL",
            service_status="activo",
        )
        db_session.add(client)
        db_session.flush()
        return client

    return _make


def _init(db_session, registry, actor, password="contrasena-inicial-ficticia-de-prueba"):
    return svc.initialize_vault(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
        personal_vault_password=password,
    )


# --- inicializacion y recuperacion ---


def test_initialize_creates_vault_with_user_slot_recovery_and_data_key(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    assert result.vault.status == "ACTIVE"
    assert result.user_slot.kind == "USER"
    assert result.recovery_slot.kind == "RECOVERY"
    assert result.recovery_slot.recovery_confirmed_at is None
    assert result.data_key.data_key_version == 1
    assert len(result.recovery_code) >= 32
    assert registry.get_entry(result.unlock_handle) is not None


def test_initialize_twice_fails(db_session, registry, actor):
    _init(db_session, registry, actor)
    with pytest.raises(svc.VaultAlreadyInitializedError):
        _init(db_session, registry, actor)


def test_confirm_recovery_with_correct_and_incorrect_code(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    with pytest.raises(svc.BadVaultPasswordError):
        svc.confirm_recovery(
            db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code="codigo-malo"
        )
    svc.confirm_recovery(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        recovery_code=result.recovery_code,
    )
    slot = db_session.get(SolVaultKeySlot, result.recovery_slot.id)
    assert slot.recovery_confirmed_at is not None


def test_replace_recovery_then_confirm_revokes_the_old_one(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    svc.confirm_recovery(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code=result.recovery_code
    )
    new_code = svc.replace_recovery(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
        expected_version=1,
    )
    assert new_code != result.recovery_code
    svc.confirm_recovery(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code=new_code
    )
    old_slot = db_session.get(SolVaultKeySlot, result.recovery_slot.id)
    assert old_slot.status == "REVOKED"
    with pytest.raises(svc.BadVaultPasswordError):
        svc.confirm_recovery(
            db_session,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            recovery_code=result.recovery_code,
        )


def test_recover_transitions_recovery_required_to_active_and_issues_new_slot(
    db_session, registry, actor, clock_state
):
    result = _init(db_session, registry, actor)
    svc.confirm_recovery(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code=result.recovery_code
    )
    svc.revoke_slot(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        actor=actor["user"],
        slot_id=result.user_slot.id,
        expected_version=1,
    )
    vault = db_session.get(SolVault, result.vault.id)
    assert vault.status == "RECOVERY_REQUIRED"

    svc.recover(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
        recovery_code=result.recovery_code,
        new_personal_vault_password="contrasena-post-recovery-ficticia",
    )
    vault = db_session.get(SolVault, result.vault.id)
    assert vault.status == "ACTIVE"

    with pytest.raises(svc.BadVaultPasswordError):
        svc.unlock(
            db_session,
            registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            actor=actor["user"],
            app_session_id=actor["app_session_id"],
            personal_vault_password="contrasena-inicial-ficticia-de-prueba",
        )
    clock_state["now"] += timedelta(seconds=5)  # supera el backoff del intento fallido anterior
    svc.unlock(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
        personal_vault_password="contrasena-post-recovery-ficticia",
    )


def test_recover_preserves_vault_root_key_identity(db_session, registry, actor, clock_state):
    """Corrective closure (post v0.9.0-alpha.1 rejection): `recover()` NUNCA
    genera una `vault_root_key` nueva -- desenvuelve la existente desde el
    slot RECOVERY confirmado y la re-envuelve para el nuevo slot USER. Se
    prueba comparando una huella SHA-256 de los bytes crudos de la clave
    antes y despues de recover -- la huella se usa exclusivamente para esta
    comparacion en memoria, nunca se imprime ni se persiste."""
    result = _init(db_session, registry, actor)
    svc.confirm_recovery(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code=result.recovery_code
    )

    entry_before = registry.get_entry(result.unlock_handle)
    fingerprint_before = hashlib.sha256(bytes(entry_before.vault_root_key)).hexdigest()

    svc.revoke_slot(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        actor=actor["user"],
        slot_id=result.user_slot.id,
        expected_version=1,
    )
    vault = db_session.get(SolVault, result.vault.id)
    assert vault.status == "RECOVERY_REQUIRED"

    svc.recover(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
        recovery_code=result.recovery_code,
        new_personal_vault_password="contrasena-post-recovery-identidad-ficticia",
    )

    new_handle = registry.get_handle(
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        user_id=actor["user"].id,
        app_session_id=actor["app_session_id"],
    )
    entry_after = registry.get_entry(new_handle)
    fingerprint_after = hashlib.sha256(bytes(entry_after.vault_root_key)).hexdigest()

    assert fingerprint_after == fingerprint_before
    assert new_handle != result.unlock_handle  # el handle en si es nuevo, la raiz no


def test_last_user_slot_cannot_be_revoked_without_confirmed_recovery(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    with pytest.raises(svc.LastUserSlotError):
        svc.revoke_slot(
            db_session,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            actor=actor["user"],
            slot_id=result.user_slot.id,
            expected_version=1,
        )


# --- unlock/lock/change-password ---


def test_unlock_wrong_password_applies_backoff(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    with pytest.raises(svc.BadVaultPasswordError):
        svc.unlock(
            db_session,
            registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            actor=actor["user"],
            app_session_id=actor["app_session_id"],
            personal_vault_password="contrasena-incorrecta",
        )
    slot = db_session.get(SolVaultKeySlot, result.user_slot.id)
    assert slot.failed_attempts == 1
    assert slot.backoff_until is not None


def test_lock_purges_registry_entry(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    svc.lock(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
    )
    assert registry.get_entry(result.unlock_handle) is None


def test_change_password_then_old_password_fails_new_succeeds(db_session, registry, actor, clock_state):
    result = _init(db_session, registry, actor)
    svc.change_password(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
        new_personal_vault_password="contrasena-nueva-ficticia",
    )
    svc.lock(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
    )
    with pytest.raises(svc.BadVaultPasswordError):
        svc.unlock(
            db_session,
            registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            actor=actor["user"],
            app_session_id=actor["app_session_id"],
            personal_vault_password="contrasena-inicial-ficticia-de-prueba",
        )
    clock_state["now"] += timedelta(seconds=5)  # supera el backoff del intento fallido anterior
    svc.unlock(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
        personal_vault_password="contrasena-nueva-ficticia",
    )


# --- invitaciones ---


def test_invitation_full_flow_creates_second_user_slot(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    svc.confirm_recovery(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code=result.recovery_code
    )
    target = _make_user(db_session, actor["tenant_id"])
    invitation = svc.create_invitation(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        invited_by=actor["user"],
        target_user_id=target.id,
    )
    svc.invitation_association_registry.associate(invitation.id, result.unlock_handle)
    new_slot = svc.accept_invitation(
        db_session,
        registry,
        svc.invitation_association_registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        invitation_id=invitation.id,
        actor=target,
        new_personal_vault_password="contrasena-del-invitado-ficticia",
    )
    assert new_slot.user_id == target.id

    target_session = _make_session(db_session, actor["tenant_id"], target.id)
    handle = svc.invitation_association_registry.get(invitation.id)
    assert handle is None  # se limpia tras aceptar

    target_registry = VaultUnlockRegistry()
    svc.unlock(
        db_session,
        target_registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        actor=target,
        app_session_id=target_session.id,
        personal_vault_password="contrasena-del-invitado-ficticia",
    )


def test_accept_invitation_fails_if_inviter_association_lost(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    svc.confirm_recovery(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code=result.recovery_code
    )
    target = _make_user(db_session, actor["tenant_id"])
    invitation = svc.create_invitation(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        invited_by=actor["user"],
        target_user_id=target.id,
    )
    # nunca se asocio el handle del invitador -- simula que nunca estuvo desbloqueado
    with pytest.raises(svc.InvitationInviterNotUnlockedError):
        svc.accept_invitation(
            db_session,
            registry,
            svc.invitation_association_registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            invitation_id=invitation.id,
            actor=target,
            new_personal_vault_password="da-igual",
        )


def test_accept_invitation_by_wrong_user_fails(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    svc.confirm_recovery(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code=result.recovery_code
    )
    target = _make_user(db_session, actor["tenant_id"])
    other = _make_user(db_session, actor["tenant_id"])
    invitation = svc.create_invitation(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        invited_by=actor["user"],
        target_user_id=target.id,
    )
    svc.invitation_association_registry.associate(invitation.id, result.unlock_handle)
    with pytest.raises(svc.InvitationWrongUserError):
        svc.accept_invitation(
            db_session,
            registry,
            svc.invitation_association_registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            invitation_id=invitation.id,
            actor=other,
            new_personal_vault_password="da-igual",
        )


def test_reauthorize_invitation_restores_association(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    svc.confirm_recovery(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code=result.recovery_code
    )
    target = _make_user(db_session, actor["tenant_id"])
    invitation = svc.create_invitation(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        invited_by=actor["user"],
        target_user_id=target.id,
    )
    # simular reinicio: se pierde la asociacion efimera
    svc.invitation_association_registry.reset()
    svc.reauthorize_invitation(
        db_session,
        registry,
        svc.invitation_association_registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
        invitation_id=invitation.id,
        expected_version=1,
    )
    assert svc.invitation_association_registry.get(invitation.id) is not None


def test_cancel_invitation(db_session, actor, registry):
    result = _init(db_session, registry, actor)
    target = _make_user(db_session, actor["tenant_id"])
    invitation = svc.create_invitation(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        invited_by=actor["user"],
        target_user_id=target.id,
    )
    svc.cancel_invitation(
        db_session,
        tenant_id=actor["tenant_id"],
        actor=actor["user"],
        invitation_id=invitation.id,
        expected_version=1,
    )
    reloaded = db_session.get(type(invitation), invitation.id)
    assert reloaded.status == "CANCELLED"


# --- credenciales por cliente ---


def test_create_credential_requires_recovery_confirmed(db_session, registry, actor, make_client):
    result = _init(db_session, registry, actor)
    client = make_client(actor["tenant_id"])
    with pytest.raises(svc.RecoveryNotConfirmedError):
        client_sol_credential_service.create_credential(
            db_session,
            registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            client_id=client.id,
            actor=actor["user"],
            app_session_id=actor["app_session_id"],
            credential_kind="SECONDARY",
            sol_username="usuario-sol-ficticio",
            sol_password="clave-sol-ficticia",
            authorization_basis="autorizacion ficticia de prueba",
        )


def test_create_credential_lands_in_needs_validation_and_decrypts(db_session, registry, actor, make_client):
    result = _init(db_session, registry, actor)
    svc.confirm_recovery(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code=result.recovery_code
    )
    client = make_client(actor["tenant_id"])
    credential = client_sol_credential_service.create_credential(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        client_id=client.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
        credential_kind="SECONDARY",
        sol_username="usuario-sol-ficticio",
        sol_password="clave-sol-ficticia",
        authorization_basis="autorizacion ficticia de prueba",
    )
    assert credential.status == "NEEDS_VALIDATION"

    entry = registry.resolve(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        user_id=actor["user"].id,
        app_session_id=actor["app_session_id"],
        operation="VERIFY_ACCESS",
        now=_NOW,
    )
    data_key_row = db_session.get(SolVaultDataKey, result.data_key.id)
    credential_dek = crypto.unwrap_data_key(
        data_key_row.wrapped_data_key,
        bytes(entry.vault_root_key),
        crypto.aad_wrapped_data_key(
            data_key_row_id=data_key_row.id,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            data_key_version=1,
        ),
    )
    username = crypto.decrypt_credential_field(
        credential.sol_username_encrypted,
        credential_dek,
        crypto.aad_credential_field(
            credential_id=credential.id,
            tenant_id=actor["tenant_id"],
            client_id=client.id,
            field_name="sol_username",
            data_key_version=1,
        ),
    )
    assert username == "usuario-sol-ficticio"


def test_create_credential_with_expired_authorization_fails(db_session, registry, actor, make_client):
    result = _init(db_session, registry, actor)
    svc.confirm_recovery(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code=result.recovery_code
    )
    client = make_client(actor["tenant_id"])
    with pytest.raises(client_sol_credential_service.AuthorizationAlreadyExpiredError):
        client_sol_credential_service.create_credential(
            db_session,
            registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            client_id=client.id,
            actor=actor["user"],
            app_session_id=actor["app_session_id"],
            credential_kind="SECONDARY",
            sol_username="usuario-sol-ficticio",
            sol_password="clave-sol-ficticia",
            authorization_basis="autorizacion ficticia vencida",
            authorization_expires_at=_NOW - timedelta(days=1),
        )


def test_update_credential_returns_to_needs_validation(db_session, registry, actor, make_client):
    result = _init(db_session, registry, actor)
    svc.confirm_recovery(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code=result.recovery_code
    )
    client = make_client(actor["tenant_id"])
    credential = client_sol_credential_service.create_credential(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        client_id=client.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
        credential_kind="SECONDARY",
        sol_username="usuario-sol-ficticio",
        sol_password="clave-sol-ficticia",
        authorization_basis="autorizacion ficticia",
    )
    updated = client_sol_credential_service.update_credential(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        client_id=client.id,
        credential_id=credential.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
        expected_version=1,
        sol_password="clave-sol-actualizada-ficticia",
    )
    assert updated.status == "NEEDS_VALIDATION"
    assert updated.version == 2


def test_disable_credential(db_session, registry, actor, make_client):
    result = _init(db_session, registry, actor)
    svc.confirm_recovery(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code=result.recovery_code
    )
    client = make_client(actor["tenant_id"])
    credential = client_sol_credential_service.create_credential(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        client_id=client.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
        credential_kind="SECONDARY",
        sol_username="usuario-sol-ficticio",
        sol_password="clave-sol-ficticia",
        authorization_basis="autorizacion ficticia",
    )
    disabled = client_sol_credential_service.disable_credential(
        db_session,
        tenant_id=actor["tenant_id"],
        client_id=client.id,
        credential_id=credential.id,
        actor=actor["user"],
        expected_version=1,
    )
    assert disabled.status == "DISABLED"
    assert (
        client_sol_credential_service.get_credential_status(
            db_session, tenant_id=actor["tenant_id"], client_id=client.id
        )
        is None
    )


def test_only_one_non_disabled_credential_per_client(db_session, registry, actor, make_client):
    result = _init(db_session, registry, actor)
    svc.confirm_recovery(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code=result.recovery_code
    )
    client = make_client(actor["tenant_id"])
    client_sol_credential_service.create_credential(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        client_id=client.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
        credential_kind="SECONDARY",
        sol_username="usuario-sol-ficticio",
        sol_password="clave-sol-ficticia",
        authorization_basis="autorizacion ficticia",
    )
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        client_sol_credential_service.create_credential(
            db_session,
            registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            client_id=client.id,
            actor=actor["user"],
            app_session_id=actor["app_session_id"],
            credential_kind="SECONDARY",
            sol_username="usuario-sol-ficticio-2",
            sol_password="clave-sol-ficticia-2",
            authorization_basis="autorizacion ficticia",
        )
        db_session.flush()


# --- rotacion funcional (2 usuarios, N credenciales) ---


def test_rotation_with_two_users_preserves_functionality(db_session, registry, actor, make_client):
    result = _init(db_session, registry, actor)
    svc.confirm_recovery(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code=result.recovery_code
    )
    target = _make_user(db_session, actor["tenant_id"])
    invitation = svc.create_invitation(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        invited_by=actor["user"],
        target_user_id=target.id,
    )
    svc.invitation_association_registry.associate(invitation.id, result.unlock_handle)
    svc.accept_invitation(
        db_session,
        registry,
        svc.invitation_association_registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        invitation_id=invitation.id,
        actor=target,
        new_personal_vault_password="contrasena-del-invitado-ficticia",
    )
    target_session = _make_session(db_session, actor["tenant_id"], target.id)

    plaintexts = {}
    for i in range(5):
        client = make_client(actor["tenant_id"])
        credential = client_sol_credential_service.create_credential(
            db_session,
            registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            client_id=client.id,
            actor=actor["user"],
            app_session_id=actor["app_session_id"],
            credential_kind="SECONDARY",
            sol_username=f"usuario-sol-{i}",
            sol_password=f"clave-sol-ficticia-{i}",
            authorization_basis="autorizacion ficticia",
        )
        plaintexts[credential.id] = (client.id, f"usuario-sol-{i}", f"clave-sol-ficticia-{i}")

    new_version = svc.rotate_data_key(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
        expected_version=1,
    )
    assert new_version == 2

    entry = registry.resolve(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        user_id=actor["user"].id,
        app_session_id=actor["app_session_id"],
        operation="VERIFY_ACCESS",
        now=_NOW,
    )
    data_key_row = repo.get_active_data_key(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id
    )
    assert data_key_row.data_key_version == 2
    credential_dek = crypto.unwrap_data_key(
        data_key_row.wrapped_data_key,
        bytes(entry.vault_root_key),
        crypto.aad_wrapped_data_key(
            data_key_row_id=data_key_row.id,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            data_key_version=2,
        ),
    )
    for credential_id, (client_id, expected_username, expected_password) in plaintexts.items():
        credential = db_session.get(ClientSolCredential, credential_id)
        assert credential.data_key_version == 2
        username = crypto.decrypt_credential_field(
            credential.sol_username_encrypted,
            credential_dek,
            crypto.aad_credential_field(
                credential_id=credential.id,
                tenant_id=actor["tenant_id"],
                client_id=client_id,
                field_name="sol_username",
                data_key_version=2,
            ),
        )
        password = crypto.decrypt_credential_field(
            credential.sol_password_encrypted,
            credential_dek,
            crypto.aad_credential_field(
                credential_id=credential.id,
                tenant_id=actor["tenant_id"],
                client_id=client_id,
                field_name="sol_password",
                data_key_version=2,
            ),
        )
        assert username == expected_username
        assert password == expected_password

    # ambos usuarios siguen desbloqueando sin cambios
    svc.lock(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
    )
    svc.unlock(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
        personal_vault_password="contrasena-inicial-ficticia-de-prueba",
    )
    svc.unlock(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        actor=target,
        app_session_id=target_session.id,
        personal_vault_password="contrasena-del-invitado-ficticia",
    )


def test_rotate_data_key_preserves_both_user_slots_identically(db_session, registry, actor, clock_state):
    """Corrective closure (post v0.9.0-alpha.1 rejection): prueba el
    invariante estructural completo de la rotacion de `credential_dek` con
    dos slots USER reales (admin + contador invitado) -- ningun slot es
    revocado, recreado, ni reinvitado; sus ids y `wrapped_vault_root_key`
    permanecen byte-identicos; ambos usuarios bloquean y vuelven a
    desbloquear exitosamente con la nueva `credential_dek` activa."""
    result = _init(db_session, registry, actor)
    svc.confirm_recovery(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code=result.recovery_code
    )
    contador = _make_user(db_session, actor["tenant_id"], role="contador")
    invitation = svc.create_invitation(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        invited_by=actor["user"],
        target_user_id=contador.id,
    )
    svc.invitation_association_registry.associate(invitation.id, result.unlock_handle)
    slot_b = svc.accept_invitation(
        db_session,
        registry,
        svc.invitation_association_registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        invitation_id=invitation.id,
        actor=contador,
        new_personal_vault_password="contrasena-contador-rotacion-ficticia",
    )
    contador_session = _make_session(db_session, actor["tenant_id"], contador.id)
    svc.unlock(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        actor=contador,
        app_session_id=contador_session.id,
        personal_vault_password="contrasena-contador-rotacion-ficticia",
    )

    slot_a_id, slot_b_id = result.user_slot.id, slot_b.id
    wrapped_a_before = bytes(db_session.get(SolVaultKeySlot, slot_a_id).wrapped_vault_root_key)
    wrapped_b_before = bytes(db_session.get(SolVaultKeySlot, slot_b_id).wrapped_vault_root_key)
    invitations_before = db_session.execute(
        select(SolVaultInvitation).where(SolVaultInvitation.vault_id == result.vault.id)
    ).scalars().all()
    assert len(invitations_before) == 1

    new_version = svc.rotate_data_key(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
        expected_version=1,
    )
    assert new_version == 2
    db_session.flush()

    data_keys = db_session.execute(
        select(SolVaultDataKey).where(SolVaultDataKey.vault_id == result.vault.id)
    ).scalars().all()
    active_keys = [k for k in data_keys if k.status == "ACTIVE"]
    retired_keys = [k for k in data_keys if k.status == "RETIRED"]
    assert len(active_keys) == 1
    assert active_keys[0].data_key_version == 2
    assert len(retired_keys) == 1
    assert retired_keys[0].data_key_version == 1

    slot_a_after = db_session.get(SolVaultKeySlot, slot_a_id)
    slot_b_after = db_session.get(SolVaultKeySlot, slot_b_id)
    assert slot_a_after.id == slot_a_id
    assert slot_b_after.id == slot_b_id
    assert slot_a_after.status == "ACTIVE"
    assert slot_b_after.status == "ACTIVE"
    assert slot_a_after.revoked_at is None
    assert slot_b_after.revoked_at is None
    assert bytes(slot_a_after.wrapped_vault_root_key) == wrapped_a_before
    assert bytes(slot_b_after.wrapped_vault_root_key) == wrapped_b_before

    all_user_slots_after = db_session.execute(
        select(SolVaultKeySlot).where(
            SolVaultKeySlot.vault_id == result.vault.id, SolVaultKeySlot.kind == "USER"
        )
    ).scalars().all()
    assert len(all_user_slots_after) == 2  # nunca se creo un tercer slot USER

    invitations_after = db_session.execute(
        select(SolVaultInvitation).where(SolVaultInvitation.vault_id == result.vault.id)
    ).scalars().all()
    assert len(invitations_after) == 1  # ninguna invitacion nueva se emitio
    assert invitations_after[0].id == invitation.id
    assert invitations_after[0].status == "ACCEPTED"

    # admin bloquea y vuelve a desbloquear -- mismo slot, sin reinvitacion
    svc.lock(
        db_session, registry, tenant_id=actor["tenant_id"], vault_id=result.vault.id,
        actor=actor["user"], app_session_id=actor["app_session_id"],
    )
    svc.unlock(
        db_session, registry, tenant_id=actor["tenant_id"], vault_id=result.vault.id,
        actor=actor["user"], app_session_id=actor["app_session_id"],
        personal_vault_password="contrasena-inicial-ficticia-de-prueba",
    )

    # contador bloquea y vuelve a desbloquear -- mismo slot, sin reinvitacion
    svc.lock(
        db_session, registry, tenant_id=actor["tenant_id"], vault_id=result.vault.id,
        actor=contador, app_session_id=contador_session.id,
    )
    svc.unlock(
        db_session, registry, tenant_id=actor["tenant_id"], vault_id=result.vault.id,
        actor=contador, app_session_id=contador_session.id,
        personal_vault_password="contrasena-contador-rotacion-ficticia",
    )

    # ambos acceden mediante la nueva credential_dek activa
    entry_admin = registry.resolve(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, user_id=actor["user"].id,
        app_session_id=actor["app_session_id"], operation="VERIFY_ACCESS", now=clock_state["now"],
    )
    entry_contador = registry.resolve(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, user_id=contador.id,
        app_session_id=contador_session.id, operation="VERIFY_ACCESS", now=clock_state["now"],
    )
    active_data_key = active_keys[0]
    dek_via_admin = crypto.unwrap_data_key(
        active_data_key.wrapped_data_key, bytes(entry_admin.vault_root_key),
        crypto.aad_wrapped_data_key(
            data_key_row_id=active_data_key.id, tenant_id=actor["tenant_id"], vault_id=result.vault.id,
            data_key_version=2,
        ),
    )
    dek_via_contador = crypto.unwrap_data_key(
        active_data_key.wrapped_data_key, bytes(entry_contador.vault_root_key),
        crypto.aad_wrapped_data_key(
            data_key_row_id=active_data_key.id, tenant_id=actor["tenant_id"], vault_id=result.vault.id,
            data_key_version=2,
        ),
    )
    assert dek_via_admin == dek_via_contador


# --- ramas de error adicionales (cobertura, Dominio H) ---


def test_replace_recovery_on_nonexistent_vault_fails(db_session, registry, actor):
    fake_vault_id = uuid.uuid4()
    registry.issue(
        vault_root_key=crypto.generate_key(),
        tenant_id=actor["tenant_id"],
        vault_id=fake_vault_id,
        user_id=actor["user"].id,
        app_session_id=actor["app_session_id"],
        allowed_operations=ALLOWED_OPERATIONS,
        authorized_client_ids=None,
        ttl_seconds=3600,
        now=_NOW,
    )
    with pytest.raises(svc.VaultNotInitializedError):
        svc.replace_recovery(
            db_session,
            registry,
            tenant_id=actor["tenant_id"],
            vault_id=fake_vault_id,
            actor=actor["user"],
            app_session_id=actor["app_session_id"],
            expected_version=1,
        )


def test_replace_recovery_wrong_version_fails(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    with pytest.raises(svc.VersionConflictError):
        svc.replace_recovery(
            db_session,
            registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            actor=actor["user"],
            app_session_id=actor["app_session_id"],
            expected_version=99,
        )


def test_replace_recovery_twice_without_confirming_fails(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    svc.confirm_recovery(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code=result.recovery_code
    )
    svc.replace_recovery(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
        expected_version=1,
    )
    with pytest.raises(svc.RecoveryReplacementAlreadyPendingError):
        svc.replace_recovery(
            db_session,
            registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            actor=actor["user"],
            app_session_id=actor["app_session_id"],
            expected_version=1,
        )


def test_recover_on_nonexistent_vault_fails(db_session, registry, actor):
    with pytest.raises(svc.VaultNotInitializedError):
        svc.recover(
            db_session,
            registry,
            tenant_id=actor["tenant_id"],
            vault_id=uuid.uuid4(),
            actor=actor["user"],
            app_session_id=actor["app_session_id"],
            recovery_code="da-igual",
            new_personal_vault_password="da-igual-tambien-ficticia",
        )


def test_recover_on_disabled_vault_fails(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    vault = db_session.get(SolVault, result.vault.id)
    vault.status = "DISABLED"
    vault.disabled_at = _NOW
    vault.disabled_by = actor["user"].id
    db_session.flush()
    with pytest.raises(svc.VaultDisabledError):
        svc.recover(
            db_session,
            registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            actor=actor["user"],
            app_session_id=actor["app_session_id"],
            recovery_code=result.recovery_code,
            new_personal_vault_password="da-igual-ficticia",
        )


def test_change_password_without_active_user_slot_fails(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    slot = db_session.get(SolVaultKeySlot, result.user_slot.id)
    slot.status = "REVOKED"
    slot.revoked_at = _NOW
    slot.revoked_by = actor["user"].id
    db_session.flush()
    with pytest.raises(svc.VaultNotInitializedError):
        svc.change_password(
            db_session,
            registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            actor=actor["user"],
            app_session_id=actor["app_session_id"],
            new_personal_vault_password="contrasena-nueva-ficticia-2",
        )


def test_revoke_slot_on_nonexistent_vault_fails(db_session, actor):
    with pytest.raises(svc.VaultNotInitializedError):
        svc.revoke_slot(
            db_session,
            tenant_id=actor["tenant_id"],
            vault_id=uuid.uuid4(),
            actor=actor["user"],
            slot_id=uuid.uuid4(),
            expected_version=1,
        )


def test_revoke_slot_wrong_version_fails(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    with pytest.raises(svc.VersionConflictError):
        svc.revoke_slot(
            db_session,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            actor=actor["user"],
            slot_id=result.user_slot.id,
            expected_version=99,
        )


def test_revoke_last_recovery_slot_without_remaining_user_slots_fails(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    svc.confirm_recovery(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code=result.recovery_code
    )
    user_slot = db_session.get(SolVaultKeySlot, result.user_slot.id)
    user_slot.status = "REVOKED"
    user_slot.revoked_at = _NOW
    user_slot.revoked_by = actor["user"].id
    db_session.flush()
    with pytest.raises(svc.LastRecoverySlotError):
        svc.revoke_slot(
            db_session,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            actor=actor["user"],
            slot_id=result.recovery_slot.id,
            expected_version=1,
        )


def test_rotate_data_key_on_nonexistent_vault_fails(db_session, registry, actor):
    fake_vault_id = uuid.uuid4()
    registry.issue(
        vault_root_key=crypto.generate_key(),
        tenant_id=actor["tenant_id"],
        vault_id=fake_vault_id,
        user_id=actor["user"].id,
        app_session_id=actor["app_session_id"],
        allowed_operations=ALLOWED_OPERATIONS,
        authorized_client_ids=None,
        ttl_seconds=3600,
        now=_NOW,
    )
    with pytest.raises(svc.VaultNotInitializedError):
        svc.rotate_data_key(
            db_session,
            registry,
            tenant_id=actor["tenant_id"],
            vault_id=fake_vault_id,
            actor=actor["user"],
            app_session_id=actor["app_session_id"],
            expected_version=1,
        )


def test_rotate_data_key_wrong_version_fails(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    with pytest.raises(svc.VersionConflictError):
        svc.rotate_data_key(
            db_session,
            registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            actor=actor["user"],
            app_session_id=actor["app_session_id"],
            expected_version=99,
        )


# --- invitaciones: ramas de error adicionales ---


def test_create_invitation_already_pending_fails(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    target = _make_user(db_session, actor["tenant_id"])
    svc.create_invitation(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        invited_by=actor["user"],
        target_user_id=target.id,
    )
    with pytest.raises(svc.InvitationAlreadyPendingError):
        svc.create_invitation(
            db_session,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            invited_by=actor["user"],
            target_user_id=target.id,
        )


def test_create_invitation_auto_expires_previous_pending(db_session, registry, actor, clock_state):
    result = _init(db_session, registry, actor)
    target = _make_user(db_session, actor["tenant_id"])
    first = svc.create_invitation(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        invited_by=actor["user"],
        target_user_id=target.id,
        ttl_seconds=1,
    )
    clock_state["now"] += timedelta(seconds=5)
    second = svc.create_invitation(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        invited_by=actor["user"],
        target_user_id=target.id,
    )
    assert second.id != first.id
    reloaded_first = db_session.get(type(first), first.id)
    assert reloaded_first.status == "EXPIRED"


def test_accept_invitation_not_found_fails(db_session, registry, actor):
    with pytest.raises(svc.InvitationNotFoundError):
        svc.accept_invitation(
            db_session,
            registry,
            svc.invitation_association_registry,
            tenant_id=actor["tenant_id"],
            vault_id=uuid.uuid4(),
            invitation_id=uuid.uuid4(),
            actor=actor["user"],
            new_personal_vault_password="da-igual",
        )


def test_accept_invitation_already_resolved_fails(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    target = _make_user(db_session, actor["tenant_id"])
    invitation = svc.create_invitation(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        invited_by=actor["user"],
        target_user_id=target.id,
    )
    svc.cancel_invitation(
        db_session,
        tenant_id=actor["tenant_id"],
        actor=actor["user"],
        invitation_id=invitation.id,
        expected_version=1,
    )
    with pytest.raises(svc.InvitationExpiredError):
        svc.accept_invitation(
            db_session,
            registry,
            svc.invitation_association_registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            invitation_id=invitation.id,
            actor=target,
            new_personal_vault_password="da-igual",
        )


def test_accept_invitation_expired_by_time_fails(db_session, registry, actor, clock_state):
    result = _init(db_session, registry, actor)
    target = _make_user(db_session, actor["tenant_id"])
    invitation = svc.create_invitation(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        invited_by=actor["user"],
        target_user_id=target.id,
        ttl_seconds=1,
    )
    clock_state["now"] += timedelta(seconds=5)
    with pytest.raises(svc.InvitationExpiredError):
        svc.accept_invitation(
            db_session,
            registry,
            svc.invitation_association_registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            invitation_id=invitation.id,
            actor=target,
            new_personal_vault_password="da-igual",
        )
    reloaded = db_session.get(type(invitation), invitation.id)
    assert reloaded.status == "EXPIRED"


def test_accept_invitation_inviter_locked_after_association_fails(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    target = _make_user(db_session, actor["tenant_id"])
    invitation = svc.create_invitation(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        invited_by=actor["user"],
        target_user_id=target.id,
    )
    svc.invitation_association_registry.associate(invitation.id, result.unlock_handle)
    svc.lock(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
    )
    with pytest.raises(svc.InvitationInviterNotUnlockedError):
        svc.accept_invitation(
            db_session,
            registry,
            svc.invitation_association_registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            invitation_id=invitation.id,
            actor=target,
            new_personal_vault_password="da-igual",
        )


def test_accept_invitation_inviter_without_slot_management_fails(db_session, registry, actor):
    tenant2 = Tenant(name="Tenant contador SOL invitaciones")
    db_session.add(tenant2)
    db_session.flush()
    contador_user = _make_user(db_session, tenant2.id, role="contador")
    contador_session = _make_session(db_session, tenant2.id, contador_user.id)
    contador_actor = {"tenant_id": tenant2.id, "user": contador_user, "app_session_id": contador_session.id}
    contador_result = _init(db_session, registry, contador_actor, password="contrasena-contador-ficticia")
    target = _make_user(db_session, tenant2.id)
    invitation = svc.create_invitation(
        db_session,
        tenant_id=tenant2.id,
        vault_id=contador_result.vault.id,
        invited_by=contador_user,
        target_user_id=target.id,
    )
    svc.invitation_association_registry.associate(invitation.id, contador_result.unlock_handle)
    with pytest.raises(svc.InvitationInviterNotUnlockedError):
        svc.accept_invitation(
            db_session,
            registry,
            svc.invitation_association_registry,
            tenant_id=tenant2.id,
            vault_id=contador_result.vault.id,
            invitation_id=invitation.id,
            actor=target,
            new_personal_vault_password="da-igual",
        )


def test_cancel_invitation_not_found_fails(db_session, actor):
    with pytest.raises(svc.InvitationNotFoundError):
        svc.cancel_invitation(
            db_session,
            tenant_id=actor["tenant_id"],
            actor=actor["user"],
            invitation_id=uuid.uuid4(),
            expected_version=1,
        )


def test_cancel_invitation_wrong_version_fails(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    target = _make_user(db_session, actor["tenant_id"])
    invitation = svc.create_invitation(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        invited_by=actor["user"],
        target_user_id=target.id,
    )
    with pytest.raises(svc.VersionConflictError):
        svc.cancel_invitation(
            db_session,
            tenant_id=actor["tenant_id"],
            actor=actor["user"],
            invitation_id=invitation.id,
            expected_version=99,
        )


def test_cancel_invitation_already_resolved_fails(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    target = _make_user(db_session, actor["tenant_id"])
    invitation = svc.create_invitation(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        invited_by=actor["user"],
        target_user_id=target.id,
    )
    svc.cancel_invitation(
        db_session,
        tenant_id=actor["tenant_id"],
        actor=actor["user"],
        invitation_id=invitation.id,
        expected_version=1,
    )
    with pytest.raises(svc.InvitationNotPendingError):
        svc.cancel_invitation(
            db_session,
            tenant_id=actor["tenant_id"],
            actor=actor["user"],
            invitation_id=invitation.id,
            expected_version=2,
        )


def test_reauthorize_invitation_not_found_fails(db_session, registry, actor):
    with pytest.raises(svc.InvitationNotFoundError):
        svc.reauthorize_invitation(
            db_session,
            registry,
            svc.invitation_association_registry,
            tenant_id=actor["tenant_id"],
            vault_id=uuid.uuid4(),
            actor=actor["user"],
            app_session_id=actor["app_session_id"],
            invitation_id=uuid.uuid4(),
            expected_version=1,
        )


def test_reauthorize_invitation_wrong_user_fails(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    target = _make_user(db_session, actor["tenant_id"])
    invitation = svc.create_invitation(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        invited_by=actor["user"],
        target_user_id=target.id,
    )
    with pytest.raises(svc.InvitationWrongUserError):
        svc.reauthorize_invitation(
            db_session,
            registry,
            svc.invitation_association_registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            actor=target,
            app_session_id=actor["app_session_id"],
            invitation_id=invitation.id,
            expected_version=1,
        )


def test_reauthorize_invitation_wrong_version_fails(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    target = _make_user(db_session, actor["tenant_id"])
    invitation = svc.create_invitation(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        invited_by=actor["user"],
        target_user_id=target.id,
    )
    with pytest.raises(svc.VersionConflictError):
        svc.reauthorize_invitation(
            db_session,
            registry,
            svc.invitation_association_registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            actor=actor["user"],
            app_session_id=actor["app_session_id"],
            invitation_id=invitation.id,
            expected_version=99,
        )


def test_reauthorize_invitation_no_longer_pending_fails(db_session, registry, actor):
    result = _init(db_session, registry, actor)
    target = _make_user(db_session, actor["tenant_id"])
    invitation = svc.create_invitation(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        invited_by=actor["user"],
        target_user_id=target.id,
    )
    svc.cancel_invitation(
        db_session,
        tenant_id=actor["tenant_id"],
        actor=actor["user"],
        invitation_id=invitation.id,
        expected_version=1,
    )
    with pytest.raises(svc.InvitationExpiredError):
        svc.reauthorize_invitation(
            db_session,
            registry,
            svc.invitation_association_registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            actor=actor["user"],
            app_session_id=actor["app_session_id"],
            invitation_id=invitation.id,
            expected_version=2,
        )


def test_reauthorize_invitation_handle_missing_after_resolve_fails(db_session, registry, actor, monkeypatch):
    """Rama defensiva: en la practica un `resolve()` exitoso implica que
    `get_handle` encuentra el mismo handle -- se fuerza con monkeypatch
    para cubrir el `if handle is None` de la Seccion 15."""
    result = _init(db_session, registry, actor)
    target = _make_user(db_session, actor["tenant_id"])
    invitation = svc.create_invitation(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        invited_by=actor["user"],
        target_user_id=target.id,
    )
    monkeypatch.setattr(registry, "get_handle", lambda **kwargs: None)
    with pytest.raises(svc.InvitationInviterNotUnlockedError):
        svc.reauthorize_invitation(
            db_session,
            registry,
            svc.invitation_association_registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            actor=actor["user"],
            app_session_id=actor["app_session_id"],
            invitation_id=invitation.id,
            expected_version=1,
        )


# --- credenciales por cliente: ramas de error adicionales ---


def test_update_credential_not_found_fails(db_session, registry, actor, make_client):
    result = _init(db_session, registry, actor)
    svc.confirm_recovery(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code=result.recovery_code
    )
    client = make_client(actor["tenant_id"])
    with pytest.raises(client_sol_credential_service.CredentialNotFoundError):
        client_sol_credential_service.update_credential(
            db_session,
            registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            client_id=client.id,
            credential_id=uuid.uuid4(),
            actor=actor["user"],
            app_session_id=actor["app_session_id"],
            expected_version=1,
            sol_password="clave-nueva-ficticia",
        )


def test_update_credential_wrong_version_fails(db_session, registry, actor, make_client):
    result = _init(db_session, registry, actor)
    svc.confirm_recovery(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code=result.recovery_code
    )
    client = make_client(actor["tenant_id"])
    credential = client_sol_credential_service.create_credential(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        client_id=client.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
        credential_kind="SECONDARY",
        sol_username="usuario-sol-ficticio",
        sol_password="clave-sol-ficticia",
        authorization_basis="autorizacion ficticia",
    )
    with pytest.raises(client_sol_credential_service.VersionConflictError):
        client_sol_credential_service.update_credential(
            db_session,
            registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            client_id=client.id,
            credential_id=credential.id,
            actor=actor["user"],
            app_session_id=actor["app_session_id"],
            expected_version=99,
            sol_password="clave-nueva-ficticia",
        )


def test_update_credential_with_new_expired_authorization_fails(db_session, registry, actor, make_client):
    result = _init(db_session, registry, actor)
    svc.confirm_recovery(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code=result.recovery_code
    )
    client = make_client(actor["tenant_id"])
    credential = client_sol_credential_service.create_credential(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        client_id=client.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
        credential_kind="SECONDARY",
        sol_username="usuario-sol-ficticio",
        sol_password="clave-sol-ficticia",
        authorization_basis="autorizacion ficticia",
    )
    with pytest.raises(client_sol_credential_service.AuthorizationAlreadyExpiredError):
        client_sol_credential_service.update_credential(
            db_session,
            registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            client_id=client.id,
            credential_id=credential.id,
            actor=actor["user"],
            app_session_id=actor["app_session_id"],
            expected_version=1,
            authorization_expires_at=_NOW - timedelta(days=1),
        )


def test_update_credential_with_stale_expired_authorization_not_renewed_fails(
    db_session, registry, actor, make_client, clock_state
):
    result = _init(db_session, registry, actor)
    svc.confirm_recovery(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code=result.recovery_code
    )
    client = make_client(actor["tenant_id"])
    credential = client_sol_credential_service.create_credential(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        client_id=client.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
        credential_kind="SECONDARY",
        sol_username="usuario-sol-ficticio",
        sol_password="clave-sol-ficticia",
        authorization_basis="autorizacion ficticia",
        authorization_expires_at=clock_state["now"] + timedelta(seconds=2),
    )
    clock_state["now"] += timedelta(seconds=5)  # la autorizacion ya vencio
    with pytest.raises(client_sol_credential_service.AuthorizationAlreadyExpiredError):
        client_sol_credential_service.update_credential(
            db_session,
            registry,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            client_id=client.id,
            credential_id=credential.id,
            actor=actor["user"],
            app_session_id=actor["app_session_id"],
            expected_version=1,
            sol_username="usuario-sol-actualizado",
        )


def test_update_credential_username_only_and_authorization_metadata(
    db_session, registry, actor, make_client, clock_state
):
    result = _init(db_session, registry, actor)
    svc.confirm_recovery(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code=result.recovery_code
    )
    client = make_client(actor["tenant_id"])
    credential = client_sol_credential_service.create_credential(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        client_id=client.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
        credential_kind="SECONDARY",
        sol_username="usuario-sol-ficticio",
        sol_password="clave-sol-ficticia",
        authorization_basis="autorizacion ficticia",
    )
    updated = client_sol_credential_service.update_credential(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        client_id=client.id,
        credential_id=credential.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
        expected_version=1,
        sol_username="usuario-sol-renovado",
        authorization_basis="autorizacion renovada ficticia",
        authorization_expires_at=clock_state["now"] + timedelta(days=30),
        authorization_reference="REF-FICTICIA-001",
        authorized_scope_description="alcance ficticio de prueba",
    )
    assert updated.status == "NEEDS_VALIDATION"
    assert updated.authorization_basis == "autorizacion renovada ficticia"
    assert updated.authorization_reference == "REF-FICTICIA-001"
    assert updated.authorized_scope_description == "alcance ficticio de prueba"
    assert updated.last_scope_reviewed_at is not None

    entry = registry.resolve(
        db_session,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        user_id=actor["user"].id,
        app_session_id=actor["app_session_id"],
        operation="VERIFY_ACCESS",
        now=clock_state["now"],
    )
    data_key_row = repo.get_active_data_key(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id
    )
    credential_dek = crypto.unwrap_data_key(
        data_key_row.wrapped_data_key,
        bytes(entry.vault_root_key),
        crypto.aad_wrapped_data_key(
            data_key_row_id=data_key_row.id,
            tenant_id=actor["tenant_id"],
            vault_id=result.vault.id,
            data_key_version=1,
        ),
    )
    username = crypto.decrypt_credential_field(
        updated.sol_username_encrypted,
        credential_dek,
        crypto.aad_credential_field(
            credential_id=updated.id,
            tenant_id=actor["tenant_id"],
            client_id=client.id,
            field_name="sol_username",
            data_key_version=1,
        ),
    )
    assert username == "usuario-sol-renovado"
    # la password no se toco -- sigue siendo la original
    password = crypto.decrypt_credential_field(
        updated.sol_password_encrypted,
        credential_dek,
        crypto.aad_credential_field(
            credential_id=updated.id,
            tenant_id=actor["tenant_id"],
            client_id=client.id,
            field_name="sol_password",
            data_key_version=1,
        ),
    )
    assert password == "clave-sol-ficticia"


def test_disable_credential_not_found_fails(db_session, actor, make_client):
    client = make_client(actor["tenant_id"])
    with pytest.raises(client_sol_credential_service.CredentialNotFoundError):
        client_sol_credential_service.disable_credential(
            db_session,
            tenant_id=actor["tenant_id"],
            client_id=client.id,
            credential_id=uuid.uuid4(),
            actor=actor["user"],
            expected_version=1,
        )


def test_disable_credential_already_disabled_fails(db_session, registry, actor, make_client):
    result = _init(db_session, registry, actor)
    svc.confirm_recovery(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code=result.recovery_code
    )
    client = make_client(actor["tenant_id"])
    credential = client_sol_credential_service.create_credential(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        client_id=client.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
        credential_kind="SECONDARY",
        sol_username="usuario-sol-ficticio",
        sol_password="clave-sol-ficticia",
        authorization_basis="autorizacion ficticia",
    )
    client_sol_credential_service.disable_credential(
        db_session,
        tenant_id=actor["tenant_id"],
        client_id=client.id,
        credential_id=credential.id,
        actor=actor["user"],
        expected_version=1,
    )
    with pytest.raises(client_sol_credential_service.CredentialAlreadyDisabledError):
        client_sol_credential_service.disable_credential(
            db_session,
            tenant_id=actor["tenant_id"],
            client_id=client.id,
            credential_id=credential.id,
            actor=actor["user"],
            expected_version=2,
        )


def test_disable_credential_wrong_version_fails(db_session, registry, actor, make_client):
    result = _init(db_session, registry, actor)
    svc.confirm_recovery(
        db_session, tenant_id=actor["tenant_id"], vault_id=result.vault.id, recovery_code=result.recovery_code
    )
    client = make_client(actor["tenant_id"])
    credential = client_sol_credential_service.create_credential(
        db_session,
        registry,
        tenant_id=actor["tenant_id"],
        vault_id=result.vault.id,
        client_id=client.id,
        actor=actor["user"],
        app_session_id=actor["app_session_id"],
        credential_kind="SECONDARY",
        sol_username="usuario-sol-ficticio",
        sol_password="clave-sol-ficticia",
        authorization_basis="autorizacion ficticia",
    )
    with pytest.raises(client_sol_credential_service.VersionConflictError):
        client_sol_credential_service.disable_credential(
            db_session,
            tenant_id=actor["tenant_id"],
            client_id=client.id,
            credential_id=credential.id,
            actor=actor["user"],
            expected_version=99,
        )
