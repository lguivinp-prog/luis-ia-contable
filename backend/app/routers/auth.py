from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.core.dependencies import get_client_ip, get_current_session, get_current_user, verify_csrf
from app.core.identifier import InvalidIdentifierError, normalize_login_identifier
from app.db.session import get_db
from app.models.audit_log import AuditAction
from app.models.security_event import SecurityEventSeverity, SecurityEventType
from app.models.session import Session as SessionModel
from app.models.user import User
from app.schemas.auth import (
    ChangePasswordRequest,
    KeepAliveOut,
    LoginRequest,
    SessionOut,
    SessionPolicyOut,
    UserOut,
)
from app.services import auth_service, login_attempt_service, security_event_service
from app.services.audit_service import record as record_audit
from app.services.auth_service import InvalidCredentialsError

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
settings = get_settings()


def _set_auth_cookies(response: Response, raw_session_token: str, raw_csrf_token: str) -> None:
    max_age = settings.session_ttl_hours * 3600
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_session_token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        max_age=max_age,
        path="/",
    )
    response.set_cookie(
        key="csrf_token",
        value=raw_csrf_token,
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite="strict",
        max_age=max_age,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(key=settings.session_cookie_name, path="/")
    response.delete_cookie(key="csrf_token", path="/")


@router.post("/login", response_model=UserOut)
def login(
    payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)
) -> User:
    ip_address = get_client_ip(request)
    user_agent = request.headers.get("user-agent")

    try:
        normalized = normalize_login_identifier(payload.email)
    except InvalidIdentifierError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    lockout = login_attempt_service.get_lockout_status(db, normalized)
    if lockout.is_locked:
        security_event_service.record(
            db,
            event_type=SecurityEventType.LOGIN_ATTEMPT_WHILE_LOCKED,
            severity=SecurityEventSeverity.WARNING,
            identifier=normalized,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        db.commit()
        retry_after_seconds = max(
            1, int((lockout.locked_until - clock.utcnow()).total_seconds())
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos. Intenta nuevamente más tarde.",
            headers={"Retry-After": str(retry_after_seconds)},
        )

    try:
        user = auth_service.authenticate(db, normalized, payload.password)
    except InvalidCredentialsError as exc:
        # Misma huella de persistencia exista o no la cuenta (ver ADR-0009):
        # una fila en login_attempts (tenant NULL) + una en security_events.
        login_attempt_service.record_attempt(db, identifier=normalized, success=False, tenant_id=None)
        security_event_service.record(
            db,
            event_type=SecurityEventType.LOGIN_FAILURE_ATTEMPT,
            severity=SecurityEventSeverity.INFO,
            tenant_id=exc.tenant_id,
            user_id=exc.user_id,
            identifier=normalized,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        new_lockout = login_attempt_service.get_lockout_status(db, normalized)
        if new_lockout.is_locked:
            security_event_service.record(
                db,
                event_type=SecurityEventType.LOGIN_LOCKOUT_TRIGGERED,
                severity=SecurityEventSeverity.WARNING,
                identifier=normalized,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Correo o contraseña incorrectos"
        ) from None

    login_attempt_service.record_attempt(
        db, identifier=normalized, success=True, tenant_id=user.tenant_id
    )
    raw_token, raw_csrf_token, _session = auth_service.create_session(
        db, user, ip_address=ip_address, user_agent=user_agent
    )
    record_audit(
        db,
        tenant_id=user.tenant_id,
        user_id=user.id,
        action=AuditAction.LOGIN_SUCCESS,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.commit()

    _set_auth_cookies(response, raw_token, raw_csrf_token)
    return user


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> dict:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token:
        session = auth_service.get_valid_session(db, raw_token)
        if session is not None:
            auth_service.revoke_session(db, session)

    record_audit(
        db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        action=AuditAction.LOGOUT,
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()

    _clear_auth_cookies(response)
    return {"status": "ok"}


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.get("/session-policy", response_model=SessionPolicyOut)
def session_policy(current_user: User = Depends(get_current_user)) -> SessionPolicyOut:
    return SessionPolicyOut(
        inactivity_timeout_minutes=settings.session_inactivity_timeout_minutes,
        inactivity_warning_seconds=settings.session_inactivity_warning_seconds,
        session_ttl_hours=settings.session_ttl_hours,
    )


@router.post("/keep-alive", response_model=KeepAliveOut)
def keep_alive(
    db: Session = Depends(get_db),
    current_session: SessionModel = Depends(get_current_session),
    _current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> KeepAliveOut:
    auth_service.keep_alive(db, current_session)
    db.commit()
    return KeepAliveOut(status="ok", last_seen_at=current_session.last_seen_at)


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_session: SessionModel = Depends(get_current_session),
) -> list[SessionOut]:
    sessions = auth_service.list_active_sessions(db, current_user.id)
    return [
        SessionOut(
            id=s.id,
            created_at=s.created_at,
            last_seen_at=s.last_seen_at,
            expires_at=s.expires_at,
            ip_address=s.ip_address,
            user_agent=s.user_agent,
            is_current=(s.id == current_session.id),
        )
        for s in sessions
    ]


@router.post("/sessions/revoke-others")
def revoke_other_sessions(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_session: SessionModel = Depends(get_current_session),
    _csrf: None = Depends(verify_csrf),
) -> dict:
    revoked_count = auth_service.revoke_all_sessions_except(db, current_user.id, current_session.id)
    record_audit(
        db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        action=AuditAction.SESSION_REVOKED_ALL_OTHERS,
        metadata={"revoked_count": revoked_count},
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    return {"status": "ok", "revoked_count": revoked_count}


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> dict:
    ip_address = get_client_ip(request)
    user_agent = request.headers.get("user-agent")
    try:
        raw_token, raw_csrf_token, _session = auth_service.change_password(
            db,
            current_user,
            payload.current_password,
            payload.new_password,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    except InvalidCredentialsError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Contraseña actual incorrecta"
        ) from exc

    record_audit(
        db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        action=AuditAction.PASSWORD_CHANGED,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.commit()

    _set_auth_cookies(response, raw_token, raw_csrf_token)
    return {"status": "ok"}
