import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core import clock
from app.core.dependencies import ensure_admin_role, get_client_ip, get_current_user, verify_csrf
from app.db.session import get_db
from app.models.audit_log import AuditAction
from app.models.declaration import Declaration
from app.models.user import User
from app.repositories import declaration_repository
from app.schemas.declaration import (
    AssignRequest,
    CancelRequest,
    CloseRequest,
    DeclarationCreateRequest,
    DeclarationHistoryOut,
    DeclarationOut,
    FilingCorrectRequest,
    FilingRequest,
    RecalculatePreviewOut,
    RecalculateRequest,
    ReopenRequest,
    WorkflowStatusChangeRequest,
)
from app.services import declaration_risk, declaration_service
from app.services.audit_service import record as record_audit
from app.services.client_service import ClientNotFoundError
from app.services.declaration_service import (
    CloseOverrideDisabledError,
    CloseRequiresPendingOverrideError,
    DeclarationCancelledError,
    DeclarationClosedError,
    DeclarationNotFoundError,
    DuplicateDeclarationError,
    FilingRequiresPresentationDateError,
    InvalidFilingTransitionError,
    InvalidReopenTargetError,
    InvalidWorkflowTransitionError,
    MissingCancellationReasonError,
    MissingCloseOverrideReasonError,
    MissingRecalculationReasonError,
    MissingReopenReasonError,
    MissingWorkflowRegressionReasonError,
    ReadyToFileRequiresDocumentsOrJustificationError,
    ScheduleNotResolvableForPeriodError,
    StaleRowVersionError,
)
from app.services.tax_period_service import TaxPeriodClosedError, TaxPeriodNotFoundError

router = APIRouter(prefix="/api/v1/declarations", tags=["declarations"])


def _to_out(declaration: Declaration) -> DeclarationOut:
    out = DeclarationOut.model_validate(declaration)
    risk = declaration_risk.compute_risk_status(
        declaration.current_effective_due_date,
        declaration.workflow_status,
        declaration.filing_status,
        clock.utcnow().date(),
    )
    return out.model_copy(update={"computed_risk_status": risk})


@router.get("", response_model=list[DeclarationOut])
def list_declarations(
    tax_period_id: uuid.UUID | None = None,
    client_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[DeclarationOut]:
    items, _total = declaration_repository.list_for_dashboard(
        db,
        current_user.tenant_id,
        tax_period_id=tax_period_id,
        client_id=client_id,
        limit=limit,
        offset=offset,
    )
    return [_to_out(d) for d in items]


@router.post("", response_model=DeclarationOut, status_code=status.HTTP_201_CREATED)
def create_declaration(
    payload: DeclarationCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> DeclarationOut:
    try:
        declaration = declaration_service.create_declaration(
            db,
            current_user.tenant_id,
            current_user.id,
            client_id=payload.client_id,
            client_obligation_id=payload.client_obligation_id,
            tax_period_id=payload.tax_period_id,
            assigned_user_id=payload.assigned_user_id,
            origin="MANUAL",
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.DECLARATION_CREATE,
            entity_type="declaration",
            entity_id=declaration.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(declaration)
        return _to_out(declaration)
    except (ClientNotFoundError, DeclarationNotFoundError, TaxPeriodNotFoundError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TaxPeriodClosedError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except DuplicateDeclarationError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ScheduleNotResolvableForPeriodError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/{declaration_id}", response_model=DeclarationOut)
def get_declaration(
    declaration_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeclarationOut:
    try:
        declaration = declaration_service.get_declaration(db, current_user.tenant_id, declaration_id)
        return _to_out(declaration)
    except DeclarationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/{declaration_id}/workflow-status", response_model=DeclarationOut)
def change_workflow_status(
    declaration_id: uuid.UUID,
    payload: WorkflowStatusChangeRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> DeclarationOut:
    try:
        declaration = declaration_service.change_workflow_status(
            db,
            current_user.tenant_id,
            current_user.id,
            declaration_id,
            target=payload.target,
            reason=payload.reason,
            row_version=payload.row_version,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.DECLARATION_WORKFLOW_CHANGE,
            entity_type="declaration",
            entity_id=declaration.id,
            metadata={"target": payload.target},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(declaration)
        return _to_out(declaration)
    except DeclarationNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except StaleRowVersionError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (DeclarationClosedError, DeclarationCancelledError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvalidWorkflowTransitionError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (
        MissingWorkflowRegressionReasonError,
        ReadyToFileRequiresDocumentsOrJustificationError,
    ) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.patch("/{declaration_id}/assign", response_model=DeclarationOut)
def assign_declaration(
    declaration_id: uuid.UUID,
    payload: AssignRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> DeclarationOut:
    try:
        declaration = declaration_service.assign(
            db,
            current_user.tenant_id,
            current_user.id,
            declaration_id,
            target_user_id=payload.user_id,
            reason=payload.reason,
            row_version=payload.row_version,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.DECLARATION_ASSIGN,
            entity_type="declaration",
            entity_id=declaration.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(declaration)
        return _to_out(declaration)
    except DeclarationNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except StaleRowVersionError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (DeclarationClosedError, DeclarationCancelledError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.patch("/{declaration_id}/filing", response_model=DeclarationOut)
def file_declaration(
    declaration_id: uuid.UUID,
    payload: FilingRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> DeclarationOut:
    try:
        declaration = declaration_service.file_declaration(
            db,
            current_user.tenant_id,
            current_user.id,
            declaration_id,
            target=payload.target,
            presentation_date=payload.presentation_date,
            order_number=payload.order_number,
            reason=payload.reason,
            row_version=payload.row_version,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.DECLARATION_FILE,
            entity_type="declaration",
            entity_id=declaration.id,
            metadata={"target": payload.target},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(declaration)
        return _to_out(declaration)
    except DeclarationNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except StaleRowVersionError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (DeclarationClosedError, DeclarationCancelledError, InvalidFilingTransitionError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except FilingRequiresPresentationDateError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.patch("/{declaration_id}/filing/correct", response_model=DeclarationOut)
def correct_filing(
    declaration_id: uuid.UUID,
    payload: FilingCorrectRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> DeclarationOut:
    try:
        declaration = declaration_service.correct_filing(
            db,
            current_user.tenant_id,
            current_user.id,
            declaration_id,
            target=payload.target,
            reason=payload.reason,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.DECLARATION_FILING_CORRECT,
            entity_type="declaration",
            entity_id=declaration.id,
            metadata={"target": payload.target, "reason": payload.reason},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(declaration)
        return _to_out(declaration)
    except DeclarationNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (DeclarationClosedError, DeclarationCancelledError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except MissingRecalculationReasonError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/{declaration_id}/recalculate-preview", response_model=RecalculatePreviewOut)
def recalculate_preview(
    declaration_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RecalculatePreviewOut:
    try:
        return declaration_service.recalculate_preview(db, current_user.tenant_id, declaration_id)
    except DeclarationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ScheduleNotResolvableForPeriodError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/{declaration_id}/recalculate", response_model=DeclarationOut)
def recalculate(
    declaration_id: uuid.UUID,
    payload: RecalculateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> DeclarationOut:
    try:
        declaration = declaration_service.recalculate(
            db,
            current_user.tenant_id,
            current_user.id,
            declaration_id,
            reason=payload.reason,
            row_version=payload.row_version,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.DECLARATION_RECALCULATE,
            entity_type="declaration",
            entity_id=declaration.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(declaration)
        return _to_out(declaration)
    except DeclarationNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except StaleRowVersionError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (DeclarationClosedError, DeclarationCancelledError, TaxPeriodClosedError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except MissingRecalculationReasonError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ScheduleNotResolvableForPeriodError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/{declaration_id}/reopen", response_model=DeclarationOut)
def reopen_declaration(
    declaration_id: uuid.UUID,
    payload: ReopenRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> DeclarationOut:
    try:
        declaration = declaration_service.reopen(
            db,
            current_user.tenant_id,
            current_user.id,
            declaration_id,
            reason=payload.reason,
            target_workflow_status=payload.target_workflow_status,
            row_version=payload.row_version,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.DECLARATION_REOPEN,
            entity_type="declaration",
            entity_id=declaration.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(declaration)
        return _to_out(declaration)
    except DeclarationNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except StaleRowVersionError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (MissingReopenReasonError, InvalidReopenTargetError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/{declaration_id}/close", response_model=DeclarationOut)
def close_declaration(
    declaration_id: uuid.UUID,
    payload: CloseRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> DeclarationOut:
    try:
        if payload.force_close_with_pending:
            ensure_admin_role(current_user)
        declaration = declaration_service.close(
            db,
            current_user.tenant_id,
            current_user.id,
            declaration_id,
            force_close_with_pending=payload.force_close_with_pending,
            close_override_reason=payload.close_override_reason,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.DECLARATION_CLOSE,
            entity_type="declaration",
            entity_id=declaration.id,
            metadata={"closed_with_pending": declaration.closed_with_pending},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(declaration)
        return _to_out(declaration)
    except DeclarationNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (DeclarationClosedError, DeclarationCancelledError, InvalidWorkflowTransitionError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except CloseRequiresPendingOverrideError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (CloseOverrideDisabledError, MissingCloseOverrideReasonError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/{declaration_id}/cancel", response_model=DeclarationOut)
def cancel_declaration(
    declaration_id: uuid.UUID,
    payload: CancelRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> DeclarationOut:
    try:
        declaration = declaration_service.cancel(
            db, current_user.tenant_id, current_user.id, declaration_id, reason=payload.reason
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.DECLARATION_CANCEL,
            entity_type="declaration",
            entity_id=declaration.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(declaration)
        return _to_out(declaration)
    except DeclarationNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (DeclarationClosedError, DeclarationCancelledError, InvalidWorkflowTransitionError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except MissingCancellationReasonError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/{declaration_id}/history", response_model=DeclarationHistoryOut)
def get_history(
    declaration_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DeclarationHistoryOut:
    try:
        history = declaration_service.get_history(db, current_user.tenant_id, declaration_id)
        return DeclarationHistoryOut(**history)
    except DeclarationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
