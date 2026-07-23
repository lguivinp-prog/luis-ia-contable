"""Catalogo de requisitos documentales, hibrido sistema/personalizado (ver
ADR-0033). Las filas `is_system=true` (tenant_id NULL) solo se crean por
semilla o una accion administrativa fuera de estos endpoints -- ningun
metodo de este servicio expone su creacion."""

import uuid

from sqlalchemy.orm import Session

from app.models.declaration_document import DocumentRequirementCatalog
from app.repositories import declaration_document_repository


class DocumentCatalogEntryNotFoundError(Exception):
    pass


class ForeignDocumentCatalogEntryError(Exception):
    pass


def list_visible_catalog(
    db: Session, tenant_id: uuid.UUID
) -> list[DocumentRequirementCatalog]:
    return declaration_document_repository.list_catalog(db, tenant_id)


def get_catalog_entry(
    db: Session, tenant_id: uuid.UUID, catalog_id: uuid.UUID
) -> DocumentRequirementCatalog:
    entry = declaration_document_repository.get_catalog_entry(db, catalog_id)
    if entry is None:
        raise DocumentCatalogEntryNotFoundError(f"Requisito de catalogo {catalog_id} no encontrado")
    validate_catalog_scope(entry, tenant_id)
    return entry


def validate_catalog_scope(entry: DocumentRequirementCatalog, tenant_id: uuid.UUID) -> None:
    """Valida transaccionalmente que el catalogo sea del sistema o del mismo
    tenant (ver ADR-0033: la FK hacia el catalogo es simple, no compuesta,
    por la limitacion real de Postgres con filas de sistema `tenant_id
    NULL`; esta validacion de servicio es la alternativa equivalente)."""
    if entry.tenant_id is not None and entry.tenant_id != tenant_id:
        raise ForeignDocumentCatalogEntryError(
            "El requisito de catalogo pertenece a otro tenant"
        )


def create_custom_catalog_entry(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    code: str,
    name: str,
    description: str | None,
    category: str | None,
) -> DocumentRequirementCatalog:
    entry = DocumentRequirementCatalog(
        tenant_id=tenant_id,
        is_system=False,
        code=code,
        name=name,
        description=description,
        category=category,
        is_active=True,
    )
    return declaration_document_repository.create_catalog_entry(db, entry)
