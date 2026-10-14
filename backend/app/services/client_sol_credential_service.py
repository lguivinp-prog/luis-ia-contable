"""Credenciales SOL por cliente (Fase 5, Bloque 5.1, ver ADR-0068). Toda
alta/actualizacion aterriza siempre en `NEEDS_VALIDATION` -- el cambio a
`ACTIVE` requiere una validacion real contra SUNAT que este bloque NO
implementa. Ningun servicio de este modulo ejecuta `db.commit()` ni
`db.rollback()` (ADR-0037)."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import clock
from app.core import sol_vault_crypto as crypto
from app.core.sol_vault_registry import VaultUnlockRegistry
from app.models.audit_log import AuditAction
from app.models.client_sol_credential import ClientSolCredential
from app.models.user import User
from app.repositories import sol_vault_repository as repo
from app.services import audit_service
from app.services.sol_vault_service import RecoveryNotConfirmedError, VersionConflictError

__all__ = [
    "RecoveryNotConfirmedError",
    "VersionConflictError",
    "CredentialServiceError",
    "CredentialNotFoundError",
    "CredentialAlreadyDisabledError",
    "AuthorizationAlreadyExpiredError",
    "get_credential_status",
    "create_credential",
    "update_credential",
    "disable_credential",
]


def _advisory_lock(db: Session, vault_id: uuid.UUID) -> None:
    """Mismo advisory lock por boveda que `sol_vault_service._advisory_lock`
    (identico `pg_advisory_xact_lock(hashtext(vault_id))`) -- toda
    operacion que crea/actualiza una credencial debe serializarse contra
    una rotacion de data key concurrente de la misma boveda."""
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": str(vault_id)})


class CredentialServiceError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class CredentialNotFoundError(CredentialServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_CREDENTIAL_NOT_FOUND")


class CredentialAlreadyDisabledError(CredentialServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_CREDENTIAL_ALREADY_DISABLED")


class ProfileNameTakenError(CredentialServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_CREDENTIAL_PROFILE_NAME_TAKEN")


class PrimaryCredentialAlreadyExistsError(CredentialServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_PRIMARY_CREDENTIAL_ALREADY_EXISTS")


DEFAULT_PROFILE_NAME = {"SECONDARY": "Credencial secundaria", "PRIMARY_OWN_RUC": "Credencial principal", "PRIMARY_EXCEPTIONAL": "Credencial principal"}


class FieldTooLongError(CredentialServiceError):
    """Defensa en profundidad si otro llamador omite el esquema: nunca se llega al INSERT con un valor que la columna no admite."""

    def __init__(self) -> None:
        super().__init__("SOL_CREDENTIAL_FIELD_TOO_LONG")


# Longitudes reales de las columnas (`client_sol_credentials`).
FIELD_LIMITS = {"profile_name": 60, "authorization_basis": 60, "authorization_reference": 255}


def _check_field_lengths(**values: str | None) -> None:
    for field, limit in FIELD_LIMITS.items():
        value = values.get(field)
        if value is not None and len(value) > limit:
            raise FieldTooLongError()


class AuthorizationAlreadyExpiredError(CredentialServiceError):
    """La autorizacion (nueva o existente) esta vencida -- nunca se usa
    (crea/actualiza) una credencial en base a una autorizacion vencida sin
    que el actor la renueve explicitamente."""

    def __init__(self) -> None:
        super().__init__("SOL_CREDENTIAL_AUTHORIZATION_EXPIRED")


def get_credential_status(
    db: Session, *, tenant_id: uuid.UUID, client_id: uuid.UUID
) -> ClientSolCredential | None:
    return repo.get_active_credential_for_client(db, tenant_id=tenant_id, client_id=client_id)


def list_credentials(db: Session, *, tenant_id: uuid.UUID, client_id: uuid.UUID) -> list[ClientSolCredential]:
    return repo.list_active_credentials_for_client(db, tenant_id=tenant_id, client_id=client_id)


def _unwrap_credential_dek(db: Session, *, tenant_id: uuid.UUID, vault_id: uuid.UUID, vault_root_key: bytes):
    data_key_row = repo.get_active_data_key(db, tenant_id=tenant_id, vault_id=vault_id, for_update=True)
    aad = crypto.aad_wrapped_data_key(
        data_key_row_id=data_key_row.id,
        tenant_id=tenant_id,
        vault_id=vault_id,
        data_key_version=data_key_row.data_key_version,
    )
    credential_dek = crypto.unwrap_data_key(data_key_row.wrapped_data_key, vault_root_key, aad)
    return credential_dek, data_key_row


def create_credential(
    db: Session,
    registry: VaultUnlockRegistry,
    *,
    tenant_id: uuid.UUID,
    vault_id: uuid.UUID,
    client_id: uuid.UUID,
    actor: User,
    app_session_id: uuid.UUID,
    credential_kind: str,
    sol_username: str,
    sol_password: str,
    authorization_basis: str,
    authorization_expires_at=None,
    authorization_reference: str | None = None,
    authorized_scope_description: str | None = None,
    profile_name: str | None = None,
) -> ClientSolCredential:
    now = clock.utcnow()
    _check_field_lengths(profile_name=profile_name, authorization_basis=authorization_basis, authorization_reference=authorization_reference)
    if authorization_expires_at is not None and authorization_expires_at <= now:
        raise AuthorizationAlreadyExpiredError()
    name = (profile_name or DEFAULT_PROFILE_NAME.get(credential_kind, "Credencial")).strip()[:60]
    existing = repo.list_active_credentials_for_client(db, tenant_id=tenant_id, client_id=client_id)
    if any(c.profile_name.strip().lower() == name.lower() for c in existing):
        raise ProfileNameTakenError()
    if credential_kind != "SECONDARY" and any(c.credential_kind != "SECONDARY" for c in existing):
        raise PrimaryCredentialAlreadyExistsError()

    entry = registry.resolve(
        db,
        tenant_id=tenant_id,
        vault_id=vault_id,
        user_id=actor.id,
        app_session_id=app_session_id,
        operation="CREDENTIAL_MANAGEMENT",
        now=now,
        client_id=client_id,
    )

    if repo.get_confirmed_recovery_slot(db, tenant_id=tenant_id, vault_id=vault_id) is None:
        raise RecoveryNotConfirmedError()

    # Fe de erratas A.2 (Spike C): toda operacion que crea/actualiza una
    # credencial adquiere el mismo advisory lock por boveda antes de leer
    # la data key activa -- evita usar una version que una rotacion
    # concurrente esta a punto de retirar.
    _advisory_lock(db, vault_id)
    credential_dek, data_key_row = _unwrap_credential_dek(
        db, tenant_id=tenant_id, vault_id=vault_id, vault_root_key=bytes(entry.vault_root_key)
    )

    credential_id = uuid.uuid4()
    username_aad = crypto.aad_credential_field(
        credential_id=credential_id,
        tenant_id=tenant_id,
        client_id=client_id,
        field_name="sol_username",
        data_key_version=data_key_row.data_key_version,
    )
    password_aad = crypto.aad_credential_field(
        credential_id=credential_id,
        tenant_id=tenant_id,
        client_id=client_id,
        field_name="sol_password",
        data_key_version=data_key_row.data_key_version,
    )
    credential = ClientSolCredential(
        id=credential_id,
        tenant_id=tenant_id,
        client_id=client_id,
        vault_id=vault_id,
        credential_kind=credential_kind,
        profile_name=name,
        sol_username_encrypted=crypto.encrypt_credential_field(sol_username, credential_dek, username_aad),
        sol_password_encrypted=crypto.encrypt_credential_field(sol_password, credential_dek, password_aad),
        data_key_version=data_key_row.data_key_version,
        status="NEEDS_VALIDATION",
        authorization_basis=authorization_basis,
        authorized_at=now,
        authorization_expires_at=authorization_expires_at,
        authorization_reference=authorization_reference,
        authorized_scope_description=authorized_scope_description,
        created_by=actor.id,
    )
    db.add(credential)
    db.flush()
    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=actor.id,
        action=AuditAction.SOL_CREDENTIAL_CREATE,
        entity_type="client_sol_credential",
        entity_id=credential_id,
        metadata={"client_id": str(client_id), "credential_kind": credential_kind, "profile_name": name},
    )
    return credential


def update_credential(
    db: Session,
    registry: VaultUnlockRegistry,
    *,
    tenant_id: uuid.UUID,
    vault_id: uuid.UUID,
    client_id: uuid.UUID,
    credential_id: uuid.UUID,
    actor: User,
    app_session_id: uuid.UUID,
    expected_version: int,
    sol_username: str | None = None,
    sol_password: str | None = None,
    authorization_basis: str | None = None,
    authorization_expires_at=None,
    authorization_reference: str | None = None,
    authorized_scope_description: str | None = None,
    profile_name: str | None = None,
) -> ClientSolCredential:
    now = clock.utcnow()
    _check_field_lengths(profile_name=profile_name, authorization_basis=authorization_basis, authorization_reference=authorization_reference)
    entry = registry.resolve(
        db,
        tenant_id=tenant_id,
        vault_id=vault_id,
        user_id=actor.id,
        app_session_id=app_session_id,
        operation="CREDENTIAL_MANAGEMENT",
        now=now,
        client_id=client_id,
    )

    credential = repo.get_credential_for_update(
        db, tenant_id=tenant_id, client_id=client_id, credential_id=credential_id
    )
    if credential is None or credential.status == "DISABLED":
        raise CredentialNotFoundError()
    if credential.version != expected_version:
        raise VersionConflictError()

    renews_authorization = authorization_expires_at is not None or authorization_basis is not None
    if authorization_expires_at is not None and authorization_expires_at <= now:
        raise AuthorizationAlreadyExpiredError()
    if not renews_authorization and credential.authorization_expires_at is not None:
        if credential.authorization_expires_at <= now:
            raise AuthorizationAlreadyExpiredError()

    if sol_username is not None or sol_password is not None:
        _advisory_lock(db, vault_id)
        credential_dek, data_key_row = _unwrap_credential_dek(
            db, tenant_id=tenant_id, vault_id=vault_id, vault_root_key=bytes(entry.vault_root_key)
        )
        if sol_username is not None:
            username_aad = crypto.aad_credential_field(
                credential_id=credential_id,
                tenant_id=tenant_id,
                client_id=client_id,
                field_name="sol_username",
                data_key_version=data_key_row.data_key_version,
            )
            credential.sol_username_encrypted = crypto.encrypt_credential_field(
                sol_username, credential_dek, username_aad
            )
        if sol_password is not None:
            password_aad = crypto.aad_credential_field(
                credential_id=credential_id,
                tenant_id=tenant_id,
                client_id=client_id,
                field_name="sol_password",
                data_key_version=data_key_row.data_key_version,
            )
            credential.sol_password_encrypted = crypto.encrypt_credential_field(
                sol_password, credential_dek, password_aad
            )
        credential.data_key_version = data_key_row.data_key_version
        credential.status = "NEEDS_VALIDATION"

    if authorization_basis is not None:
        credential.authorization_basis = authorization_basis
        credential.authorized_at = now
    if authorization_expires_at is not None:
        credential.authorization_expires_at = authorization_expires_at
    if authorization_reference is not None:
        credential.authorization_reference = authorization_reference
    if authorized_scope_description is not None:
        credential.authorized_scope_description = authorized_scope_description
        credential.last_scope_reviewed_at = now

    if profile_name is not None:
        new_name = profile_name.strip()[:60]
        others = [c for c in repo.list_active_credentials_for_client(db, tenant_id=tenant_id, client_id=client_id) if c.id != credential_id]
        if not new_name or any(c.profile_name.strip().lower() == new_name.lower() for c in others):
            raise ProfileNameTakenError()
        credential.profile_name = new_name

    credential.updated_by = actor.id
    credential.version += 1
    db.flush()
    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=actor.id,
        action=AuditAction.SOL_CREDENTIAL_UPDATE,
        entity_type="client_sol_credential",
        entity_id=credential_id,
        metadata={"client_id": str(client_id)},
    )
    return credential


def disable_credential(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    client_id: uuid.UUID,
    credential_id: uuid.UUID,
    actor: User,
    expected_version: int,
) -> ClientSolCredential:
    now = clock.utcnow()
    credential = repo.get_credential_for_update(
        db, tenant_id=tenant_id, client_id=client_id, credential_id=credential_id
    )
    if credential is None:
        raise CredentialNotFoundError()
    if credential.status == "DISABLED":
        raise CredentialAlreadyDisabledError()
    if credential.version != expected_version:
        raise VersionConflictError()

    credential.status = "DISABLED"
    credential.disabled_at = now
    credential.disabled_by = actor.id
    credential.version += 1
    db.flush()
    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=actor.id,
        action=AuditAction.SOL_CREDENTIAL_DISABLE,
        entity_type="client_sol_credential",
        entity_id=credential_id,
        metadata={"client_id": str(client_id)},
    )
    return credential
