import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.login_attempt import LoginAttempt


def record(
    db: Session,
    *,
    tenant_id: uuid.UUID | None,
    identifier: str,
    success: bool,
    attempted_at: datetime,
    ip_address: str | None,
    user_agent: str | None,
) -> LoginAttempt:
    attempt = LoginAttempt(
        tenant_id=tenant_id,
        identifier=identifier,
        success=success,
        attempted_at=attempted_at,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(attempt)
    db.flush()
    return attempt


def get_last_success_at(db: Session, identifier: str) -> datetime | None:
    return db.execute(
        select(LoginAttempt.attempted_at)
        .where(LoginAttempt.identifier == identifier, LoginAttempt.success.is_(True))
        .order_by(LoginAttempt.attempted_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def get_failures_since(db: Session, identifier: str, since: datetime | None) -> list[LoginAttempt]:
    query = select(LoginAttempt).where(
        LoginAttempt.identifier == identifier, LoginAttempt.success.is_(False)
    )
    if since is not None:
        query = query.where(LoginAttempt.attempted_at > since)
    query = query.order_by(LoginAttempt.attempted_at.asc())
    return list(db.execute(query).scalars().all())
