"""Fase 5, Bloque 5.1: boveda de credenciales SOL (ver ADR-0068 a
ADR-0071). Rutas estaticas antes de dinamicas. Ninguna ruta de este router
devuelve una contrasena en claro, un ciphertext, un salt, una clave
envuelta, ni el handle interno de `VaultUnlockRegistry`."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_session, verify_csrf
from app.core.permissions import Permission, require_permission
from app.core.sol_vault_registry import (
    VaultLockedError,
    VaultOperationNotAuthorizedError,
    VaultUnlockExpiredError,
    vault_unlock_registry,
)
from app.db.session import get_db
from app.models.session import Session as AppSession
from app.models.sol_vault import SolVaultInvitation, SolVaultKeySlot
from app.models.user import User
from app.repositories import client_repository
from app.repositories import sol_vault_repository as repo
from app.schemas.sol_vault import (
    AcceptInvitationRequest,
    ChangePasswordRequest,
    ConfirmRecoveryRequest,
    CreateInvitationRequest,
    CreateSolCredentialRequest,
    DisableSolCredentialRequest,
    ExpectedVersionRequest,
    InitializeVaultRequest,
    InitializeVaultResponse,
    InvitationOut,
    RecoverRequest,
    RecoveryReplaceResponse,
    RevokeSlotRequest,
    RotateDataKeyResponse,
    SimpleMessageResponse,
    SlotOut,
    SlotsListOut,
    SolCredentialStatusOut,
    SolVaultStatusOut,
    UnlockRequest,
    UpdateSolCredentialRequest,
)
from app.services import client_sol_credential_service
from app.services import sol_vault_service as svc

router = APIRouter(prefix="/api/v1/sol-vault", tags=["sol-vault"])
credential_router = APIRouter(prefix="/api/v1/clients", tags=["sol-vault-credentials"])


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _slot_out(slot: SolVaultKeySlot) -> SlotOut:
    return SlotOut(
        id=slot.id,
        kind=slot.kind,
        user_id=slot.user_id,
        status=slot.status,
        recovery_confirmed_at=slot.recovery_confirmed_at,
        last_unlocked_at=slot.last_unlocked_at,
        revoked_at=slot.revoked_at,
        created_at=slot.created_at,
    )


def _invitation_out(invitation: SolVaultInvitation) -> InvitationOut:
    return InvitationOut(
        id=invitation.id,
        vault_id=invitation.vault_id,
        invited_by=invitation.invited_by,
        target_user_id=invitation.target_user_id,
        status=invitation.status,
        expires_at=invitation.expires_at,
        accepted_at=invitation.accepted_at,
        version=invitation.version,
        created_at=invitation.created_at,
    )


def _credential_out(credential) -> SolCredentialStatusOut:
    return SolCredentialStatusOut(
        id=credential.id,
        client_id=credential.client_id,
        credential_kind=credential.credential_kind,
        profile_name=credential.profile_name,
        status=credential.status,
        last_validation_result=credential.last_validation_result,
        last_validated_at=credential.last_validated_at,
        last_used_at=credential.last_used_at,
        authorization_basis=credential.authorization_basis,
        authorized_at=credential.authorized_at,
        authorization_expires_at=credential.authorization_expires_at,
        authorization_reference=credential.authorization_reference,
        authorized_scope_description=credential.authorized_scope_description,
        version=credential.version,
        created_at=credential.created_at,
        updated_at=credential.updated_at,
    )


def _require_active_vault(db: Session, tenant_id: uuid.UUID) -> uuid.UUID:
    vault = svc.get_vault_status(db, tenant_id=tenant_id)
    if vault is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"code": "SOL_VAULT_NOT_INITIALIZED"}
        )
    return vault.id


_SERVICE_ERROR_STATUS = {
    "SOL_VAULT_ALREADY_INITIALIZED": status.HTTP_409_CONFLICT,
    "SOL_VAULT_NOT_INITIALIZED": status.HTTP_404_NOT_FOUND,
    "SOL_VAULT_DISABLED": status.HTTP_409_CONFLICT,
    "SOL_VAULT_BAD_PASSWORD": status.HTTP_401_UNAUTHORIZED,
    "VERSION_CONFLICT": status.HTTP_409_CONFLICT,
    "SOL_VAULT_RECOVERY_NOT_CONFIRMED": status.HTTP_409_CONFLICT,
    "SOL_VAULT_LAST_USER_SLOT": status.HTTP_409_CONFLICT,
    "SOL_VAULT_LAST_RECOVERY_SLOT": status.HTTP_409_CONFLICT,
    "SOL_VAULT_RECOVERY_REPLACEMENT_ALREADY_PENDING": status.HTTP_409_CONFLICT,
    "SOL_VAULT_INVITATION_NOT_FOUND": status.HTTP_404_NOT_FOUND,
    "SOL_VAULT_INVITATION_INVITER_NOT_UNLOCKED": status.HTTP_409_CONFLICT,
    "SOL_VAULT_INVITATION_WRONG_USER": status.HTTP_403_FORBIDDEN,
    "SOL_VAULT_INVITATION_EXPIRED": status.HTTP_409_CONFLICT,
    "SOL_VAULT_INVITATION_ALREADY_PENDING": status.HTTP_409_CONFLICT,
    "SOL_VAULT_INVITATION_NOT_PENDING": status.HTTP_409_CONFLICT,
    "SOL_VAULT_SLOT_NOT_FOUND": status.HTTP_404_NOT_FOUND,
    "SOL_CREDENTIAL_NOT_FOUND": status.HTTP_404_NOT_FOUND,
    "SOL_CREDENTIAL_ALREADY_DISABLED": status.HTTP_409_CONFLICT,
    "SOL_CREDENTIAL_PROFILE_NAME_TAKEN": status.HTTP_409_CONFLICT,
    "SOL_PRIMARY_CREDENTIAL_ALREADY_EXISTS": status.HTTP_409_CONFLICT,
    "SOL_CREDENTIAL_AUTHORIZATION_EXPIRED": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "SOL_CREDENTIAL_FIELD_TOO_LONG": status.HTTP_422_UNPROCESSABLE_ENTITY,
}

# Restricciones de base de datos -> codigo normalizado. Una carrera (doble clic / reintento) NO debe terminar en un 500 opaco.
_CREDENTIAL_CONSTRAINT_CODES = {
    "ux_client_sol_credentials_active_profile": ("SOL_CREDENTIAL_PROFILE_NAME_TAKEN", status.HTTP_409_CONFLICT),
    "ux_client_sol_credentials_one_primary": ("SOL_PRIMARY_CREDENTIAL_ALREADY_EXISTS", status.HTTP_409_CONFLICT),
}


def _handle_database_error(db: Session, exc: Exception) -> HTTPException:
    """Traduce un fallo de base de datos al guardar una credencial a una respuesta controlada. No incluye SQL, parametros ni cifrado; la transaccion se
    revierte entera (no queda ningun perfil a medias)."""
    db.rollback()
    constraint = getattr(getattr(getattr(exc, "orig", None), "diag", None), "constraint_name", None)
    if constraint in _CREDENTIAL_CONSTRAINT_CODES:
        code, http_status = _CREDENTIAL_CONSTRAINT_CODES[constraint]
        return HTTPException(status_code=http_status, detail={"code": code})
    kind = type(getattr(exc, "orig", exc)).__name__
    logging.getLogger("app.sol_vault").error("credential_store_failed", extra={"error_kind": kind})
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"code": "SOL_CREDENTIAL_STORE_FAILED"})


def _handle_service_error(db: Session, exc: svc.SolVaultServiceError) -> HTTPException:
    db.rollback()
    return HTTPException(
        status_code=_SERVICE_ERROR_STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST),
        detail={"code": exc.code},
    )


def _handle_registry_error(db: Session, exc: Exception) -> HTTPException:
    db.rollback()
    if isinstance(exc, VaultLockedError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "SOL_VAULT_LOCKED"})
    if isinstance(exc, VaultUnlockExpiredError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"code": "SOL_VAULT_UNLOCK_EXPIRED"}
        )
    if isinstance(exc, VaultOperationNotAuthorizedError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail={"code": "SOL_VAULT_OPERATION_NOT_AUTHORIZED"}
        )
    raise exc


# --- estado ---


@router.get("/status", response_model=SolVaultStatusOut)
def get_status(
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_VAULT_UNLOCK)),
    session: AppSession = Depends(get_current_session),
) -> SolVaultStatusOut:
    _no_store(response)
    vault = svc.get_vault_status(db, tenant_id=current_user.tenant_id)
    if vault is None:
        return SolVaultStatusOut(
            initialized=False, status="NOT_INITIALIZED", unlocked=False, recovery_confirmed=False
        )
    handle = vault_unlock_registry.get_handle(
        tenant_id=current_user.tenant_id,
        vault_id=vault.id,
        user_id=current_user.id,
        app_session_id=session.id,
    )
    recovery_confirmed = (
        repo.get_confirmed_recovery_slot(db, tenant_id=current_user.tenant_id, vault_id=vault.id) is not None
    )
    return SolVaultStatusOut(
        initialized=True,
        vault_id=vault.id,
        status=vault.status,
        unlocked=handle is not None,
        recovery_confirmed=recovery_confirmed,
        version=vault.version,
    )


# --- inicializacion y recuperacion ---


@router.post("/initialize", response_model=InitializeVaultResponse)
def initialize(
    payload: InitializeVaultRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_VAULT_INITIALIZE)),
    session: AppSession = Depends(get_current_session),
    _csrf: None = Depends(verify_csrf),
) -> InitializeVaultResponse:
    _no_store(response)
    try:
        result = svc.initialize_vault(
            db,
            vault_unlock_registry,
            tenant_id=current_user.tenant_id,
            actor=current_user,
            app_session_id=session.id,
            personal_vault_password=payload.personal_vault_password,
        )
    except svc.SolVaultServiceError as exc:
        raise _handle_service_error(db, exc) from exc
    db.commit()
    return InitializeVaultResponse(
        vault_id=result.vault.id, status=result.vault.status, recovery_code=result.recovery_code
    )


@router.post("/recovery/confirm", response_model=SimpleMessageResponse)
def recovery_confirm(
    payload: ConfirmRecoveryRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_VAULT_UNLOCK)),
    _csrf: None = Depends(verify_csrf),
) -> SimpleMessageResponse:
    _no_store(response)
    vault_id = _require_active_vault(db, current_user.tenant_id)
    try:
        svc.confirm_recovery(
            db, tenant_id=current_user.tenant_id, vault_id=vault_id, recovery_code=payload.recovery_code
        )
    except svc.SolVaultServiceError as exc:
        raise _handle_service_error(db, exc) from exc
    db.commit()
    return SimpleMessageResponse(message="Código de recuperación confirmado")


@router.post("/recovery/replace", response_model=RecoveryReplaceResponse)
def recovery_replace(
    payload: ExpectedVersionRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_VAULT_RECOVER)),
    session: AppSession = Depends(get_current_session),
    _csrf: None = Depends(verify_csrf),
) -> RecoveryReplaceResponse:
    _no_store(response)
    vault_id = _require_active_vault(db, current_user.tenant_id)
    try:
        new_code = svc.replace_recovery(
            db,
            vault_unlock_registry,
            tenant_id=current_user.tenant_id,
            vault_id=vault_id,
            actor=current_user,
            app_session_id=session.id,
            expected_version=payload.expected_version,
        )
    except svc.SolVaultServiceError as exc:
        raise _handle_service_error(db, exc) from exc
    except (VaultLockedError, VaultUnlockExpiredError, VaultOperationNotAuthorizedError) as exc:
        raise _handle_registry_error(db, exc) from exc
    db.commit()
    return RecoveryReplaceResponse(new_recovery_code=new_code)


@router.post("/recover", response_model=SimpleMessageResponse)
def recover(
    payload: RecoverRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_VAULT_RECOVER)),
    session: AppSession = Depends(get_current_session),
    _csrf: None = Depends(verify_csrf),
) -> SimpleMessageResponse:
    _no_store(response)
    vault_id = _require_active_vault(db, current_user.tenant_id)
    try:
        svc.recover(
            db,
            vault_unlock_registry,
            tenant_id=current_user.tenant_id,
            vault_id=vault_id,
            actor=current_user,
            app_session_id=session.id,
            recovery_code=payload.recovery_code,
            new_personal_vault_password=payload.new_personal_vault_password,
        )
    except svc.SolVaultServiceError as exc:
        raise _handle_service_error(db, exc) from exc
    db.commit()
    return SimpleMessageResponse(message="Acceso recuperado correctamente")


# --- desbloqueo/bloqueo y contrasena personal ---


@router.post("/unlock", response_model=SimpleMessageResponse)
def unlock(
    payload: UnlockRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_VAULT_UNLOCK)),
    session: AppSession = Depends(get_current_session),
    _csrf: None = Depends(verify_csrf),
) -> SimpleMessageResponse:
    _no_store(response)
    vault_id = _require_active_vault(db, current_user.tenant_id)
    try:
        svc.unlock(
            db,
            vault_unlock_registry,
            tenant_id=current_user.tenant_id,
            vault_id=vault_id,
            actor=current_user,
            app_session_id=session.id,
            personal_vault_password=payload.personal_vault_password,
        )
    except svc.SolVaultServiceError as exc:
        raise _handle_service_error(db, exc) from exc
    db.commit()
    return SimpleMessageResponse(message="Bóveda desbloqueada")


@router.post("/lock", response_model=SimpleMessageResponse)
def lock(
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_VAULT_UNLOCK)),
    session: AppSession = Depends(get_current_session),
    _csrf: None = Depends(verify_csrf),
) -> SimpleMessageResponse:
    _no_store(response)
    vault_id = _require_active_vault(db, current_user.tenant_id)
    svc.lock(
        db,
        vault_unlock_registry,
        tenant_id=current_user.tenant_id,
        vault_id=vault_id,
        actor=current_user,
        app_session_id=session.id,
    )
    db.commit()
    return SimpleMessageResponse(message="Bóveda bloqueada")


@router.post("/change-password", response_model=SimpleMessageResponse)
def change_password(
    payload: ChangePasswordRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_VAULT_UNLOCK)),
    session: AppSession = Depends(get_current_session),
    _csrf: None = Depends(verify_csrf),
) -> SimpleMessageResponse:
    _no_store(response)
    vault_id = _require_active_vault(db, current_user.tenant_id)
    try:
        svc.change_password(
            db,
            vault_unlock_registry,
            tenant_id=current_user.tenant_id,
            vault_id=vault_id,
            actor=current_user,
            app_session_id=session.id,
            new_personal_vault_password=payload.new_personal_vault_password,
        )
    except svc.SolVaultServiceError as exc:
        raise _handle_service_error(db, exc) from exc
    except (VaultLockedError, VaultUnlockExpiredError, VaultOperationNotAuthorizedError) as exc:
        raise _handle_registry_error(db, exc) from exc
    db.commit()
    return SimpleMessageResponse(message="Contraseña de bóveda actualizada")


# --- rotacion ---


@router.post("/data-key/rotate", response_model=RotateDataKeyResponse)
def rotate_data_key(
    payload: ExpectedVersionRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_VAULT_ROTATE)),
    session: AppSession = Depends(get_current_session),
    _csrf: None = Depends(verify_csrf),
) -> RotateDataKeyResponse:
    _no_store(response)
    vault_id = _require_active_vault(db, current_user.tenant_id)
    try:
        new_version = svc.rotate_data_key(
            db,
            vault_unlock_registry,
            tenant_id=current_user.tenant_id,
            vault_id=vault_id,
            actor=current_user,
            app_session_id=session.id,
            expected_version=payload.expected_version,
        )
    except svc.SolVaultServiceError as exc:
        raise _handle_service_error(db, exc) from exc
    except (VaultLockedError, VaultUnlockExpiredError, VaultOperationNotAuthorizedError) as exc:
        raise _handle_registry_error(db, exc) from exc
    db.commit()
    return RotateDataKeyResponse(new_data_key_version=new_version)


# --- slots ---


@router.get("/slots", response_model=SlotsListOut)
def list_slots(
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_VAULT_MANAGE_USERS)),
) -> SlotsListOut:
    _no_store(response)
    vault_id = _require_active_vault(db, current_user.tenant_id)
    slots = repo.list_slots(db, tenant_id=current_user.tenant_id, vault_id=vault_id)
    return SlotsListOut(items=[_slot_out(s) for s in slots])


@router.post("/slots/{slot_id}/revoke", response_model=SimpleMessageResponse)
def revoke_slot(
    slot_id: uuid.UUID,
    payload: RevokeSlotRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_VAULT_MANAGE_USERS)),
    _csrf: None = Depends(verify_csrf),
) -> SimpleMessageResponse:
    _no_store(response)
    vault_id = _require_active_vault(db, current_user.tenant_id)
    try:
        svc.revoke_slot(
            db,
            tenant_id=current_user.tenant_id,
            vault_id=vault_id,
            actor=current_user,
            slot_id=slot_id,
            expected_version=payload.expected_version,
        )
    except svc.SolVaultServiceError as exc:
        raise _handle_service_error(db, exc) from exc
    db.commit()
    return SimpleMessageResponse(message="Slot revocado")


# --- invitaciones ---


@router.post("/invitations", response_model=InvitationOut)
def create_invitation(
    payload: CreateInvitationRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_VAULT_MANAGE_USERS)),
    _csrf: None = Depends(verify_csrf),
) -> InvitationOut:
    _no_store(response)
    vault_id = _require_active_vault(db, current_user.tenant_id)
    if payload.target_user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "SOL_VAULT_INVITATION_CANNOT_TARGET_SELF"},
        )
    try:
        invitation = svc.create_invitation(
            db,
            tenant_id=current_user.tenant_id,
            vault_id=vault_id,
            invited_by=current_user,
            target_user_id=payload.target_user_id,
        )
    except svc.SolVaultServiceError as exc:
        raise _handle_service_error(db, exc) from exc
    db.commit()
    return _invitation_out(invitation)


@router.post("/invitations/{invitation_id}/accept", response_model=SimpleMessageResponse)
def accept_invitation(
    invitation_id: uuid.UUID,
    payload: AcceptInvitationRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_VAULT_UNLOCK)),
    _csrf: None = Depends(verify_csrf),
) -> SimpleMessageResponse:
    _no_store(response)
    vault_id = _require_active_vault(db, current_user.tenant_id)
    try:
        svc.accept_invitation(
            db,
            vault_unlock_registry,
            svc.invitation_association_registry,
            tenant_id=current_user.tenant_id,
            vault_id=vault_id,
            invitation_id=invitation_id,
            actor=current_user,
            new_personal_vault_password=payload.new_personal_vault_password,
        )
    except svc.SolVaultServiceError as exc:
        raise _handle_service_error(db, exc) from exc
    db.commit()
    return SimpleMessageResponse(message="Invitación aceptada")


@router.post("/invitations/{invitation_id}/reauthorize", response_model=SimpleMessageResponse)
def reauthorize_invitation(
    invitation_id: uuid.UUID,
    payload: ExpectedVersionRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_VAULT_MANAGE_USERS)),
    session: AppSession = Depends(get_current_session),
    _csrf: None = Depends(verify_csrf),
) -> SimpleMessageResponse:
    _no_store(response)
    vault_id = _require_active_vault(db, current_user.tenant_id)
    try:
        svc.reauthorize_invitation(
            db,
            vault_unlock_registry,
            svc.invitation_association_registry,
            tenant_id=current_user.tenant_id,
            vault_id=vault_id,
            actor=current_user,
            app_session_id=session.id,
            invitation_id=invitation_id,
            expected_version=payload.expected_version,
        )
    except svc.SolVaultServiceError as exc:
        raise _handle_service_error(db, exc) from exc
    except (VaultLockedError, VaultUnlockExpiredError, VaultOperationNotAuthorizedError) as exc:
        raise _handle_registry_error(db, exc) from exc
    db.commit()
    return SimpleMessageResponse(message="Invitación reautorizada")


@router.post("/invitations/{invitation_id}/cancel", response_model=SimpleMessageResponse)
def cancel_invitation(
    invitation_id: uuid.UUID,
    payload: ExpectedVersionRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_VAULT_MANAGE_USERS)),
    _csrf: None = Depends(verify_csrf),
) -> SimpleMessageResponse:
    _no_store(response)
    _require_active_vault(db, current_user.tenant_id)
    try:
        svc.cancel_invitation(
            db,
            tenant_id=current_user.tenant_id,
            actor=current_user,
            invitation_id=invitation_id,
            expected_version=payload.expected_version,
        )
    except svc.SolVaultServiceError as exc:
        raise _handle_service_error(db, exc) from exc
    db.commit()
    return SimpleMessageResponse(message="Invitación cancelada")


# --- credenciales SOL por cliente ---


def _require_client(db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID):
    client = client_repository.get_by_id(db, tenant_id, client_id)
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "CLIENT_NOT_FOUND"})
    return client


def _single_credential_or_409(db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID):
    """Rutas M2 sin `credential_id`: solo valen con UN perfil. Con varios se exige seleccion explicita (jamas se elige uno por el usuario)."""
    found = client_sol_credential_service.list_credentials(db, tenant_id=tenant_id, client_id=client_id)
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "SOL_CREDENTIAL_NOT_FOUND"})
    if len(found) > 1:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "SOL_CREDENTIAL_SELECTION_REQUIRED"})
    return found[0]


@credential_router.get("/{client_id}/sol-credential/status", response_model=SolCredentialStatusOut | None)
def get_credential_status(
    client_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_CREDENTIAL_READ)),
):
    _no_store(response)
    _require_client(db, current_user.tenant_id, client_id)
    credential = client_sol_credential_service.get_credential_status(
        db, tenant_id=current_user.tenant_id, client_id=client_id
    )
    return _credential_out(credential) if credential is not None else None


@credential_router.post("/{client_id}/sol-credentials", response_model=SolCredentialStatusOut)
@credential_router.post("/{client_id}/sol-credential", response_model=SolCredentialStatusOut)
def create_credential(
    client_id: uuid.UUID,
    payload: CreateSolCredentialRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_CREDENTIAL_CREATE)),
    session: AppSession = Depends(get_current_session),
    _csrf: None = Depends(verify_csrf),
) -> SolCredentialStatusOut:
    _no_store(response)
    _require_client(db, current_user.tenant_id, client_id)
    vault_id = _require_active_vault(db, current_user.tenant_id)
    try:
        credential = client_sol_credential_service.create_credential(
            db,
            vault_unlock_registry,
            tenant_id=current_user.tenant_id,
            vault_id=vault_id,
            client_id=client_id,
            actor=current_user,
            app_session_id=session.id,
            credential_kind=payload.credential_kind,
            profile_name=payload.profile_name,
            sol_username=payload.sol_username,
            sol_password=payload.sol_password,
            authorization_basis=payload.authorization_basis,
            authorization_expires_at=payload.authorization_expires_at,
            authorization_reference=payload.authorization_reference,
            authorized_scope_description=payload.authorized_scope_description,
        )
        db.commit()
    except client_sol_credential_service.CredentialServiceError as exc:
        raise _handle_service_error(db, exc) from exc
    except svc.SolVaultServiceError as exc:
        raise _handle_service_error(db, exc) from exc
    except (VaultLockedError, VaultUnlockExpiredError, VaultOperationNotAuthorizedError) as exc:
        raise _handle_registry_error(db, exc) from exc
    except DBAPIError as exc:
        raise _handle_database_error(db, exc) from exc
    return _credential_out(credential)


@credential_router.put("/{client_id}/sol-credential", response_model=SolCredentialStatusOut)
def update_credential(
    client_id: uuid.UUID,
    payload: UpdateSolCredentialRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_CREDENTIAL_UPDATE)),
    session: AppSession = Depends(get_current_session),
    _csrf: None = Depends(verify_csrf),
) -> SolCredentialStatusOut:
    _no_store(response)
    _require_client(db, current_user.tenant_id, client_id)
    vault_id = _require_active_vault(db, current_user.tenant_id)
    existing = _single_credential_or_409(db, current_user.tenant_id, client_id)
    try:
        credential = client_sol_credential_service.update_credential(
            db,
            vault_unlock_registry,
            tenant_id=current_user.tenant_id,
            vault_id=vault_id,
            client_id=client_id,
            credential_id=existing.id,
            actor=current_user,
            app_session_id=session.id,
            expected_version=payload.expected_version,
            profile_name=payload.profile_name,
            sol_username=payload.sol_username,
            sol_password=payload.sol_password,
            authorization_basis=payload.authorization_basis,
            authorization_expires_at=payload.authorization_expires_at,
            authorization_reference=payload.authorization_reference,
            authorized_scope_description=payload.authorized_scope_description,
        )
    except client_sol_credential_service.CredentialServiceError as exc:
        raise _handle_service_error(db, exc) from exc
    except svc.SolVaultServiceError as exc:
        raise _handle_service_error(db, exc) from exc
    except (VaultLockedError, VaultUnlockExpiredError, VaultOperationNotAuthorizedError) as exc:
        raise _handle_registry_error(db, exc) from exc
    except DBAPIError as exc:
        raise _handle_database_error(db, exc) from exc
    try:
        db.commit()
    except DBAPIError as exc:
        raise _handle_database_error(db, exc) from exc
    return _credential_out(credential)


@credential_router.post("/{client_id}/sol-credential/disable", response_model=SolCredentialStatusOut)
def disable_credential(
    client_id: uuid.UUID,
    payload: DisableSolCredentialRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_CREDENTIAL_DISABLE)),
    _csrf: None = Depends(verify_csrf),
) -> SolCredentialStatusOut:
    _no_store(response)
    _require_client(db, current_user.tenant_id, client_id)
    existing = _single_credential_or_409(db, current_user.tenant_id, client_id)
    try:
        credential = client_sol_credential_service.disable_credential(
            db,
            tenant_id=current_user.tenant_id,
            client_id=client_id,
            credential_id=existing.id,
            actor=current_user,
            expected_version=payload.expected_version,
        )
    except client_sol_credential_service.CredentialServiceError as exc:
        raise _handle_service_error(db, exc) from exc
    db.commit()
    return _credential_out(credential)


# --- M3: perfiles de credencial (varias credenciales cifradas por cliente; seleccion EXPLICITA por id) ---


@credential_router.get("/{client_id}/sol-credentials", response_model=list[SolCredentialStatusOut])
def list_credential_profiles(
    client_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_CREDENTIAL_READ)),
) -> list[SolCredentialStatusOut]:
    _no_store(response)
    _require_client(db, current_user.tenant_id, client_id)
    return [_credential_out(c) for c in client_sol_credential_service.list_credentials(db, tenant_id=current_user.tenant_id, client_id=client_id)]


def _profile_or_404(db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID, credential_id: uuid.UUID):
    for c in client_sol_credential_service.list_credentials(db, tenant_id=tenant_id, client_id=client_id):
        if c.id == credential_id:
            return c
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "SOL_CREDENTIAL_NOT_FOUND"})


@credential_router.put("/{client_id}/sol-credentials/{credential_id}", response_model=SolCredentialStatusOut)
def update_credential_profile(
    client_id: uuid.UUID,
    credential_id: uuid.UUID,
    payload: UpdateSolCredentialRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_CREDENTIAL_UPDATE)),
    session: AppSession = Depends(get_current_session),
    _csrf: None = Depends(verify_csrf),
) -> SolCredentialStatusOut:
    _no_store(response)
    _require_client(db, current_user.tenant_id, client_id)
    vault_id = _require_active_vault(db, current_user.tenant_id)
    _profile_or_404(db, current_user.tenant_id, client_id, credential_id)
    try:
        credential = client_sol_credential_service.update_credential(
            db, vault_unlock_registry, tenant_id=current_user.tenant_id, vault_id=vault_id, client_id=client_id, credential_id=credential_id,
            actor=current_user, app_session_id=session.id, expected_version=payload.expected_version, profile_name=payload.profile_name,
            sol_username=payload.sol_username, sol_password=payload.sol_password, authorization_basis=payload.authorization_basis,
            authorization_expires_at=payload.authorization_expires_at, authorization_reference=payload.authorization_reference,
            authorized_scope_description=payload.authorized_scope_description,
        )
    except client_sol_credential_service.CredentialServiceError as exc:
        raise _handle_service_error(db, exc) from exc
    except svc.SolVaultServiceError as exc:
        raise _handle_service_error(db, exc) from exc
    except (VaultLockedError, VaultUnlockExpiredError, VaultOperationNotAuthorizedError) as exc:
        raise _handle_registry_error(db, exc) from exc
    except DBAPIError as exc:
        raise _handle_database_error(db, exc) from exc
    try:
        db.commit()
    except DBAPIError as exc:
        raise _handle_database_error(db, exc) from exc
    return _credential_out(credential)


@credential_router.post("/{client_id}/sol-credentials/{credential_id}/disable", response_model=SolCredentialStatusOut)
def disable_credential_profile(
    client_id: uuid.UUID,
    credential_id: uuid.UUID,
    payload: DisableSolCredentialRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_CREDENTIAL_DISABLE)),
    _csrf: None = Depends(verify_csrf),
) -> SolCredentialStatusOut:
    """Deja de usar UN perfil sin tocar los demas (los otros perfiles conservan su cifrado, estado y version)."""
    _no_store(response)
    _require_client(db, current_user.tenant_id, client_id)
    _profile_or_404(db, current_user.tenant_id, client_id, credential_id)
    try:
        credential = client_sol_credential_service.disable_credential(
            db, tenant_id=current_user.tenant_id, client_id=client_id, credential_id=credential_id, actor=current_user,
            expected_version=payload.expected_version,
        )
    except client_sol_credential_service.CredentialServiceError as exc:
        raise _handle_service_error(db, exc) from exc
    db.commit()
    return _credential_out(credential)
