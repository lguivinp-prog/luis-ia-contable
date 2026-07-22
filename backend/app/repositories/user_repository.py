import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.user import User


def get_by_email(db: Session, email: str) -> User | None:
    """Se espera que `email` ya venga normalizado (ver
    app.core.identifier.normalize_login_identifier). La comparacion usa
    func.lower() como defensa adicional ante filas historicas no
    normalizadas."""
    return db.execute(
        select(User).where(func.lower(User.email) == email)
    ).scalar_one_or_none()


def get_by_id(db: Session, user_id: uuid.UUID) -> User | None:
    return db.get(User, user_id)
