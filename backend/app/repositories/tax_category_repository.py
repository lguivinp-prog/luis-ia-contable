import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tax_category import ClientTaxCategory, TaxCategoryCatalog


def list_catalog(db: Session, *, active_only: bool = True) -> list[TaxCategoryCatalog]:
    query = select(TaxCategoryCatalog).order_by(TaxCategoryCatalog.code)
    if active_only:
        query = query.where(TaxCategoryCatalog.is_active.is_(True))
    return list(db.execute(query).scalars().all())


def get_catalog_item_by_code(db: Session, code: str) -> TaxCategoryCatalog | None:
    return db.execute(
        select(TaxCategoryCatalog).where(TaxCategoryCatalog.code == code)
    ).scalar_one_or_none()


def create_catalog_item(db: Session, item: TaxCategoryCatalog) -> TaxCategoryCatalog:
    db.add(item)
    db.flush()
    return item


def list_client_categories(
    db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID
) -> list[ClientTaxCategory]:
    query = (
        select(ClientTaxCategory)
        .where(ClientTaxCategory.tenant_id == tenant_id, ClientTaxCategory.client_id == client_id)
        .order_by(ClientTaxCategory.valid_from.desc())
    )
    return list(db.execute(query).scalars().all())


def get_client_category(
    db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID, assignment_id: uuid.UUID
) -> ClientTaxCategory | None:
    return db.execute(
        select(ClientTaxCategory).where(
            ClientTaxCategory.tenant_id == tenant_id,
            ClientTaxCategory.client_id == client_id,
            ClientTaxCategory.id == assignment_id,
        )
    ).scalar_one_or_none()


def create_client_category(db: Session, assignment: ClientTaxCategory) -> ClientTaxCategory:
    """Puede lanzar sqlalchemy.exc.IntegrityError si la vigencia se solapa con
    otra fila del mismo client_id+category_code (restriccion EXCLUDE, ver
    ADR-0013). El servicio debe traducirla a OverlappingCategoryValidityError."""
    db.add(assignment)
    db.flush()
    return assignment


def delete_client_category(db: Session, assignment: ClientTaxCategory) -> None:
    db.delete(assignment)
    db.flush()


def get_active_categories_for_client(
    db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID, reference_date: date
) -> list[str]:
    """Categorias del cliente vigentes en `reference_date` (no "activas hoy"):
    ver ADR-0013 / TAX_RULES_MODEL.md sobre la fecha de referencia usada por
    el resolver de cronogramas."""
    query = select(ClientTaxCategory.category_code).where(
        ClientTaxCategory.tenant_id == tenant_id,
        ClientTaxCategory.client_id == client_id,
        ClientTaxCategory.valid_from <= reference_date,
        (ClientTaxCategory.valid_until.is_(None)) | (ClientTaxCategory.valid_until >= reference_date),
    )
    return [row[0] for row in db.execute(query).all()]


def get_active_category_client_ids(
    db: Session,
    tenant_id: uuid.UUID,
    client_ids: list[uuid.UUID],
    category_code: str,
    reference_date: date,
) -> set[uuid.UUID]:
    """Usado por client_service para calcular ClientOut.is_good_taxpayer de
    forma dinamica (ver ADR-0013): un solo query batched, sin N+1."""
    if not client_ids:
        return set()
    query = select(ClientTaxCategory.client_id).where(
        ClientTaxCategory.tenant_id == tenant_id,
        ClientTaxCategory.client_id.in_(client_ids),
        ClientTaxCategory.category_code == category_code,
        ClientTaxCategory.valid_from <= reference_date,
        (ClientTaxCategory.valid_until.is_(None)) | (ClientTaxCategory.valid_until >= reference_date),
    )
    return {row[0] for row in db.execute(query).all()}
