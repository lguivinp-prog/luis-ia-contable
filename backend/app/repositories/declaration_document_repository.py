import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.declaration_document import (
    ClientDocumentRequirement,
    DeclarationDocumentItem,
    DocumentItemStatusHistory,
    DocumentRequirementCatalog,
)

# --- catalogo ---


def list_catalog(db: Session, tenant_id: uuid.UUID) -> list[DocumentRequirementCatalog]:
    return list(
        db.execute(
            select(DocumentRequirementCatalog)
            .where(
                (DocumentRequirementCatalog.tenant_id.is_(None))
                | (DocumentRequirementCatalog.tenant_id == tenant_id)
            )
            .order_by(DocumentRequirementCatalog.name)
        )
        .scalars()
        .all()
    )


def get_catalog_entry(
    db: Session, catalog_id: uuid.UUID
) -> DocumentRequirementCatalog | None:
    return db.get(DocumentRequirementCatalog, catalog_id)


def create_catalog_entry(
    db: Session, entry: DocumentRequirementCatalog
) -> DocumentRequirementCatalog:
    db.add(entry)
    db.flush()
    return entry


# --- asignaciones cliente ---


def create_client_requirement(
    db: Session, requirement: ClientDocumentRequirement
) -> ClientDocumentRequirement:
    db.add(requirement)
    db.flush()
    return requirement


def get_client_requirement(
    db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID, requirement_id: uuid.UUID
) -> ClientDocumentRequirement | None:
    return db.execute(
        select(ClientDocumentRequirement).where(
            ClientDocumentRequirement.tenant_id == tenant_id,
            ClientDocumentRequirement.client_id == client_id,
            ClientDocumentRequirement.id == requirement_id,
        )
    ).scalar_one_or_none()


def list_client_requirements(
    db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID
) -> list[ClientDocumentRequirement]:
    return list(
        db.execute(
            select(ClientDocumentRequirement).where(
                ClientDocumentRequirement.tenant_id == tenant_id,
                ClientDocumentRequirement.client_id == client_id,
            )
        )
        .scalars()
        .all()
    )


def list_active_requirements_for_sync(
    db: Session,
    tenant_id: uuid.UUID,
    client_id: uuid.UUID,
    client_obligation_id: uuid.UUID | None,
    reference_date: date,
) -> list[ClientDocumentRequirement]:
    """Requisitos vigentes para materializar en una declaracion (ver
    "Sincronizacion documental"): generales (client_obligation_id NULL) o
    atados exactamente a la obligacion real de la declaracion."""
    return list(
        db.execute(
            select(ClientDocumentRequirement).where(
                ClientDocumentRequirement.tenant_id == tenant_id,
                ClientDocumentRequirement.client_id == client_id,
                ClientDocumentRequirement.is_active.is_(True),
                ClientDocumentRequirement.validity_range.contains(reference_date),
                (ClientDocumentRequirement.client_obligation_id.is_(None))
                | (ClientDocumentRequirement.client_obligation_id == client_obligation_id),
            )
        )
        .scalars()
        .all()
    )


# --- items por declaracion ---


def get_item_by_requirement(
    db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID, requirement_id: uuid.UUID
) -> DeclarationDocumentItem | None:
    return db.execute(
        select(DeclarationDocumentItem).where(
            DeclarationDocumentItem.tenant_id == tenant_id,
            DeclarationDocumentItem.declaration_id == declaration_id,
            DeclarationDocumentItem.client_document_requirement_id == requirement_id,
        )
    ).scalar_one_or_none()


def create_item(db: Session, item: DeclarationDocumentItem) -> DeclarationDocumentItem:
    db.add(item)
    db.flush()
    return item


def get_item(
    db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID, item_id: uuid.UUID
) -> DeclarationDocumentItem | None:
    return db.execute(
        select(DeclarationDocumentItem).where(
            DeclarationDocumentItem.tenant_id == tenant_id,
            DeclarationDocumentItem.declaration_id == declaration_id,
            DeclarationDocumentItem.id == item_id,
        )
    ).scalar_one_or_none()


def list_items_for_declaration(
    db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID
) -> list[DeclarationDocumentItem]:
    return list(
        db.execute(
            select(DeclarationDocumentItem)
            .where(
                DeclarationDocumentItem.tenant_id == tenant_id,
                DeclarationDocumentItem.declaration_id == declaration_id,
            )
            .order_by(DeclarationDocumentItem.created_at.asc())
        )
        .scalars()
        .all()
    )


def create_item_status_history_entry(db: Session, entry: DocumentItemStatusHistory) -> None:
    db.add(entry)
    db.flush()


def list_item_status_history(
    db: Session, tenant_id: uuid.UUID, declaration_document_item_id: uuid.UUID
) -> list[DocumentItemStatusHistory]:
    return list(
        db.execute(
            select(DocumentItemStatusHistory)
            .where(
                DocumentItemStatusHistory.tenant_id == tenant_id,
                DocumentItemStatusHistory.declaration_document_item_id
                == declaration_document_item_id,
            )
            .order_by(DocumentItemStatusHistory.created_at.asc())
        )
        .scalars()
        .all()
    )
