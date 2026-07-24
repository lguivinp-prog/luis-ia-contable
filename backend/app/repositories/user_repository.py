import uuid

from sqlalchemy import and_, func, or_, select
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


def get_by_id_and_tenant(db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID) -> User | None:
    return db.execute(
        select(User).where(User.id == user_id, User.tenant_id == tenant_id)
    ).scalar_one_or_none()


def list_by_tenant(db: Session, tenant_id: uuid.UUID) -> list[User]:
    return list(
        db.execute(
            select(User).where(User.tenant_id == tenant_id).order_by(User.email)
        ).scalars()
    )


def lock_target_and_active_admins(
    db: Session, tenant_id: uuid.UUID, target_id: uuid.UUID
) -> tuple[User | None, list[User]]:
    """Bloquea, en una sola consulta y en un orden deterministico (`ORDER BY
    id`), tanto la fila objetivo como todas las filas admin activas del
    tenant (ver ADR-0051 / Seccion 3 del plan) -- evita el deadlock que
    aparecería si dos transacciones bloquearan el mismo conjunto de filas en
    sentencias separadas con orden distinto. `target` es None si no existe o
    no pertenece al tenant (el llamador traduce esto a 404)."""
    rows = list(
        db.execute(
            select(User)
            .where(
                User.tenant_id == tenant_id,
                or_(
                    User.id == target_id,
                    and_(User.role == "admin", User.is_active.is_(True)),
                ),
            )
            .order_by(User.id)
            .with_for_update()
        ).scalars()
    )
    target = next((u for u in rows if u.id == target_id), None)
    admins = [u for u in rows if u.role == "admin" and u.is_active]
    return target, admins
