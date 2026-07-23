import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_client_ip, get_current_user, verify_csrf
from app.db.session import get_db
from app.models.audit_log import AuditAction
from app.models.user import User
from app.schemas.declaration_generation import (
    DeclarationGenerationRunOut,
    GenerationItemExcludeRequest,
    GenerationPreviewRequest,
)
from app.services import declaration_generation_service
from app.services.audit_service import record as record_audit
from app.services.declaration_generation_service import (
    GenerationItemNotFoundError,
    GenerationPreviewStaleError,
    GenerationRunNotConfirmableError,
    GenerationRunNotFoundError,
)
from app.services.tax_period_service import TaxPeriodClosedError, TaxPeriodNotFoundError

router = APIRouter(prefix="/api/v1/declarations/generation-runs", tags=["declaration-generation"])


@router.post("/preview", response_model=DeclarationGenerationRunOut, status_code=status.HTTP_201_CREATED)
def preview(
    payload: GenerationPreviewRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> DeclarationGenerationRunOut:
    try:
        run = declaration_generation_service.preview(
            db,
            current_user.tenant_id,
            current_user.id,
            tax_period_id=payload.tax_period_id,
            filters=payload.filters,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.DECLARATION_GENERATION_PREVIEW,
            entity_type="declaration_generation_run",
            entity_id=run.id,
            metadata={"total_analyzed": run.total_analyzed},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(run)
        items = declaration_generation_service.list_items(db, current_user.tenant_id, run.id)
        out = DeclarationGenerationRunOut.model_validate(run)
        return out.model_copy(update={"items": items})
    except TaxPeriodNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TaxPeriodClosedError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/{run_id}", response_model=DeclarationGenerationRunOut)
def get_run(
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeclarationGenerationRunOut:
    try:
        run = declaration_generation_service.get_run(db, current_user.tenant_id, run_id)
        items = declaration_generation_service.list_items(db, current_user.tenant_id, run_id)
        out = DeclarationGenerationRunOut.model_validate(run)
        return out.model_copy(update={"items": items})
    except GenerationRunNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/{run_id}/items/{item_id}")
def set_item_excluded(
    run_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: GenerationItemExcludeRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> dict:
    try:
        declaration_generation_service.set_item_excluded(
            db, current_user.tenant_id, run_id, item_id, payload.excluded_by_user
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.DECLARATION_GENERATION_ITEM_EXCLUDE,
            entity_type="declaration_generation_run_item",
            entity_id=item_id,
            metadata={"excluded_by_user": payload.excluded_by_user},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        return {"status": "ok"}
    except (GenerationRunNotFoundError, GenerationItemNotFoundError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except GenerationRunNotConfirmableError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/{run_id}/confirm", response_model=DeclarationGenerationRunOut)
def confirm(
    run_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> DeclarationGenerationRunOut:
    try:
        run = declaration_generation_service.confirm(db, current_user.tenant_id, current_user.id, run_id)
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.DECLARATION_GENERATION_CONFIRM,
            entity_type="declaration_generation_run",
            entity_id=run.id,
            metadata={"status": run.status, "total_created": run.total_created},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(run)
        items = declaration_generation_service.list_items(db, current_user.tenant_id, run.id)
        out = DeclarationGenerationRunOut.model_validate(run)
        return out.model_copy(update={"items": items})
    except GenerationRunNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except GenerationPreviewStaleError as exc:
        db.commit()  # el estado STALE + stale_diff ya se persistieron antes de la excepcion
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "stale_diff": exc.stale_diff},
        ) from exc
    except GenerationRunNotConfirmableError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/{run_id}/refresh-preview", response_model=DeclarationGenerationRunOut)
def refresh_preview(
    run_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> DeclarationGenerationRunOut:
    try:
        run = declaration_generation_service.refresh_preview(db, current_user.tenant_id, run_id)
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.DECLARATION_GENERATION_REFRESH_PREVIEW,
            entity_type="declaration_generation_run",
            entity_id=run.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(run)
        items = declaration_generation_service.list_items(db, current_user.tenant_id, run.id)
        out = DeclarationGenerationRunOut.model_validate(run)
        return out.model_copy(update={"items": items})
    except GenerationRunNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except GenerationRunNotConfirmableError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/{run_id}/cancel", response_model=DeclarationGenerationRunOut)
def cancel(
    run_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> DeclarationGenerationRunOut:
    try:
        run = declaration_generation_service.cancel_run(db, current_user.tenant_id, run_id)
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.DECLARATION_GENERATION_CANCEL,
            entity_type="declaration_generation_run",
            entity_id=run.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(run)
        return DeclarationGenerationRunOut.model_validate(run)
    except GenerationRunNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except GenerationRunNotConfirmableError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
