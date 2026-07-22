import uuid
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


def get_by_id(db: DbSession, session_id: uuid.UUID) -> SessionModel | None:
    return db.get(SessionModel, session_id)


def revoke(db: DbSession, session: SessionModel, revoked_at: datetime) -> None:
    session.revoked_at = revoked_at
    db.flush()


def touch_last_seen(db: DbSession, session: SessionModel, now: datetime) -> None:
    session.last_seen_at = now
    db.flush()


def list_active_for_user(db: DbSession, user_id: uuid.UUID, now: datetime) -> list[SessionModel]:
    query = (
        select(SessionModel)
        .where(
            SessionModel.user_id == user_id,
            SessionModel.revoked_at.is_(None),
            SessionModel.expires_at > now,
        )
        .order_by(SessionModel.created_at.asc())
    )
    return list(db.execute(query).scalars().all())


def count_active_for_user(db: DbSession, user_id: uuid.UUID, now: datetime) -> int:
    return len(list_active_for_user(db, user_id, now))


def revoke_all_for_user(db: DbSession, user_id: uuid.UUID, revoked_at: datetime) -> int:
    sessions = list_active_for_user(db, user_id, revoked_at)
    for session in sessions:
        session.revoked_at = revoked_at
    db.flush()
    return len(sessions)


def revoke_all_except(
    db: DbSession, user_id: uuid.UUID, keep_session_id: uuid.UUID, revoked_at: datetime
) -> int:
    sessions = [
        s for s in list_active_for_user(db, user_id, revoked_at) if s.id != keep_session_id
    ]
    for session in sessions:
        session.revoked_at = revoked_at
    db.flush()
    return len(sessions)
