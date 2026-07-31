import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.communication_draft import CommunicationDraft


def _apply_filters(
    query,
    tenant_id: uuid.UUID,
    *,
    client_id: uuid.UUID | None,
    status: str | None,
    created_by: uuid.UUID | None,
    related_task_id: uuid.UUID | None,
    owner_restriction: uuid.UUID | None,
    created_from: datetime | None,
    created_to: datetime | None,
):
    query = query.where(CommunicationDraft.tenant_id == tenant_id)
    if owner_restriction is not None:
        query = query.where(CommunicationDraft.created_by == owner_restriction)
    if client_id is not None:
        query = query.where(CommunicationDraft.client_id == client_id)
    if status is not None:
        query = query.where(CommunicationDraft.status == status)
    if created_by is not None:
        query = query.where(CommunicationDraft.created_by == created_by)
    if related_task_id is not None:
        query = query.where(CommunicationDraft.related_task_id == related_task_id)
    if created_from is not None:
        query = query.where(CommunicationDraft.created_at >= created_from)
    if created_to is not None:
        query = query.where(CommunicationDraft.created_at <= created_to)
    return query


def create(db: Session, draft: CommunicationDraft) -> CommunicationDraft:
    db.add(draft)
    db.flush()
    return draft


def get_by_id(db: Session, tenant_id: uuid.UUID, draft_id: uuid.UUID) -> CommunicationDraft | None:
    return db.execute(
        select(CommunicationDraft).where(
            CommunicationDraft.tenant_id == tenant_id, CommunicationDraft.id == draft_id
        )
    ).scalar_one_or_none()


def lock_by_id_for_update(
    db: Session, tenant_id: uuid.UUID, draft_id: uuid.UUID
) -> CommunicationDraft | None:
    """`SELECT ... FOR UPDATE` por la fila exacta -- `populate_existing`
    obligatorio para que la instancia en memoria refleje el estado real
    tras el bloqueo (mismo patron que `google_oauth_repository`/
    `operational_task_repository`)."""
    return db.execute(
        select(CommunicationDraft)
        .where(CommunicationDraft.tenant_id == tenant_id, CommunicationDraft.id == draft_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def list_drafts(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    client_id: uuid.UUID | None = None,
    status: str | None = None,
    created_by: uuid.UUID | None = None,
    related_task_id: uuid.UUID | None = None,
    owner_restriction: uuid.UUID | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: int,
    offset: int,
) -> list[CommunicationDraft]:
    """`owner_restriction` es la restriccion de ownership real (asistente
    solo ve lo propio) -- se aplica siempre en el backend, nunca solo en el
    frontend. `created_by` es un FILTRO opcional adicional (p. ej. un
    admin filtrando por un creador especifico), distinto de la
    restriccion de ownership."""
    query = _apply_filters(
        select(CommunicationDraft),
        tenant_id,
        client_id=client_id,
        status=status,
        created_by=created_by,
        related_task_id=related_task_id,
        owner_restriction=owner_restriction,
        created_from=created_from,
        created_to=created_to,
    )
    query = query.order_by(CommunicationDraft.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(query).scalars().all())


def count_drafts(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    client_id: uuid.UUID | None = None,
    status: str | None = None,
    created_by: uuid.UUID | None = None,
    related_task_id: uuid.UUID | None = None,
    owner_restriction: uuid.UUID | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> int:
    query = _apply_filters(
        select(func.count()).select_from(CommunicationDraft),
        tenant_id,
        client_id=client_id,
        status=status,
        created_by=created_by,
        related_task_id=related_task_id,
        owner_restriction=owner_restriction,
        created_from=created_from,
        created_to=created_to,
    )
    return db.execute(query).scalar_one()
