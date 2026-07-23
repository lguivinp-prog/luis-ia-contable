import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.obligation import ClientObligation, ObligationCatalog


def list_catalog(db: Session, *, active_only: bool = True) -> list[ObligationCatalog]:
    query = select(ObligationCatalog).order_by(ObligationCatalog.code)
    if active_only:
        query = query.where(ObligationCatalog.is_active.is_(True))
    return list(db.execute(query).scalars().all())


def get_catalog_item(db: Session, obligation_id: uuid.UUID) -> ObligationCatalog | None:
    return db.get(ObligationCatalog, obligation_id)


def list_all_client_obligations_for_tenant(
    db: Session, tenant_id: uuid.UUID
) -> list[ClientObligation]:
    """Todas las asignaciones reales cliente-obligacion de un tenant (ver
    "Generacion desde asignaciones reales" -- nunca un producto cartesiano
    cliente x catalogo global)."""
    return list(
        db.execute(
            select(ClientObligation).where(ClientObligation.tenant_id == tenant_id)
        )
        .scalars()
        .all()
    )


def list_client_obligations(
    db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID
) -> list[ClientObligation]:
    return list(
        db.execute(
            select(ClientObligation).where(
                ClientObligation.tenant_id == tenant_id,
                ClientObligation.client_id == client_id,
            )
        )
        .scalars()
        .all()
    )


def get_client_obligation_by_id(
    db: Session, tenant_id: uuid.UUID, client_obligation_id: uuid.UUID
) -> ClientObligation | None:
    return db.execute(
        select(ClientObligation).where(
            ClientObligation.tenant_id == tenant_id, ClientObligation.id == client_obligation_id
        )
    ).scalar_one_or_none()


def get_client_obligation(
    db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID, obligation_id: uuid.UUID
) -> ClientObligation | None:
    return db.execute(
        select(ClientObligation).where(
            ClientObligation.tenant_id == tenant_id,
            ClientObligation.client_id == client_id,
            ClientObligation.obligation_id == obligation_id,
        )
    ).scalar_one_or_none()


def create_client_obligation(db: Session, client_obligation: ClientObligation) -> ClientObligation:
    db.add(client_obligation)
    db.flush()
    return client_obligation


def delete_client_obligation(db: Session, client_obligation: ClientObligation) -> None:
    db.delete(client_obligation)
    db.flush()
