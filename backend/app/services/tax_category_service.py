"""Categorias tributarias del cliente (ver ADR-0013).

`tax_regime` (NRUS/RER/RMT/GENERAL) es un concepto totalmente distinto y no
se toca aqui. `clients.is_good_taxpayer` es una columna heredada del Bloque 1
que ya NO se lee ni se escribe desde este modulo: el estado "buen
contribuyente" se calcula siempre dinamicamente a partir de
`client_tax_categories`, evaluado a una fecha de referencia (ver
docs/TAX_RULES_MODEL.md), para que nunca quede un valor obsoleto una vez
cruzado `valid_from`/`valid_until`.
"""

import uuid
from datetime import date

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.tax_category import ClientTaxCategory, TaxCategoryCatalog
from app.repositories import tax_category_repository

BUEN_CONTRIBUYENTE_CODE = "BUEN_CONTRIBUYENTE"


class TaxCategoryNotFoundError(Exception):
    pass


class ClientTaxCategoryNotFoundError(Exception):
    pass


class OverlappingCategoryValidityError(Exception):
    pass


def list_catalog(db: Session) -> list[TaxCategoryCatalog]:
    return tax_category_repository.list_catalog(db)


def get_catalog_item(db: Session, code: str) -> TaxCategoryCatalog:
    item = tax_category_repository.get_catalog_item_by_code(db, code)
    if item is None:
        raise TaxCategoryNotFoundError(f"Categoría '{code}' no encontrada en el catálogo")
    return item


def create_catalog_item(db: Session, code: str, name: str, description: str | None) -> TaxCategoryCatalog:
    item = TaxCategoryCatalog(code=code, name=name, description=description, is_active=True)
    return tax_category_repository.create_catalog_item(db, item)


def list_client_categories(
    db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID
) -> list[ClientTaxCategory]:
    return tax_category_repository.list_client_categories(db, tenant_id, client_id)


def assign_category(
    db: Session,
    tenant_id: uuid.UUID,
    client_id: uuid.UUID,
    category_code: str,
    valid_from: date,
    valid_until: date | None,
    notes: str | None,
) -> ClientTaxCategory:
    get_catalog_item(db, category_code)  # valida existencia en el catalogo

    assignment = ClientTaxCategory(
        tenant_id=tenant_id,
        client_id=client_id,
        category_code=category_code,
        valid_from=valid_from,
        valid_until=valid_until,
        notes=notes,
    )
    try:
        return tax_category_repository.create_client_category(db, assignment)
    except IntegrityError as exc:
        raise OverlappingCategoryValidityError(
            f"La vigencia se solapa con otra asignación existente de '{category_code}' "
            "para este contribuyente"
        ) from exc


def remove_client_category(
    db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID, assignment_id: uuid.UUID
) -> None:
    assignment = tax_category_repository.get_client_category(db, tenant_id, client_id, assignment_id)
    if assignment is None:
        raise ClientTaxCategoryNotFoundError("Asignación de categoría no encontrada")
    tax_category_repository.delete_client_category(db, assignment)


def is_client_in_category(
    db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID, category_code: str, reference_date: date
) -> bool:
    codes = tax_category_repository.get_active_categories_for_client(
        db, tenant_id, client_id, reference_date
    )
    return category_code in codes


def compute_good_taxpayer_map(
    db: Session, tenant_id: uuid.UUID, client_ids: list[uuid.UUID], reference_date: date
) -> dict[uuid.UUID, bool]:
    """Para listados: un solo query batched, sin N+1 (ver ADR-0013)."""
    active_ids = tax_category_repository.get_active_category_client_ids(
        db, tenant_id, client_ids, BUEN_CONTRIBUYENTE_CODE, reference_date
    )
    return {client_id: client_id in active_ids for client_id in client_ids}
