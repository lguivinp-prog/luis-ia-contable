import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_client_ip, get_current_user, verify_csrf
from app.db.session import get_db
from app.models.audit_log import AuditAction
from app.models.user import User
from app.schemas.tax_payment import TaxPaymentCreate, TaxPaymentOut, TaxPaymentVoidRequest
from app.services import declaration_service, tax_payment_service
from app.services.audit_service import record as record_audit

router = APIRouter(prefix="/api/v1", tags=["tax-payments"])


@router.get("/declarations/{declaration_id}/tax-payments", response_model=list[TaxPaymentOut])
def list_payments(
    declaration_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TaxPaymentOut]:
    try:
        declaration_service.get_declaration(db, current_user.tenant_id, declaration_id)
    except declaration_service.DeclarationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return tax_payment_service.list_payments(db, current_user.tenant_id, declaration_id)


@router.post(
    "/declarations/{declaration_id}/tax-payments",
    response_model=TaxPaymentOut,
    status_code=status.HTTP_201_CREATED,
)
def create_payment(
    declaration_id: uuid.UUID,
    payload: TaxPaymentCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> TaxPaymentOut:
    try:
        declaration = tax_payment_service.lock_declaration(
            db, current_user.tenant_id, declaration_id
        )
        payment = tax_payment_service.create_payment(
            db,
            current_user.tenant_id,
            declaration,
            idempotency_key=payload.idempotency_key,
            amount=payload.amount,
            paid_at=payload.paid_at,
            payment_method=payload.payment_method,
            operation_number=payload.operation_number,
            reference=payload.reference,
            observations=payload.observations,
            overpayment_confirmed=payload.overpayment_confirmed,
            overpayment_reason=payload.overpayment_reason,
            actor_user_id=current_user.id,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TAX_PAYMENT_CREATE,
            entity_type="tax_payment",
            entity_id=payment.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        if payment.overpayment_confirmed:
            record_audit(
                db,
                tenant_id=current_user.tenant_id,
                user_id=current_user.id,
                action=AuditAction.TAX_PAYMENT_OVERPAYMENT_CONFIRM,
                entity_type="tax_payment",
                entity_id=payment.id,
                metadata={"overpayment_amount": str(payment.overpayment_amount)},
                ip_address=get_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
        db.commit()
        db.refresh(payment)
        return payment
    except tax_payment_service.DeclarationNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except tax_payment_service.NoCurrentAssessmentError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except tax_payment_service.IdempotencyKeyConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except tax_payment_service.OverpaymentConfirmationRequiredError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except tax_payment_service.MissingOverpaymentReasonError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post(
    "/declarations/{declaration_id}/tax-payments/{payment_id}/void",
    response_model=TaxPaymentOut,
)
def void_payment(
    declaration_id: uuid.UUID,
    payment_id: uuid.UUID,
    payload: TaxPaymentVoidRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> TaxPaymentOut:
    try:
        declaration = tax_payment_service.lock_declaration(
            db, current_user.tenant_id, declaration_id
        )
        payment = tax_payment_service.void_payment(
            db,
            current_user.tenant_id,
            declaration,
            payment_id,
            reason=payload.reason,
            actor_user_id=current_user.id,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TAX_PAYMENT_VOID,
            entity_type="tax_payment",
            entity_id=payment.id,
            metadata={"reason": payload.reason},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(payment)
        return payment
    except tax_payment_service.DeclarationNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except tax_payment_service.TaxPaymentNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except tax_payment_service.PaymentAlreadyVoidedError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except tax_payment_service.MissingVoidReasonError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
