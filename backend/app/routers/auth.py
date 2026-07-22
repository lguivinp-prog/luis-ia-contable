from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.dependencies import get_client_ip, get_current_user
from app.db.session import get_db
from app.models.audit_log import AuditAction
from app.models.user import User
from app.repositories import tenant_repository, user_repository
from app.schemas.auth import LoginRequest, UserOut
from app.services import audit_service, auth_service
from app.services.auth_service import InvalidCredentialsError

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
settings = get_settings()


@router.post("/login", response_model=UserOut)
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> User:
    ip_address = get_client_ip(request)
    user_agent = request.headers.get("user-agent")
    existing_user = user_repository.get_by_email(db, payload.email)

    try:
        user = auth_service.authenticate(db, payload.email, payload.password)
    except InvalidCredentialsError:
        tenant_id = existing_user.tenant_id if existing_user else None
        if tenant_id is None:
            default_tenant = tenant_repository.get_default(db)
            tenant_id = default_tenant.id if default_tenant else None
        if tenant_id is not None:
            audit_service.record(
                db,
                tenant_id=tenant_id,
                user_id=existing_user.id if existing_user else None,
                action=AuditAction.LOGIN_FAILURE,
                metadata={"email": payload.email},
                ip_address=ip_address,
                user_agent=user_agent,
            )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Correo o contraseña incorrectos"
        ) from None

    raw_token, _session = auth_service.create_session(
        db, user, ip_address=ip_address, user_agent=user_agent
    )
    audit_service.record(
        db,
        tenant_id=user.tenant_id,
        user_id=user.id,
        action=AuditAction.LOGIN_SUCCESS,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.commit()

    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )
    return user


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token:
        session = auth_service.get_valid_session(db, raw_token)
        if session is not None:
            auth_service.revoke_session(db, session)

    audit_service.record(
        db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        action=AuditAction.LOGOUT,
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()

    response.delete_cookie(key=settings.session_cookie_name, path="/")
    return {"status": "ok"}


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
