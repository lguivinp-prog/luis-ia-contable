from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.core.security import (
    generate_session_token,
    hash_session_token,
    verify_password,
)
from app.models.session import Session as SessionModel
from app.models.user import User
from app.repositories import session_repository, user_repository

settings = get_settings()


class InvalidCredentialsError(Exception):
    pass


def authenticate(db: DbSession, email: str, password: str) -> User:
    user = user_repository.get_by_email(db, email)
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        raise InvalidCredentialsError("Correo o contraseña incorrectos")
    return user


def create_session(
    db: DbSession, user: User, *, ip_address: str | None, user_agent: str | None
) -> tuple[str, SessionModel]:
    raw_token = generate_session_token()
    now = datetime.now(UTC)
    session = SessionModel(
        tenant_id=user.tenant_id,
        user_id=user.id,
        token_hash=hash_session_token(raw_token),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.session_ttl_hours),
        ip_address=ip_address,
        user_agent=user_agent,
    )
    session_repository.create(db, session)
    user.last_login_at = now
    db.flush()
    return raw_token, session


def get_valid_session(db: DbSession, raw_token: str) -> SessionModel | None:
    session = session_repository.get_by_token_hash(db, hash_session_token(raw_token))
    if session is None:
        return None
    now = datetime.now(UTC)
    if session.revoked_at is not None or session.expires_at < now:
        return None
    session_repository.touch_last_seen(db, session, now)
    return session


def revoke_session(db: DbSession, session: SessionModel) -> None:
    session_repository.revoke(db, session, datetime.now(UTC))
