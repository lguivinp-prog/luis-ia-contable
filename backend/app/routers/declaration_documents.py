import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_client_ip, get_current_user, verify_csrf
from app.db.session import get_db
from app.models.audit_log import AuditAction
from app.models.user import User
from app.schemas.declaration_document import (
    ClientDocumentRequirementCreate,
    ClientDocumentRequirementOut,
    DeclarationDocumentItemOut,
    DocumentItemStatusChangeRequest,
    DocumentRequirementCatalogCreate,
    DocumentRequirementCatalogOut,
    SyncRequirementsResult,
)
from app.services import (
    declaration_document_service,
    declaration_service,
    document_requirement_catalog_service,
)
from app.services.audit_service import record as record_audit
from app.services.client_service import ClientNotFoundError, get_client
from app.services.declaration_document_service import (
    DeclarationDocumentItemNotFoundError,
    InvalidFileMetadataError,
)
from app.services.declaration_service import DeclarationNotFoundError
from app.services.document_requirement_catalog_service import (
    DocumentCatalogEntryNotFoundError,
    ForeignDocumentCatalogEntryError,
)
from app.services.tax_period_service import TaxPeriodClosedError, TaxPeriodNotFoundError

router = APIRouter(prefix="/api/v1", tags=["declaration-documents"])


# --- catalogo ---


@router.get("/document-requirement-catalog", response_model=list[DocumentRequirementCatalogOut])
def list_catalog(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> list[DocumentRequirementCatalogOut]:
    return document_requirement_catalog_service.list_visible_catalog(db, current_user.tenant_id)


@router.post(
    "/document-requirement-catalog",
    response_model=DocumentRequirementCatalogOut,
    status_code=status.HTTP_201_CREATED,
)
def create_catalog_entry(
    payload: DocumentRequirementCatalogCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> DocumentRequirementCatalogOut:
    entry = document_requirement_catalog_service.create_custom_catalog_entry(
        db,
        current_user.tenant_id,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        category=payload.category,
    )
    record_audit(
        db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        action=AuditAction.DOCUMENT_CATALOG_CREATE,
        entity_type="document_requirement_catalog",
        entity_id=entry.id,
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(entry)
    return entry


# --- asignaciones por cliente ---


@router.get(
    "/clients/{client_id}/document-requirements",
    response_model=list[ClientDocumentRequirementOut],
)
def list_client_requirements(
    client_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ClientDocumentRequirementOut]:
    try:
        get_client(db, current_user.tenant_id, client_id)
    except ClientNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return declaration_document_service.list_client_requirements(db, current_user.tenant_id, client_id)


@router.post(
    "/clients/{client_id}/document-requirements",
    response_model=ClientDocumentRequirementOut,
    status_code=status.HTTP_201_CREATED,
)
def assign_requirement(
    client_id: uuid.UUID,
    payload: ClientDocumentRequirementCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ClientDocumentRequirementOut:
    try:
        get_client(db, current_user.tenant_id, client_id)
        requirement = declaration_document_service.assign_requirement(
            db,
            current_user.tenant_id,
            client_id,
            requirement_catalog_id=payload.requirement_catalog_id,
            client_obligation_id=payload.client_obligation_id,
            is_mandatory=payload.is_mandatory,
            periodicity=payload.periodicity,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            description=payload.description,
            responsible_user_id=payload.responsible_user_id,
            reminder_lead_days=payload.reminder_lead_days,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.DOCUMENT_REQUIREMENT_ASSIGN,
            entity_type="client_document_requirement",
            entity_id=requirement.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(requirement)
        return requirement
    except ClientNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DocumentCatalogEntryNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ForeignDocumentCatalogEntryError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


# --- items por declaracion ---


@router.get(
    "/declarations/{declaration_id}/documents", response_model=list[DeclarationDocumentItemOut]
)
def list_declaration_documents(
    declaration_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[DeclarationDocumentItemOut]:
    try:
        declaration_service.get_declaration(db, current_user.tenant_id, declaration_id)
    except DeclarationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return declaration_document_service.list_items(db, current_user.tenant_id, declaration_id)


@router.patch(
    "/declarations/{declaration_id}/documents/{item_id}",
    response_model=DeclarationDocumentItemOut,
)
def change_document_status(
    declaration_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: DocumentItemStatusChangeRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> DeclarationDocumentItemOut:
    try:
        declaration = declaration_service.get_declaration(db, current_user.tenant_id, declaration_id)
        item = declaration_document_service.change_item_status(
            db,
            current_user.tenant_id,
            declaration,
            item_id,
            target=payload.target,
            reason=payload.reason,
            file_metadata=payload.file_metadata,
            actor_user_id=current_user.id,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.DOCUMENT_ITEM_STATUS_CHANGE,
            entity_type="declaration_document_item",
            entity_id=item.id,
            metadata={"target": payload.target},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(item)
        return item
    except (DeclarationNotFoundError, DeclarationDocumentItemNotFoundError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidFileMetadataError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post(
    "/declarations/{declaration_id}/documents/sync-requirements",
    response_model=SyncRequirementsResult,
)
def sync_requirements(
    declaration_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> SyncRequirementsResult:
    try:
        declaration = declaration_service.get_declaration(db, current_user.tenant_id, declaration_id)
        result = declaration_document_service.sync_requirements_for_declaration(
            db, current_user.tenant_id, declaration, actor_user_id=current_user.id, origin="MANUAL"
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.DOCUMENT_REQUIREMENTS_SYNC,
            entity_type="declaration",
            entity_id=declaration.id,
            metadata={
                "created": len(result["created"]),
                "existing": len(result["existing"]),
                "skipped": len(result["skipped"]),
            },
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        return SyncRequirementsResult(**result)
    except DeclarationNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TaxPeriodNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TaxPeriodClosedError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/tax-periods/{tax_period_id}/documents/sync-requirements",
)
def sync_requirements_for_period(
    tax_period_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> dict:
    """Accion administrativa masiva (ver "Sincronizacion documental"): itera
    todas las declaraciones del periodo con un SAVEPOINT por declaracion,
    para declaraciones preexistentes de 3.1 o para volver a materializar
    items tras cambios de asignacion."""
    from app.repositories import declaration_repository

    total_synced = 0
    total_errors = 0
    errors: list[dict] = []

    declarations, _total = declaration_repository.list_for_dashboard(
        db, current_user.tenant_id, tax_period_id=tax_period_id, limit=10_000, offset=0
    )

    for declaration in declarations:
        savepoint = db.begin_nested()
        try:
            declaration_document_service.sync_requirements_for_declaration(
                db, current_user.tenant_id, declaration, actor_user_id=current_user.id, origin="MANUAL"
            )
            savepoint.commit()
            total_synced += 1
        except Exception as exc:  # noqa: BLE001 -- error recuperable, contenido por el SAVEPOINT
            savepoint.rollback()
            total_errors += 1
            errors.append({"declaration_id": str(declaration.id), "error": str(exc)})

    record_audit(
        db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        action=AuditAction.DOCUMENT_REQUIREMENTS_SYNC,
        entity_type="tax_period",
        entity_id=tax_period_id,
        metadata={"total_synced": total_synced, "total_errors": total_errors},
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    return {"total_synced": total_synced, "total_errors": total_errors, "errors": errors}
