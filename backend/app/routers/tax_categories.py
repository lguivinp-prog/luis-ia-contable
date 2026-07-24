import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_client_ip, verify_csrf
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.models.audit_log import AuditAction
from app.models.user import User
from app.schemas.tax_category import (
    ClientTaxCategoryCreate,
    ClientTaxCategoryOut,
    TaxCategoryCatalogCreate,
    TaxCategoryCatalogOut,
)
from app.services import client_service, tax_category_service
from app.services.audit_service import record as record_audit
from app.services.client_service import ClientNotFoundError
from app.services.tax_category_service import (
    ClientTaxCategoryNotFoundError,
    OverlappingCategoryValidityError,
    TaxCategoryNotFoundError,
)

router = APIRouter(prefix="/api/v1", tags=["tax-categories"])


@router.get("/tax-categories/catalog", response_model=list[TaxCategoryCatalogOut])
def list_catalog(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.TAX_CATEGORY_READ)),
) -> list[TaxCategoryCatalogOut]:
    return tax_category_service.list_catalog(db)


@router.post(
    "/tax-categories/catalog", response_model=TaxCategoryCatalogOut, status_code=status.HTTP_201_CREATED
)
def create_catalog_item(
    payload: TaxCategoryCatalogCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.TAX_CATEGORY_WRITE)),
    _csrf: None = Depends(verify_csrf),
) -> TaxCategoryCatalogOut:
    item = tax_category_service.create_catalog_item(db, payload.code, payload.name, payload.description)
    db.commit()
    db.refresh(item)
    return item


@router.get("/clients/{client_id}/tax-categories", response_model=list[ClientTaxCategoryOut])
def list_client_tax_categories(
    client_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.TAX_CATEGORY_READ)),
) -> list[ClientTaxCategoryOut]:
    try:
        client_service.get_client(db, current_user.tenant_id, client_id)
    except ClientNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return tax_category_service.list_client_categories(db, current_user.tenant_id, client_id)


@router.post(
    "/clients/{client_id}/tax-categories",
    response_model=ClientTaxCategoryOut,
    status_code=status.HTTP_201_CREATED,
)
def assign_client_tax_category(
    client_id: uuid.UUID,
    payload: ClientTaxCategoryCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.TAX_CATEGORY_WRITE)),
    _csrf: None = Depends(verify_csrf),
) -> ClientTaxCategoryOut:
    try:
        client_service.get_client(db, current_user.tenant_id, client_id)
        assignment = tax_category_service.assign_category(
            db,
            current_user.tenant_id,
            client_id,
            payload.category_code,
            payload.valid_from,
            payload.valid_until,
            payload.notes,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TAX_CATEGORY_ASSIGN,
            entity_type="client_tax_category",
            entity_id=assignment.id,
            metadata={"client_id": str(client_id), "category_code": payload.category_code},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(assignment)
        return assignment
    except ClientNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TaxCategoryNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except OverlappingCategoryValidityError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.delete(
    "/clients/{client_id}/tax-categories/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT
)
def remove_client_tax_category(
    client_id: uuid.UUID,
    assignment_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.TAX_CATEGORY_WRITE)),
    _csrf: None = Depends(verify_csrf),
) -> None:
    try:
        tax_category_service.remove_client_category(db, current_user.tenant_id, client_id, assignment_id)
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TAX_CATEGORY_REMOVE,
            entity_type="client_tax_category",
            entity_id=assignment_id,
            metadata={"client_id": str(client_id)},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
    except ClientTaxCategoryNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
