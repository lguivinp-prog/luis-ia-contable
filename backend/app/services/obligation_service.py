import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction
from app.models.obligation import ClientObligation, ObligationCatalog
from app.repositories import obligation_repository
from app.services import audit_service


class ObligationNotFoundError(Exception):
    pass


class ClientObligationNotFoundError(Exception):
    pass


class DuplicateAssignmentError(Exception):
    pass


def list_catalog(db: Session) -> list[ObligationCatalog]:
    return obligation_repository.list_catalog(db)


def get_catalog_item(db: Session, obligation_id: uuid.UUID) -> ObligationCatalog:
    item = obligation_repository.get_catalog_item(db, obligation_id)
    if item is None:
        raise ObligationNotFoundError(f"Obligación {obligation_id} no encontrada en el catálogo")
    return item


def list_client_obligations(
    db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID
) -> list[ClientObligation]:
    return obligation_repository.list_client_obligations(db, tenant_id, client_id)


def assign_obligation(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    client_id: uuid.UUID,
    obligation_id: uuid.UUID,
    notes: str | None,
    *,
    ip_address: str | None,
    user_agent: str | None,
) -> ClientObligation:
    get_catalog_item(db, obligation_id)  # valida existencia en el catálogo

    if obligation_repository.get_client_obligation(db, tenant_id, client_id, obligation_id):
        raise DuplicateAssignmentError("Esta obligación ya está asignada a este contribuyente")

    client_obligation = ClientObligation(
        tenant_id=tenant_id,
        client_id=client_id,
        obligation_id=obligation_id,
        assigned_at=datetime.now(UTC),
        notes=notes,
    )
    obligation_repository.create_client_obligation(db, client_obligation)

    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=AuditAction.CLIENT_OBLIGATION_ASSIGN,
        entity_type="client_obligation",
        entity_id=client_obligation.id,
        metadata={"client_id": str(client_id), "obligation_id": str(obligation_id)},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return client_obligation


def update_client_obligation(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    client_id: uuid.UUID,
    obligation_id: uuid.UUID,
    *,
    is_active: bool | None,
    notes: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> ClientObligation:
    client_obligation = obligation_repository.get_client_obligation(
        db, tenant_id, client_id, obligation_id
    )
    if client_obligation is None:
        raise ClientObligationNotFoundError("Asignación de obligación no encontrada")

    if is_active is not None:
        client_obligation.is_active = is_active
        client_obligation.deactivated_at = None if is_active else datetime.now(UTC)
    if notes is not None:
        client_obligation.notes = notes
    db.flush()

    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=AuditAction.CLIENT_OBLIGATION_UPDATE,
        entity_type="client_obligation",
        entity_id=client_obligation.id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return client_obligation


def remove_client_obligation(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    client_id: uuid.UUID,
    obligation_id: uuid.UUID,
    *,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    client_obligation = obligation_repository.get_client_obligation(
        db, tenant_id, client_id, obligation_id
    )
    if client_obligation is None:
        raise ClientObligationNotFoundError("Asignación de obligación no encontrada")

    obligation_repository.delete_client_obligation(db, client_obligation)

    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=AuditAction.CLIENT_OBLIGATION_REMOVE,
        entity_type="client_obligation",
        entity_id=obligation_id,
        metadata={"client_id": str(client_id)},
        ip_address=ip_address,
        user_agent=user_agent,
    )
