from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models.session import Session as SessionModel


def create(db: DbSession, session: SessionModel) -> SessionModel:
    db.add(session)
    db.flush()
    return session


def get_by_token_hash(db: DbSession, token_hash: str) -> SessionModel | None:
    return db.execute(
        select(SessionModel).where(SessionModel.token_hash == token_hash)
    ).scalar_one_or_none()


def revoke(db: DbSession, session: SessionModel, revoked_at: datetime) -> None:
    session.revoked_at = revoked_at
    db.flush()


def touch_last_seen(db: DbSession, session: SessionModel, now: datetime) -> None:
    session.last_seen_at = now
    db.flush()
