from datetime import timedelta

from sqlalchemy.orm import Session as DbSession

from app.core import clock
from app.core.config import get_settings
from app.core.security import (
    generate_session_token,
    get_dummy_password_hash,
    hash_password,
    hash_session_token,
    verify_password,
)
from app.models.security_event import SecurityEventSeverity, SecurityEventType
from app.models.session import Session as SessionModel
from app.models.user import User
from app.repositories import session_repository, user_repository
from app.services import csrf_service, security_event_service

settings = get_settings()


class InvalidCredentialsError(Exception):
    """Fallo de autenticacion. tenant_id/user_id son datos internos para
    security_events (investigacion de seguridad); nunca se exponen en la
    respuesta HTTP ni cambian el mensaje/codigo/tiempo observados por el
    cliente (ver ADR-0009)."""

    def __init__(self, message: str, *, tenant_id=None, user_id=None):
        super().__init__(message)
        self.tenant_id = tenant_id
        self.user_id = user_id


def authenticate(db: DbSession, normalized_email: str, password: str) -> User:
    user = user_repository.get_by_email(db, normalized_email)

    if user is None:
        verify_password(password, get_dummy_password_hash())
        raise InvalidCredentialsError("Correo o contraseña incorrectos")

    if not user.is_active:
        verify_password(password, get_dummy_password_hash())
        raise InvalidCredentialsError(
            "Correo o contraseña incorrectos", tenant_id=user.tenant_id, user_id=user.id
        )

    if not verify_password(password, user.password_hash):
        raise InvalidCredentialsError(
            "Correo o contraseña incorrectos", tenant_id=user.tenant_id, user_id=user.id
        )

    return user


def _enforce_concurrent_session_limit(db: DbSession, user: User, now) -> None:
    active = session_repository.list_active_for_user(db, user.id, now)
    excess = len(active) - settings.session_max_concurrent
    if excess <= 0:
        return
    for session in active[:excess]:
        session_repository.revoke(db, session, now)
        security_event_service.record(
            db,
            event_type=SecurityEventType.SESSION_REVOKED_CONCURRENT_LIMIT,
            severity=SecurityEventSeverity.INFO,
            tenant_id=user.tenant_id,
            user_id=user.id,
            metadata={"session_id": str(session.id)},
        )


def create_session(
    db: DbSession, user: User, *, ip_address: str | None, user_agent: str | None
) -> tuple[str, str, SessionModel]:
    raw_token = generate_session_token()
    raw_csrf_token = csrf_service.generate_csrf_token()
    now = clock.utcnow()
    session = SessionModel(
        tenant_id=user.tenant_id,
        user_id=user.id,
        token_hash=hash_session_token(raw_token),
        csrf_token_hash=csrf_service.hash_csrf_token(raw_csrf_token),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.session_ttl_hours),
        ip_address=ip_address,
        user_agent=user_agent,
    )
    session_repository.create(db, session)
    user.last_login_at = now
    db.flush()
    _enforce_concurrent_session_limit(db, user, now)
    return raw_token, raw_csrf_token, session


def get_valid_session(db: DbSession, raw_token: str) -> SessionModel | None:
    session = session_repository.get_by_token_hash(db, hash_session_token(raw_token))
    if session is None:
        return None

    now = clock.utcnow()

    if session.revoked_at is not None:
        return None

    if session.expires_at < now:
        session_repository.revoke(db, session, now)
        security_event_service.record(
            db,
            event_type=SecurityEventType.SESSION_EXPIRED_ABSOLUTE,
            severity=SecurityEventSeverity.INFO,
            tenant_id=session.tenant_id,
            user_id=session.user_id,
        )
        return None

    inactivity_limit = session.last_seen_at + timedelta(
        minutes=settings.session_inactivity_timeout_minutes
    )
    if now > inactivity_limit:
        session_repository.revoke(db, session, now)
        security_event_service.record(
            db,
            event_type=SecurityEventType.SESSION_EXPIRED_INACTIVITY,
            severity=SecurityEventSeverity.INFO,
            tenant_id=session.tenant_id,
            user_id=session.user_id,
        )
        return None

    session_repository.touch_last_seen(db, session, now)
    return session


def revoke_session(db: DbSession, session: SessionModel) -> None:
    session_repository.revoke(db, session, clock.utcnow())


def revoke_all_sessions_except(db: DbSession, user_id, keep_session_id) -> int:
    now = clock.utcnow()
    count = session_repository.revoke_all_except(db, user_id, keep_session_id, now)
    return count


def revoke_all_sessions(db: DbSession, user_id) -> int:
    """Revoca todas las sesiones activas de `user_id`, sin excepcion -- usado
    al desactivar un usuario o al resetear su contrasena administrativamente
    (ver ADR-0051), a diferencia de `revoke_all_sessions_except` que preserva
    la sesion actual del propio usuario que ejecuta la accion."""
    now = clock.utcnow()
    return session_repository.revoke_all_for_user(db, user_id, now)


def list_active_sessions(db: DbSession, user_id) -> list[SessionModel]:
    return session_repository.list_active_for_user(db, user_id, clock.utcnow())


def keep_alive(db: DbSession, session: SessionModel) -> None:
    session_repository.touch_last_seen(db, session, clock.utcnow())


def change_password(
    db: DbSession,
    user: User,
    current_password: str,
    new_password: str,
    *,
    ip_address: str | None,
    user_agent: str | None,
) -> tuple[str, str, SessionModel]:
    if not verify_password(current_password, user.password_hash):
        raise InvalidCredentialsError(
            "Contraseña actual incorrecta", tenant_id=user.tenant_id, user_id=user.id
        )

    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    now = clock.utcnow()
    revoked_count = session_repository.revoke_all_for_user(db, user.id, now)
    security_event_service.record(
        db,
        event_type=SecurityEventType.SESSION_REVOKED_PASSWORD_CHANGE,
        severity=SecurityEventSeverity.WARNING,
        tenant_id=user.tenant_id,
        user_id=user.id,
        metadata={"revoked_count": revoked_count},
    )
    return create_session(db, user, ip_address=ip_address, user_agent=user_agent)
