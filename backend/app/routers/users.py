import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core import clock
from app.core.dependencies import get_client_ip, verify_csrf
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.models.audit_log import AuditAction
from app.models.user import User
from app.schemas.user import (
    ChangeRoleRequest,
    DeactivateRequest,
    ManagedUserOut,
    ResetPasswordRequest,
    UserCreateRequest,
)
from app.services import user_service
from app.services.audit_service import record as record_audit
from app.services.user_service import (
    DuplicateEmailError,
    LastActiveAdminError,
    NoRoleChangeError,
    SelfOperationNotAllowedError,
    UserAlreadyActiveError,
    UserNotFoundError,
)

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get("", response_model=list[ManagedUserOut])
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.USER_READ)),
) -> list[ManagedUserOut]:
    return user_service.list_users(db, current_user.tenant_id)


@router.post("", response_model=ManagedUserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.USER_MANAGE)),
    _csrf: None = Depends(verify_csrf),
) -> ManagedUserOut:
    try:
        user = user_service.create_user(
            db,
            current_user.tenant_id,
            email=payload.email,
            full_name=payload.full_name,
            role=payload.role,
            password=payload.password,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.USER_CREATE,
            entity_type="user",
            entity_id=user.id,
            metadata={"role": payload.role},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(user)
        return user
    except DuplicateEmailError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.patch("/{user_id}/role", response_model=ManagedUserOut)
def change_role(
    user_id: uuid.UUID,
    payload: ChangeRoleRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.USER_MANAGE)),
    _csrf: None = Depends(verify_csrf),
) -> ManagedUserOut:
    try:
        target, previous_role = user_service.change_role(
            db,
            current_user.tenant_id,
            current_user,
            user_id,
            new_role=payload.role,
            reason=payload.reason,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.USER_ROLE_CHANGE,
            entity_type="user",
            entity_id=target.id,
            metadata={"from_role": previous_role, "to_role": payload.role, "reason": payload.reason},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(target)
        return target
    except UserNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except NoRoleChangeError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except (SelfOperationNotAllowedError, LastActiveAdminError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.patch("/{user_id}/deactivate", response_model=ManagedUserOut)
def deactivate_user(
    user_id: uuid.UUID,
    payload: DeactivateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.USER_MANAGE)),
    _csrf: None = Depends(verify_csrf),
) -> ManagedUserOut:
    try:
        target = user_service.deactivate_user(
            db,
            current_user.tenant_id,
            current_user,
            user_id,
            now=clock.utcnow(),
            reason=payload.reason,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.USER_DEACTIVATE,
            entity_type="user",
            entity_id=target.id,
            metadata={"reason": payload.reason},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(target)
        return target
    except UserNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (SelfOperationNotAllowedError, LastActiveAdminError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.patch("/{user_id}/reactivate", response_model=ManagedUserOut)
def reactivate_user(
    user_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.USER_MANAGE)),
    _csrf: None = Depends(verify_csrf),
) -> ManagedUserOut:
    try:
        target = user_service.reactivate_user(db, current_user.tenant_id, current_user, user_id)
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.USER_REACTIVATE,
            entity_type="user",
            entity_id=target.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(target)
        return target
    except UserNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SelfOperationNotAllowedError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except UserAlreadyActiveError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.patch("/{user_id}/reset-password", response_model=ManagedUserOut)
def reset_password(
    user_id: uuid.UUID,
    payload: ResetPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.USER_MANAGE)),
    _csrf: None = Depends(verify_csrf),
) -> ManagedUserOut:
    try:
        target = user_service.reset_password(
            db,
            current_user.tenant_id,
            current_user,
            user_id,
            new_password=payload.new_password,
            reason=payload.reason,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.USER_PASSWORD_RESET,
            entity_type="user",
            entity_id=target.id,
            metadata={"reason": payload.reason},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(target)
        return target
    except UserNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SelfOperationNotAllowedError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
