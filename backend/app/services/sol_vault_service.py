"""Boveda de credenciales SOL (Fase 5, Bloque 5.1, ver ADR-0068 a
ADR-0071). Ciclo de vida de la boveda, recuperacion, desbloqueo/bloqueo en
memoria, cambio de contrasena personal, invitaciones de dos actores, y
rotacion ordinaria de `credential_dek`. Ningun servicio de este modulo
ejecuta `db.commit()` ni `db.rollback()` (ADR-0037) -- el router es dueno
de la transaccion de cada endpoint.

Arquitectura validada experimentalmente en el Spike C (rama
`spike/phase5-sol-vault-crypto`, commit `2096a9b`, ver
`docs/DECISIONS/ADR-0068.md`). Este modulo es la implementacion
productiva -- no importa nada del spike en tiempo de ejecucion."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import clock
from app.core import sol_vault_crypto as crypto
from app.core.permissions import Permission, has_permission
from app.core.sol_vault_argon2_gate import argon2_gate
from app.core.sol_vault_registry import VaultUnlockRegistry
from app.models.audit_log import AuditAction
from app.models.sol_vault import SolVault, SolVaultDataKey, SolVaultInvitation, SolVaultKeySlot
from app.models.user import User
from app.repositories import sol_vault_repository as repo
from app.services import audit_service

_RECOVERY_CODE_BYTES = 32
_DEFAULT_UNLOCK_TTL_SECONDS = 8 * 3600  # 8 horas, alineado con session_ttl_hours por defecto

_ADMIN_OPERATIONS = frozenset(
    {"CREDENTIAL_MANAGEMENT", "SLOT_MANAGEMENT", "DATA_KEY_ROTATION", "RECOVERY", "VERIFY_ACCESS"}
)
_CONTADOR_OPERATIONS = frozenset({"CREDENTIAL_MANAGEMENT", "VERIFY_ACCESS"})


class SolVaultServiceError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class VaultAlreadyInitializedError(SolVaultServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_VAULT_ALREADY_INITIALIZED")


class VaultNotInitializedError(SolVaultServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_VAULT_NOT_INITIALIZED")


class VaultDisabledError(SolVaultServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_VAULT_DISABLED")


class BadVaultPasswordError(SolVaultServiceError):
    """Respuesta publica generica -- nunca distingue si el slot no existe,
    la contrasena/codigo es incorrecto, o esta en backoff."""

    def __init__(self) -> None:
        super().__init__("SOL_VAULT_BAD_PASSWORD")


class VersionConflictError(SolVaultServiceError):
    def __init__(self) -> None:
        super().__init__("VERSION_CONFLICT")


class RecoveryNotConfirmedError(SolVaultServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_VAULT_RECOVERY_NOT_CONFIRMED")


class LastUserSlotError(SolVaultServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_VAULT_LAST_USER_SLOT")


class LastRecoverySlotError(SolVaultServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_VAULT_LAST_RECOVERY_SLOT")


class RecoveryReplacementAlreadyPendingError(SolVaultServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_VAULT_RECOVERY_REPLACEMENT_ALREADY_PENDING")


class InvitationNotFoundError(SolVaultServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_VAULT_INVITATION_NOT_FOUND")


class InvitationInviterNotUnlockedError(SolVaultServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_VAULT_INVITATION_INVITER_NOT_UNLOCKED")


class InvitationWrongUserError(SolVaultServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_VAULT_INVITATION_WRONG_USER")


class InvitationExpiredError(SolVaultServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_VAULT_INVITATION_EXPIRED")


class InvitationAlreadyPendingError(SolVaultServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_VAULT_INVITATION_ALREADY_PENDING")


class InvitationNotPendingError(SolVaultServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_VAULT_INVITATION_NOT_PENDING")


def _allowed_operations_for(actor: User) -> frozenset[str]:
    if has_permission(actor, Permission.SOL_VAULT_MANAGE_USERS):
        return _ADMIN_OPERATIONS
    return _CONTADOR_OPERATIONS


def _advisory_lock(db: Session, vault_id: uuid.UUID) -> None:
    """`pg_advisory_xact_lock`: se libera automaticamente al terminar la
    transaccion (commit o rollback), nunca requiere un unlock explicito."""
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": str(vault_id)})


def _unwrap_credential_dek(
    vault_root_key: bytes, data_key_row: SolVaultDataKey, tenant_id: uuid.UUID
) -> bytes:
    aad = crypto.aad_wrapped_data_key(
        data_key_row_id=data_key_row.id,
        tenant_id=tenant_id,
        vault_id=data_key_row.vault_id,
        data_key_version=data_key_row.data_key_version,
    )
    return crypto.unwrap_data_key(data_key_row.wrapped_data_key, vault_root_key, aad)


# --- inicializacion y estado ---


@dataclass
class InitializeResult:
    vault: SolVault
    user_slot: SolVaultKeySlot
    recovery_slot: SolVaultKeySlot
    data_key: SolVaultDataKey
    recovery_code: str  # solo se devuelve aqui, una sola vez
    unlock_handle: str


def get_vault_status(db: Session, *, tenant_id: uuid.UUID) -> SolVault | None:
    """`None` significa NOT_INITIALIZED (estado derivado, ver ADR-0068) --
    nunca se persiste ese valor como fila."""
    return repo.get_vault_by_tenant(db, tenant_id)


def initialize_vault(
    db: Session,
    registry: VaultUnlockRegistry,
    *,
    tenant_id: uuid.UUID,
    actor: User,
    app_session_id: uuid.UUID,
    personal_vault_password: str,
) -> InitializeResult:
    if repo.get_vault_by_tenant(db, tenant_id) is not None:
        raise VaultAlreadyInitializedError()

    now = clock.utcnow()
    vault = SolVault(tenant_id=tenant_id, status="ACTIVE", initialized_at=now, initialized_by=actor.id)
    db.add(vault)
    db.flush()

    root_key = crypto.generate_key()
    params = crypto.production_argon2_params()

    user_salt = crypto.generate_salt()
    user_kek = argon2_gate.derive_kek(personal_vault_password, user_salt, params)
    user_slot_id = uuid.uuid4()
    user_aad = crypto.aad_wrapped_vault_root_key(
        slot_id=user_slot_id,
        tenant_id=tenant_id,
        vault_id=vault.id,
        user_id_or_recovery=str(actor.id),
        kdf_version=crypto.PRODUCTION_ARGON2_KDF_VERSION,
    )
    user_slot = SolVaultKeySlot(
        id=user_slot_id,
        tenant_id=tenant_id,
        vault_id=vault.id,
        user_id=actor.id,
        kind="USER",
        kdf_salt=user_salt,
        kdf_time_cost=params.time_cost,
        kdf_memory_cost_kib=params.memory_cost_kib,
        kdf_parallelism=params.parallelism,
        kdf_hash_len=params.hash_len,
        kdf_version=crypto.PRODUCTION_ARGON2_KDF_VERSION,
        wrapped_vault_root_key=crypto.wrap_vault_root_key(root_key, user_kek, user_aad),
        status="ACTIVE",
    )
    db.add(user_slot)

    recovery_code = crypto.generate_key(_RECOVERY_CODE_BYTES).hex()
    recovery_salt = crypto.generate_salt()
    recovery_kek = argon2_gate.derive_kek(recovery_code, recovery_salt, params)
    recovery_slot_id = uuid.uuid4()
    recovery_aad = crypto.aad_wrapped_vault_root_key(
        slot_id=recovery_slot_id,
        tenant_id=tenant_id,
        vault_id=vault.id,
        user_id_or_recovery="RECOVERY",
        kdf_version=crypto.PRODUCTION_ARGON2_KDF_VERSION,
    )
    recovery_slot = SolVaultKeySlot(
        id=recovery_slot_id,
        tenant_id=tenant_id,
        vault_id=vault.id,
        user_id=None,
        kind="RECOVERY",
        kdf_salt=recovery_salt,
        kdf_time_cost=params.time_cost,
        kdf_memory_cost_kib=params.memory_cost_kib,
        kdf_parallelism=params.parallelism,
        kdf_hash_len=params.hash_len,
        kdf_version=crypto.PRODUCTION_ARGON2_KDF_VERSION,
        wrapped_vault_root_key=crypto.wrap_vault_root_key(root_key, recovery_kek, recovery_aad),
        status="ACTIVE",
        recovery_confirmed_at=None,
    )
    db.add(recovery_slot)
    db.flush()

    data_key = crypto.generate_key()
    data_key_row_id = uuid.uuid4()
    data_key_aad = crypto.aad_wrapped_data_key(
        data_key_row_id=data_key_row_id, tenant_id=tenant_id, vault_id=vault.id, data_key_version=1
    )
    data_key_row = SolVaultDataKey(
        id=data_key_row_id,
        tenant_id=tenant_id,
        vault_id=vault.id,
        data_key_version=1,
        wrapped_data_key=crypto.wrap_data_key(data_key, root_key, data_key_aad),
        status="ACTIVE",
        activated_at=now,
        created_by=actor.id,
        created_at=now,
    )
    db.add(data_key_row)
    db.flush()

    handle = registry.issue(
        vault_root_key=root_key,
        tenant_id=tenant_id,
        vault_id=vault.id,
        user_id=actor.id,
        app_session_id=app_session_id,
        allowed_operations=_allowed_operations_for(actor),
        authorized_client_ids=None,
        ttl_seconds=_DEFAULT_UNLOCK_TTL_SECONDS,
        now=now,
    )

    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=actor.id,
        action=AuditAction.SOL_VAULT_INITIALIZE,
        entity_type="sol_vault",
        entity_id=vault.id,
    )
    return InitializeResult(
        vault=vault,
        user_slot=user_slot,
        recovery_slot=recovery_slot,
        data_key=data_key_row,
        recovery_code=recovery_code,
        unlock_handle=handle,
    )


def confirm_recovery(db: Session, *, tenant_id: uuid.UUID, vault_id: uuid.UUID, recovery_code: str) -> None:
    """Verifica criptograficamente el codigo: deriva el KEK y comprueba
    que realmente desenvuelve `vault_root_key` -- nunca una simple
    comparacion de texto. Si existe otro slot RECOVERY ya confirmado y
    activo, se revoca en la MISMA transaccion (nunca deben coexistir dos
    RECOVERY confirmados activos tras el commit)."""
    now = clock.utcnow()
    slot = repo.get_pending_recovery_slot(db, tenant_id=tenant_id, vault_id=vault_id)
    if slot is None:
        raise BadVaultPasswordError()

    params = crypto.Argon2Params(
        time_cost=slot.kdf_time_cost,
        memory_cost_kib=slot.kdf_memory_cost_kib,
        parallelism=slot.kdf_parallelism,
        hash_len=slot.kdf_hash_len,
    )
    kek = argon2_gate.derive_kek(recovery_code, slot.kdf_salt, params)
    aad = crypto.aad_wrapped_vault_root_key(
        slot_id=slot.id,
        tenant_id=tenant_id,
        vault_id=vault_id,
        user_id_or_recovery="RECOVERY",
        kdf_version=slot.kdf_version,
    )
    try:
        crypto.unwrap_vault_root_key(slot.wrapped_vault_root_key, kek, aad)
    except crypto.SolVaultDecryptionError as exc:
        raise BadVaultPasswordError() from exc

    other_confirmed = repo.get_confirmed_recovery_slot(db, tenant_id=tenant_id, vault_id=vault_id)
    if other_confirmed is not None:
        other_confirmed.status = "REVOKED"
        other_confirmed.revoked_at = now
        db.flush()
        audit_service.record(
            db,
            tenant_id=tenant_id,
            user_id=None,
            action=AuditAction.SOL_VAULT_SLOT_REVOKE,
            entity_type="sol_vault_key_slot",
            entity_id=other_confirmed.id,
            metadata={"reason": "recovery_replaced"},
        )

    slot.recovery_confirmed_at = now
    db.flush()
    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=None,
        action=AuditAction.SOL_VAULT_RECOVERY_CONFIRM,
        entity_type="sol_vault",
        entity_id=vault_id,
        metadata={"slot_id": str(slot.id)},
    )


def replace_recovery(
    db: Session,
    registry: VaultUnlockRegistry,
    *,
    tenant_id: uuid.UUID,
    vault_id: uuid.UUID,
    actor: User,
    app_session_id: uuid.UUID,
    expected_version: int,
) -> str:
    """Crea un slot RECOVERY nuevo pendiente sin revocar todavia el
    anterior confirmado -- ambos coexisten hasta que `confirm_recovery`
    revoque el anterior en la misma transaccion de confirmacion."""
    now = clock.utcnow()
    entry = registry.resolve(
        db,
        tenant_id=tenant_id,
        vault_id=vault_id,
        user_id=actor.id,
        app_session_id=app_session_id,
        operation="RECOVERY",
        now=now,
    )

    vault = repo.get_vault_for_update(db, tenant_id=tenant_id, vault_id=vault_id)
    if vault is None:
        raise VaultNotInitializedError()
    if vault.version != expected_version:
        raise VersionConflictError()

    if repo.get_pending_recovery_slot(db, tenant_id=tenant_id, vault_id=vault_id) is not None:
        raise RecoveryReplacementAlreadyPendingError()

    params = crypto.production_argon2_params()
    new_code = crypto.generate_key(_RECOVERY_CODE_BYTES).hex()
    new_salt = crypto.generate_salt()
    new_kek = argon2_gate.derive_kek(new_code, new_salt, params)
    new_slot_id = uuid.uuid4()
    new_aad = crypto.aad_wrapped_vault_root_key(
        slot_id=new_slot_id,
        tenant_id=tenant_id,
        vault_id=vault_id,
        user_id_or_recovery="RECOVERY",
        kdf_version=crypto.PRODUCTION_ARGON2_KDF_VERSION,
    )
    new_slot = SolVaultKeySlot(
        id=new_slot_id,
        tenant_id=tenant_id,
        vault_id=vault_id,
        user_id=None,
        kind="RECOVERY",
        kdf_salt=new_salt,
        kdf_time_cost=params.time_cost,
        kdf_memory_cost_kib=params.memory_cost_kib,
        kdf_parallelism=params.parallelism,
        kdf_hash_len=params.hash_len,
        kdf_version=crypto.PRODUCTION_ARGON2_KDF_VERSION,
        wrapped_vault_root_key=crypto.wrap_vault_root_key(bytes(entry.vault_root_key), new_kek, new_aad),
        status="ACTIVE",
        recovery_confirmed_at=None,
    )
    db.add(new_slot)
    db.flush()
    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=actor.id,
        action=AuditAction.SOL_VAULT_RECOVERY_REPLACE,
        entity_type="sol_vault",
        entity_id=vault_id,
        metadata={"new_slot_id": str(new_slot_id)},
    )
    return new_code


def recover(
    db: Session,
    registry: VaultUnlockRegistry,
    *,
    tenant_id: uuid.UUID,
    vault_id: uuid.UUID,
    actor: User,
    app_session_id: uuid.UUID,
    recovery_code: str,
    new_personal_vault_password: str,
) -> None:
    """`POST /sol-vault/recover`: solo llamable por un actor con
    `sol_vault:recover` (verificado por el router). Crea un nuevo slot
    USER para el actor, revoca su slot USER anterior si existia,
    transiciona `RECOVERY_REQUIRED -> ACTIVE`, emite un unlock en memoria.
    Nunca devuelve ninguna clave."""
    now = clock.utcnow()
    vault = repo.get_vault_for_update(db, tenant_id=tenant_id, vault_id=vault_id)
    if vault is None:
        raise VaultNotInitializedError()
    if vault.status == "DISABLED":
        raise VaultDisabledError()

    slot = repo.get_confirmed_recovery_slot(db, tenant_id=tenant_id, vault_id=vault_id)
    if slot is None:
        raise BadVaultPasswordError()

    params = crypto.Argon2Params(
        time_cost=slot.kdf_time_cost,
        memory_cost_kib=slot.kdf_memory_cost_kib,
        parallelism=slot.kdf_parallelism,
        hash_len=slot.kdf_hash_len,
    )
    kek = argon2_gate.derive_kek(recovery_code, slot.kdf_salt, params)
    aad = crypto.aad_wrapped_vault_root_key(
        slot_id=slot.id,
        tenant_id=tenant_id,
        vault_id=vault_id,
        user_id_or_recovery="RECOVERY",
        kdf_version=slot.kdf_version,
    )
    try:
        root_key = crypto.unwrap_vault_root_key(slot.wrapped_vault_root_key, kek, aad)
    except crypto.SolVaultDecryptionError as exc:
        raise BadVaultPasswordError() from exc

    previous_user_slot = repo.get_active_user_slot(
        db, tenant_id=tenant_id, vault_id=vault_id, user_id=actor.id
    )
    if previous_user_slot is not None:
        previous_user_slot.status = "REVOKED"
        previous_user_slot.revoked_at = now
        previous_user_slot.revoked_by = actor.id

    new_params = crypto.production_argon2_params()
    new_user_salt = crypto.generate_salt()
    new_user_kek = argon2_gate.derive_kek(new_personal_vault_password, new_user_salt, new_params)
    new_user_slot_id = uuid.uuid4()
    new_user_aad = crypto.aad_wrapped_vault_root_key(
        slot_id=new_user_slot_id,
        tenant_id=tenant_id,
        vault_id=vault_id,
        user_id_or_recovery=str(actor.id),
        kdf_version=crypto.PRODUCTION_ARGON2_KDF_VERSION,
    )
    new_user_slot = SolVaultKeySlot(
        id=new_user_slot_id,
        tenant_id=tenant_id,
        vault_id=vault_id,
        user_id=actor.id,
        kind="USER",
        kdf_salt=new_user_salt,
        kdf_time_cost=new_params.time_cost,
        kdf_memory_cost_kib=new_params.memory_cost_kib,
        kdf_parallelism=new_params.parallelism,
        kdf_hash_len=new_params.hash_len,
        kdf_version=crypto.PRODUCTION_ARGON2_KDF_VERSION,
        wrapped_vault_root_key=crypto.wrap_vault_root_key(root_key, new_user_kek, new_user_aad),
        status="ACTIVE",
    )
    db.add(new_user_slot)

    vault.status = "ACTIVE"
    vault.version += 1
    db.flush()

    registry.issue(
        vault_root_key=root_key,
        tenant_id=tenant_id,
        vault_id=vault_id,
        user_id=actor.id,
        app_session_id=app_session_id,
        allowed_operations=_allowed_operations_for(actor),
        authorized_client_ids=None,
        ttl_seconds=_DEFAULT_UNLOCK_TTL_SECONDS,
        now=now,
    )
    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=actor.id,
        action=AuditAction.SOL_VAULT_RECOVER,
        entity_type="sol_vault",
        entity_id=vault_id,
    )


# --- desbloqueo/bloqueo y contrasena personal ---


def unlock(
    db: Session,
    registry: VaultUnlockRegistry,
    *,
    tenant_id: uuid.UUID,
    vault_id: uuid.UUID,
    actor: User,
    app_session_id: uuid.UUID,
    personal_vault_password: str,
) -> None:
    now = clock.utcnow()
    slot = repo.get_active_user_slot(db, tenant_id=tenant_id, vault_id=vault_id, user_id=actor.id)
    if slot is None:
        raise BadVaultPasswordError()
    if slot.backoff_until is not None and slot.backoff_until > now:
        raise BadVaultPasswordError()

    params = crypto.Argon2Params(
        time_cost=slot.kdf_time_cost,
        memory_cost_kib=slot.kdf_memory_cost_kib,
        parallelism=slot.kdf_parallelism,
        hash_len=slot.kdf_hash_len,
    )
    kek = argon2_gate.derive_kek(personal_vault_password, slot.kdf_salt, params)
    aad = crypto.aad_wrapped_vault_root_key(
        slot_id=slot.id,
        tenant_id=tenant_id,
        vault_id=vault_id,
        user_id_or_recovery=str(actor.id),
        kdf_version=slot.kdf_version,
    )
    try:
        root_key = crypto.unwrap_vault_root_key(slot.wrapped_vault_root_key, kek, aad)
    except crypto.SolVaultDecryptionError as exc:
        slot.failed_attempts += 1
        slot.backoff_until = now + timedelta(seconds=min(2**slot.failed_attempts, 300))
        audit_service.record(
            db,
            tenant_id=tenant_id,
            user_id=actor.id,
            action=AuditAction.SOL_VAULT_UNLOCK_FAILED,
            entity_type="sol_vault",
            entity_id=vault_id,
        )
        raise BadVaultPasswordError() from exc

    slot.failed_attempts = 0
    slot.backoff_until = None
    slot.last_unlocked_at = now

    registry.issue(
        vault_root_key=root_key,
        tenant_id=tenant_id,
        vault_id=vault_id,
        user_id=actor.id,
        app_session_id=app_session_id,
        allowed_operations=_allowed_operations_for(actor),
        authorized_client_ids=None,
        ttl_seconds=_DEFAULT_UNLOCK_TTL_SECONDS,
        now=now,
    )
    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=actor.id,
        action=AuditAction.SOL_VAULT_UNLOCK_SUCCESS,
        entity_type="sol_vault",
        entity_id=vault_id,
    )


def lock(
    db: Session,
    registry: VaultUnlockRegistry,
    *,
    tenant_id: uuid.UUID,
    vault_id: uuid.UUID,
    actor: User,
    app_session_id: uuid.UUID,
) -> None:
    registry.lock(tenant_id=tenant_id, vault_id=vault_id, user_id=actor.id, app_session_id=app_session_id)
    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=actor.id,
        action=AuditAction.SOL_VAULT_LOCK,
        entity_type="sol_vault",
        entity_id=vault_id,
    )


def change_password(
    db: Session,
    registry: VaultUnlockRegistry,
    *,
    tenant_id: uuid.UUID,
    vault_id: uuid.UUID,
    actor: User,
    app_session_id: uuid.UUID,
    new_personal_vault_password: str,
) -> None:
    """El actor ya desbloqueado (autorizacion vigente en el registro)
    cambia su contrasena personal -- se re-envuelve la MISMA
    `vault_root_key` bajo un KEK nuevo, con una sal nueva."""
    now = clock.utcnow()
    entry = registry.resolve(
        db,
        tenant_id=tenant_id,
        vault_id=vault_id,
        user_id=actor.id,
        app_session_id=app_session_id,
        operation="SLOT_MANAGEMENT"
        if has_permission(actor, Permission.SOL_VAULT_MANAGE_USERS)
        else "CREDENTIAL_MANAGEMENT",
        now=now,
    )
    slot = repo.get_active_user_slot(db, tenant_id=tenant_id, vault_id=vault_id, user_id=actor.id)
    if slot is None:
        raise VaultNotInitializedError()

    params = crypto.production_argon2_params()
    new_salt = crypto.generate_salt()
    new_kek = argon2_gate.derive_kek(new_personal_vault_password, new_salt, params)
    new_aad = crypto.aad_wrapped_vault_root_key(
        slot_id=slot.id,
        tenant_id=tenant_id,
        vault_id=vault_id,
        user_id_or_recovery=str(actor.id),
        kdf_version=crypto.PRODUCTION_ARGON2_KDF_VERSION,
    )
    slot.kdf_salt = new_salt
    slot.kdf_time_cost = params.time_cost
    slot.kdf_memory_cost_kib = params.memory_cost_kib
    slot.kdf_parallelism = params.parallelism
    slot.kdf_hash_len = params.hash_len
    slot.kdf_version = crypto.PRODUCTION_ARGON2_KDF_VERSION
    slot.wrapped_vault_root_key = crypto.wrap_vault_root_key(bytes(entry.vault_root_key), new_kek, new_aad)
    db.flush()
    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=actor.id,
        action=AuditAction.SOL_VAULT_PASSWORD_CHANGE,
        entity_type="sol_vault_key_slot",
        entity_id=slot.id,
    )


def revoke_slot(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    vault_id: uuid.UUID,
    actor: User,
    slot_id: uuid.UUID,
    expected_version: int,
) -> None:
    now = clock.utcnow()
    vault = repo.get_vault_for_update(db, tenant_id=tenant_id, vault_id=vault_id)
    if vault is None:
        raise VaultNotInitializedError()
    if vault.version != expected_version:
        raise VersionConflictError()

    slot = repo.get_slot_for_update(db, tenant_id=tenant_id, slot_id=slot_id)
    if slot is None or slot.status != "ACTIVE" or slot.vault_id != vault_id:
        raise SolVaultServiceError("SOL_VAULT_SLOT_NOT_FOUND")

    if slot.kind == "USER":
        other_user_slots = [
            s
            for s in repo.list_active_user_slots(db, tenant_id=tenant_id, vault_id=vault_id)
            if s.id != slot_id
        ]
        if (
            not other_user_slots
            and repo.get_confirmed_recovery_slot(db, tenant_id=tenant_id, vault_id=vault_id) is None
        ):
            raise LastUserSlotError()
    else:
        remaining_user_slots = repo.list_active_user_slots(db, tenant_id=tenant_id, vault_id=vault_id)
        if not remaining_user_slots:
            raise LastRecoverySlotError()

    slot.status = "REVOKED"
    slot.revoked_at = now
    slot.revoked_by = actor.id
    db.flush()

    remaining_user_active = repo.list_active_user_slots(db, tenant_id=tenant_id, vault_id=vault_id)
    if not remaining_user_active and vault.status == "ACTIVE":
        vault.status = "RECOVERY_REQUIRED"

    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=actor.id,
        action=AuditAction.SOL_VAULT_SLOT_REVOKE,
        entity_type="sol_vault_key_slot",
        entity_id=slot_id,
    )


# --- rotacion ordinaria de credential_dek (Seccion 16) ---


def rotate_data_key(
    db: Session,
    registry: VaultUnlockRegistry,
    *,
    tenant_id: uuid.UUID,
    vault_id: uuid.UUID,
    actor: User,
    app_session_id: uuid.UUID,
    expected_version: int,
) -> int:
    now = clock.utcnow()
    entry = registry.resolve(
        db,
        tenant_id=tenant_id,
        vault_id=vault_id,
        user_id=actor.id,
        app_session_id=app_session_id,
        operation="DATA_KEY_ROTATION",
        now=now,
    )

    _advisory_lock(db, vault_id)  # paso 1

    vault = repo.get_vault_for_update(db, tenant_id=tenant_id, vault_id=vault_id)  # paso 2
    if vault is None:
        raise VaultNotInitializedError()

    old_data_key_row = repo.get_active_data_key(
        db, tenant_id=tenant_id, vault_id=vault_id, for_update=True
    )  # 3

    if vault.version != expected_version:  # paso 4
        raise VersionConflictError()

    old_credential_dek = _unwrap_credential_dek(bytes(entry.vault_root_key), old_data_key_row, tenant_id)  # 5
    new_credential_dek = crypto.generate_key()  # paso 6

    credentials = repo.list_credentials_for_rotation(db, tenant_id=tenant_id, vault_id=vault_id)  # paso 7

    old_version = old_data_key_row.data_key_version
    new_version = old_version + 1

    old_data_key_row.status = "RETIRED"  # paso 10 (RETIRED antes de insertar la nueva)
    old_data_key_row.retired_at = now
    db.flush()  # paso 11: libera el indice unico parcial ACTIVE

    new_data_key_row_id = uuid.uuid4()
    new_data_key_aad = crypto.aad_wrapped_data_key(
        data_key_row_id=new_data_key_row_id,
        tenant_id=tenant_id,
        vault_id=vault_id,
        data_key_version=new_version,
    )
    new_data_key_row = SolVaultDataKey(
        id=new_data_key_row_id,
        tenant_id=tenant_id,
        vault_id=vault_id,
        data_key_version=new_version,
        wrapped_data_key=crypto.wrap_data_key(
            new_credential_dek, bytes(entry.vault_root_key), new_data_key_aad
        ),
        status="ACTIVE",
        activated_at=now,
        created_by=actor.id,
        created_at=now,
    )
    db.add(new_data_key_row)  # paso 12
    db.flush()  # la fila debe existir antes de reasignar credenciales (FK compuesta)

    for credential in credentials:  # paso 8-9
        old_username_aad = crypto.aad_credential_field(
            credential_id=credential.id,
            tenant_id=tenant_id,
            client_id=credential.client_id,
            field_name="sol_username",
            data_key_version=old_version,
        )
        old_password_aad = crypto.aad_credential_field(
            credential_id=credential.id,
            tenant_id=tenant_id,
            client_id=credential.client_id,
            field_name="sol_password",
            data_key_version=old_version,
        )
        username = crypto.decrypt_credential_field(
            credential.sol_username_encrypted, old_credential_dek, old_username_aad
        )
        password = crypto.decrypt_credential_field(
            credential.sol_password_encrypted, old_credential_dek, old_password_aad
        )

        new_username_aad = crypto.aad_credential_field(
            credential_id=credential.id,
            tenant_id=tenant_id,
            client_id=credential.client_id,
            field_name="sol_username",
            data_key_version=new_version,
        )
        new_password_aad = crypto.aad_credential_field(
            credential_id=credential.id,
            tenant_id=tenant_id,
            client_id=credential.client_id,
            field_name="sol_password",
            data_key_version=new_version,
        )
        credential.sol_username_encrypted = crypto.encrypt_credential_field(
            username, new_credential_dek, new_username_aad
        )
        credential.sol_password_encrypted = crypto.encrypt_credential_field(
            password, new_credential_dek, new_password_aad
        )
        credential.data_key_version = new_version

    vault.version += 1  # paso 13
    db.flush()

    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=actor.id,
        action=AuditAction.SOL_VAULT_DATA_KEY_ROTATE,
        entity_type="sol_vault",
        entity_id=vault_id,
        metadata={"new_data_key_version": new_version, "rotated_count": len(credentials)},
    )
    return new_version  # paso 14: commit lo ejecuta el router


# --- invitaciones de dos actores (Seccion 15) ---


def create_invitation(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    vault_id: uuid.UUID,
    invited_by: User,
    target_user_id: uuid.UUID,
    ttl_seconds: int = 24 * 3600,
) -> SolVaultInvitation:
    now = clock.utcnow()
    existing = repo.get_pending_invitation_for_target(
        db, tenant_id=tenant_id, vault_id=vault_id, target_user_id=target_user_id
    )
    if existing is not None:
        if existing.expires_at <= now:
            existing.status = "EXPIRED"
            existing.closed_at = now
            db.flush()
        else:
            raise InvitationAlreadyPendingError()

    invitation = SolVaultInvitation(
        tenant_id=tenant_id,
        vault_id=vault_id,
        invited_by=invited_by.id,
        target_user_id=target_user_id,
        status="PENDING",
        expires_at=now + timedelta(seconds=ttl_seconds),
    )
    db.add(invitation)
    db.flush()
    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=invited_by.id,
        action=AuditAction.SOL_VAULT_SLOT_INVITE,
        entity_type="sol_vault_invitation",
        entity_id=invitation.id,
        metadata={"target_user_id": str(target_user_id)},
    )
    return invitation


def accept_invitation(
    db: Session,
    registry: VaultUnlockRegistry,
    association_registry: InvitationAssociationRegistry,
    *,
    tenant_id: uuid.UUID,
    vault_id: uuid.UUID,
    invitation_id: uuid.UUID,
    actor: User,
    new_personal_vault_password: str,
) -> SolVaultKeySlot:
    now = clock.utcnow()
    invitation = repo.get_invitation_for_update(db, tenant_id=tenant_id, invitation_id=invitation_id)
    if invitation is None:
        raise InvitationNotFoundError()
    if invitation.target_user_id != actor.id:
        raise InvitationWrongUserError()
    if invitation.status != "PENDING":
        raise InvitationExpiredError()
    if invitation.expires_at <= now:
        invitation.status = "EXPIRED"
        invitation.closed_at = now
        db.flush()
        raise InvitationExpiredError()

    inviter_handle = association_registry.get(invitation_id)
    if inviter_handle is None:
        raise InvitationInviterNotUnlockedError()

    inviter_entry = registry.get_entry(inviter_handle)
    if inviter_entry is None or inviter_entry.revoked or inviter_entry.expires_at <= now:
        raise InvitationInviterNotUnlockedError()
    if "SLOT_MANAGEMENT" not in inviter_entry.allowed_operations:
        raise InvitationInviterNotUnlockedError()

    params = crypto.production_argon2_params()
    new_salt = crypto.generate_salt()
    new_kek = argon2_gate.derive_kek(new_personal_vault_password, new_salt, params)
    new_slot_id = uuid.uuid4()
    new_aad = crypto.aad_wrapped_vault_root_key(
        slot_id=new_slot_id,
        tenant_id=tenant_id,
        vault_id=vault_id,
        user_id_or_recovery=str(actor.id),
        kdf_version=crypto.PRODUCTION_ARGON2_KDF_VERSION,
    )
    new_slot = SolVaultKeySlot(
        id=new_slot_id,
        tenant_id=tenant_id,
        vault_id=vault_id,
        user_id=actor.id,
        kind="USER",
        kdf_salt=new_salt,
        kdf_time_cost=params.time_cost,
        kdf_memory_cost_kib=params.memory_cost_kib,
        kdf_parallelism=params.parallelism,
        kdf_hash_len=params.hash_len,
        kdf_version=crypto.PRODUCTION_ARGON2_KDF_VERSION,
        wrapped_vault_root_key=crypto.wrap_vault_root_key(
            bytes(inviter_entry.vault_root_key), new_kek, new_aad
        ),
        status="ACTIVE",
    )
    db.add(new_slot)

    invitation.status = "ACCEPTED"
    invitation.accepted_at = now
    db.flush()

    association_registry.clear(invitation_id)
    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=actor.id,
        action=AuditAction.SOL_VAULT_SLOT_ADD,
        entity_type="sol_vault_key_slot",
        entity_id=new_slot_id,
        metadata={"invitation_id": str(invitation_id)},
    )
    return new_slot


def cancel_invitation(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor: User,
    invitation_id: uuid.UUID,
    expected_version: int,
) -> None:
    now = clock.utcnow()
    invitation = repo.get_invitation_for_update(db, tenant_id=tenant_id, invitation_id=invitation_id)
    if invitation is None:
        raise InvitationNotFoundError()
    if invitation.version != expected_version:
        raise VersionConflictError()
    if invitation.status != "PENDING":
        raise InvitationNotPendingError()
    invitation.status = "CANCELLED"
    invitation.closed_at = now
    invitation.version += 1
    db.flush()
    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=actor.id,
        action=AuditAction.SOL_VAULT_SLOT_INVITATION_CANCEL,
        entity_type="sol_vault_invitation",
        entity_id=invitation_id,
    )


def reauthorize_invitation(
    db: Session,
    registry: VaultUnlockRegistry,
    association_registry: InvitationAssociationRegistry,
    *,
    tenant_id: uuid.UUID,
    vault_id: uuid.UUID,
    actor: User,
    app_session_id: uuid.UUID,
    invitation_id: uuid.UUID,
    expected_version: int,
) -> None:
    now = clock.utcnow()
    invitation = repo.get_invitation_for_update(db, tenant_id=tenant_id, invitation_id=invitation_id)
    if invitation is None:
        raise InvitationNotFoundError()
    if invitation.invited_by != actor.id:
        raise InvitationWrongUserError()
    if invitation.version != expected_version:
        raise VersionConflictError()
    if invitation.status != "PENDING" or invitation.expires_at <= now:
        raise InvitationExpiredError()

    registry.resolve(
        db,
        tenant_id=tenant_id,
        vault_id=vault_id,
        user_id=actor.id,
        app_session_id=app_session_id,
        operation="SLOT_MANAGEMENT",
        now=now,
    )
    handle = registry.get_handle(
        tenant_id=tenant_id, vault_id=vault_id, user_id=actor.id, app_session_id=app_session_id
    )
    if handle is None:
        raise InvitationInviterNotUnlockedError()

    association_registry.associate(invitation_id, handle)
    invitation.version += 1
    db.flush()
    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=actor.id,
        action=AuditAction.SOL_VAULT_SLOT_INVITATION_REAUTHORIZE,
        entity_type="sol_vault_invitation",
        entity_id=invitation_id,
    )


class InvitationAssociationRegistry:
    """Asociacion efimera en memoria `invitation_id -> handle del
    invitador`. Vive junto al `VaultUnlockRegistry`; un reinicio de
    proceso la destruye igual que a el -- nunca se persiste."""

    def __init__(self) -> None:
        self._map: dict[uuid.UUID, str] = {}
        self._lock = threading.Lock()

    def associate(self, invitation_id: uuid.UUID, handle: str) -> None:
        with self._lock:
            self._map[invitation_id] = handle

    def get(self, invitation_id: uuid.UUID) -> str | None:
        with self._lock:
            return self._map.get(invitation_id)

    def clear(self, invitation_id: uuid.UUID) -> None:
        with self._lock:
            self._map.pop(invitation_id, None)

    def reset(self) -> None:
        with self._lock:
            self._map.clear()


invitation_association_registry = InvitationAssociationRegistry()
