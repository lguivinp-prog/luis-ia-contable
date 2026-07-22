import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.client import Client


def get_by_id(db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID) -> Client | None:
    return db.execute(
        select(Client).where(Client.tenant_id == tenant_id, Client.id == client_id)
    ).scalar_one_or_none()


def get_by_ruc(db: Session, tenant_id: uuid.UUID, ruc: str) -> Client | None:
    return db.execute(
        select(Client).where(Client.tenant_id == tenant_id, Client.ruc == ruc)
    ).scalar_one_or_none()


def list_clients(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    service_status: str | None = None,
    tax_regime: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Client], int]:
    query = select(Client).where(Client.tenant_id == tenant_id)
    count_query = select(func.count()).select_from(Client).where(Client.tenant_id == tenant_id)

    if service_status:
        query = query.where(Client.service_status == service_status)
        count_query = count_query.where(Client.service_status == service_status)
    if tax_regime:
        query = query.where(Client.tax_regime == tax_regime)
        count_query = count_query.where(Client.tax_regime == tax_regime)
    if search:
        pattern = f"%{search.upper()}%"
        condition = or_(
            func.upper(Client.business_name).like(pattern),
            Client.ruc.like(f"%{search}%"),
        )
        query = query.where(condition)
        count_query = count_query.where(condition)

    total = db.execute(count_query).scalar_one()
    items = (
        db.execute(query.order_by(Client.business_name).offset(offset).limit(limit))
        .scalars()
        .all()
    )
    return list(items), total


def create(db: Session, client: Client) -> Client:
    db.add(client)
    db.flush()
    return client
