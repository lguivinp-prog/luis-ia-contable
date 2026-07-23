import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_client_ip, get_current_user, verify_csrf
from app.db.session import get_db
from app.models.audit_log import AuditAction
from app.models.user import User
from app.schemas.tax_period import (
    TaxPeriodCloseRequest,
    TaxPeriodCreate,
    TaxPeriodOut,
    TaxPeriodReopenRequest,
    TaxPeriodStatusHistoryOut,
)
from app.services import tax_period_service
from app.services.audit_service import record as record_audit
from app.services.tax_period_service import (
    DuplicateTaxPeriodError,
    InvalidTaxPeriodError,
    MissingTaxPeriodReopenReasonError,
    TaxPeriodNotFoundError,
)

router = APIRouter(prefix="/api/v1/tax-periods", tags=["tax-periods"])


@router.get("", response_model=list[TaxPeriodOut])
def list_periods(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> list[TaxPeriodOut]:
    return tax_period_service.list_periods(db, current_user.tenant_id)


@router.post("", response_model=TaxPeriodOut, status_code=status.HTTP_201_CREATED)
def create_period(
    payload: TaxPeriodCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> TaxPeriodOut:
    try:
        period = tax_period_service.create_period(
            db,
            current_user.tenant_id,
            period_type=payload.period_type,
            fiscal_year=payload.fiscal_year,
            month=payload.month,
            canonical_code=payload.canonical_code,
            schedule_tax_period_code=payload.schedule_tax_period_code,
            display_name=payload.display_name,
            start_date=payload.start_date,
            end_date=payload.end_date,
            description=payload.description,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TAX_PERIOD_CREATE,
            entity_type="tax_period",
            entity_id=period.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(period)
        return period
    except InvalidTaxPeriodError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except DuplicateTaxPeriodError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/{period_id}", response_model=TaxPeriodOut)
def get_period(
    period_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaxPeriodOut:
    try:
        return tax_period_service.get_period(db, current_user.tenant_id, period_id)
    except TaxPeriodNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/{period_id}/close", response_model=TaxPeriodOut)
def close_period(
    period_id: uuid.UUID,
    payload: TaxPeriodCloseRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> TaxPeriodOut:
    try:
        period = tax_period_service.close_period(
            db, current_user.tenant_id, current_user.id, period_id, payload.reason
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TAX_PERIOD_CLOSE,
            entity_type="tax_period",
            entity_id=period.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(period)
        return period
    except TaxPeriodNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/{period_id}/reopen", response_model=TaxPeriodOut)
def reopen_period(
    period_id: uuid.UUID,
    payload: TaxPeriodReopenRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> TaxPeriodOut:
    try:
        period = tax_period_service.reopen_period(
            db, current_user.tenant_id, current_user.id, period_id, payload.reason
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TAX_PERIOD_REOPEN,
            entity_type="tax_period",
            entity_id=period.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(period)
        return period
    except TaxPeriodNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except MissingTaxPeriodReopenReasonError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/{period_id}/status-history", response_model=list[TaxPeriodStatusHistoryOut])
def get_status_history(
    period_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TaxPeriodStatusHistoryOut]:
    tax_period_service.get_period(db, current_user.tenant_id, period_id)
    return tax_period_service.list_status_history(db, current_user.tenant_id, period_id)
