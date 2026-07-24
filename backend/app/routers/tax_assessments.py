import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_client_ip, verify_csrf
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.models.audit_log import AuditAction
from app.models.user import User
from app.schemas.tax_assessment import (
    TaxAssessmentCorrectionCreate,
    TaxAssessmentCreate,
    TaxAssessmentOut,
)
from app.services import declaration_service, tax_assessment_service
from app.services.audit_service import record as record_audit

router = APIRouter(prefix="/api/v1", tags=["tax-assessments"])


@router.get(
    "/declarations/{declaration_id}/tax-assessments", response_model=list[TaxAssessmentOut]
)
def list_assessments(
    declaration_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.TAX_ASSESSMENT_READ)),
) -> list[TaxAssessmentOut]:
    try:
        declaration_service.get_declaration(db, current_user.tenant_id, declaration_id)
    except declaration_service.DeclarationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return tax_assessment_service.list_assessments(db, current_user.tenant_id, declaration_id)


@router.post(
    "/declarations/{declaration_id}/tax-assessments",
    response_model=TaxAssessmentOut,
    status_code=status.HTTP_201_CREATED,
)
def create_assessment(
    declaration_id: uuid.UUID,
    payload: TaxAssessmentCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.TAX_ASSESSMENT_WRITE)),
    _csrf: None = Depends(verify_csrf),
) -> TaxAssessmentOut:
    try:
        declaration = tax_assessment_service.lock_declaration(
            db, current_user.tenant_id, declaration_id
        )
        assessment = tax_assessment_service.create_initial_assessment(
            db,
            current_user.tenant_id,
            declaration,
            gross_tax_amount=payload.gross_tax_amount,
            credits_applied=payload.credits_applied,
            observations=payload.observations,
            actor_user_id=current_user.id,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TAX_ASSESSMENT_CREATE,
            entity_type="tax_assessment",
            entity_id=assessment.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(assessment)
        return assessment
    except tax_assessment_service.DeclarationNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except tax_assessment_service.AssessmentAlreadyExistsError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/declarations/{declaration_id}/tax-assessments/correct",
    response_model=TaxAssessmentOut,
)
def correct_assessment(
    declaration_id: uuid.UUID,
    payload: TaxAssessmentCorrectionCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.TAX_ASSESSMENT_WRITE)),
    _csrf: None = Depends(verify_csrf),
) -> TaxAssessmentOut:
    try:
        declaration = tax_assessment_service.lock_declaration(
            db, current_user.tenant_id, declaration_id
        )
        assessment = tax_assessment_service.correct_assessment(
            db,
            current_user.tenant_id,
            declaration,
            gross_tax_amount=payload.gross_tax_amount,
            credits_applied=payload.credits_applied,
            observations=payload.observations,
            reason=payload.reason,
            actor_user_id=current_user.id,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TAX_ASSESSMENT_CORRECT,
            entity_type="tax_assessment",
            entity_id=assessment.id,
            metadata={"reason": payload.reason},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(assessment)
        return assessment
    except tax_assessment_service.DeclarationNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except tax_assessment_service.NoCurrentAssessmentError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except tax_assessment_service.MissingCorrectionReasonError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
