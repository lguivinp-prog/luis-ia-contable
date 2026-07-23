import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_client_ip, get_current_user, verify_csrf
from app.db.session import get_db
from app.models.audit_log import AuditAction
from app.models.user import User
from app.schemas.professional_fee import (
    ProfessionalFeeAgreementCreate,
    ProfessionalFeeAgreementLifecycleRequest,
    ProfessionalFeeAgreementOut,
    ProfessionalFeeChargeCreate,
    ProfessionalFeeChargeIssueRequest,
    ProfessionalFeeChargeOut,
    ProfessionalFeePaymentCreate,
    ProfessionalFeePaymentOut,
    ProfessionalFeePaymentVoidRequest,
    ProfessionalReceiptCreate,
    ProfessionalReceiptOut,
    ProfessionalReceiptVoidRequest,
)
from app.services import (
    professional_fee_payment_service,
    professional_fee_receipt_service,
    professional_fee_service,
)
from app.services.audit_service import record as record_audit
from app.services.client_service import ClientNotFoundError, get_client

router = APIRouter(prefix="/api/v1", tags=["professional-fees"])


# --- acuerdos ---


@router.get(
    "/clients/{client_id}/fee-agreements", response_model=list[ProfessionalFeeAgreementOut]
)
def list_agreements(
    client_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ProfessionalFeeAgreementOut]:
    try:
        get_client(db, current_user.tenant_id, client_id)
    except ClientNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return professional_fee_service.list_agreements(db, current_user.tenant_id, client_id)


@router.post(
    "/clients/{client_id}/fee-agreements",
    response_model=ProfessionalFeeAgreementOut,
    status_code=status.HTTP_201_CREATED,
)
def create_agreement(
    client_id: uuid.UUID,
    payload: ProfessionalFeeAgreementCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ProfessionalFeeAgreementOut:
    try:
        agreement = professional_fee_service.create_agreement(
            db,
            current_user.tenant_id,
            client_id,
            service_type=payload.service_type,
            amount=payload.amount,
            billing_day=payload.billing_day,
            included_services=payload.included_services,
            description=payload.description,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            observations=payload.observations,
            actor_user_id=current_user.id,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.FEE_AGREEMENT_CREATE,
            entity_type="professional_fee_agreement",
            entity_id=agreement.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(agreement)
        return agreement
    except ClientNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except professional_fee_service.AgreementOverlapError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/fee-agreements/{agreement_id}/supersede", response_model=ProfessionalFeeAgreementOut
)
def supersede_agreement(
    agreement_id: uuid.UUID,
    payload: ProfessionalFeeAgreementLifecycleRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ProfessionalFeeAgreementOut:
    try:
        agreement = professional_fee_service.get_agreement(db, current_user.tenant_id, agreement_id)
        agreement = professional_fee_service.supersede_agreement(
            db,
            current_user.tenant_id,
            agreement,
            new_valid_until=payload.effective_date,
            reason=payload.reason,
            actor_user_id=current_user.id,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.FEE_AGREEMENT_SUPERSEDE,
            entity_type="professional_fee_agreement",
            entity_id=agreement.id,
            metadata={"reason": payload.reason},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(agreement)
        return agreement
    except professional_fee_service.AgreementNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except professional_fee_service.AgreementNotActiveError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except professional_fee_service.InvalidValidUntilError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except professional_fee_service.MissingLifecycleReasonError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post("/fee-agreements/{agreement_id}/cancel", response_model=ProfessionalFeeAgreementOut)
def cancel_agreement(
    agreement_id: uuid.UUID,
    payload: ProfessionalFeeAgreementLifecycleRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ProfessionalFeeAgreementOut:
    try:
        agreement = professional_fee_service.get_agreement(db, current_user.tenant_id, agreement_id)
        agreement = professional_fee_service.cancel_agreement(
            db,
            current_user.tenant_id,
            agreement,
            effective_date=payload.effective_date,
            reason=payload.reason,
            actor_user_id=current_user.id,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.FEE_AGREEMENT_CANCEL,
            entity_type="professional_fee_agreement",
            entity_id=agreement.id,
            metadata={"reason": payload.reason},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(agreement)
        return agreement
    except professional_fee_service.AgreementNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except professional_fee_service.AgreementNotActiveError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except professional_fee_service.InvalidValidUntilError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except professional_fee_service.MissingLifecycleReasonError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


# --- cargos ---


@router.get(
    "/fee-agreements/{agreement_id}/charges", response_model=list[ProfessionalFeeChargeOut]
)
def list_charges(
    agreement_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ProfessionalFeeChargeOut]:
    try:
        professional_fee_service.get_agreement(db, current_user.tenant_id, agreement_id)
    except professional_fee_service.AgreementNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return professional_fee_service.list_charges(db, current_user.tenant_id, agreement_id)


@router.post(
    "/fee-agreements/{agreement_id}/charges",
    response_model=ProfessionalFeeChargeOut,
    status_code=status.HTTP_201_CREATED,
)
def create_charge(
    agreement_id: uuid.UUID,
    payload: ProfessionalFeeChargeCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ProfessionalFeeChargeOut:
    try:
        agreement = professional_fee_service.get_agreement(db, current_user.tenant_id, agreement_id)
        charge = professional_fee_service.create_charge(
            db,
            current_user.tenant_id,
            agreement,
            tax_period_id=payload.tax_period_id,
            amount=payload.amount,
            due_date=payload.due_date,
            observations=payload.observations,
            actor_user_id=current_user.id,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.FEE_CHARGE_CREATE,
            entity_type="professional_fee_charge",
            entity_id=charge.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(charge)
        return charge
    except professional_fee_service.AgreementNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except professional_fee_service.TaxPeriodNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except professional_fee_service.AgreementNotActiveError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except professional_fee_service.ChargeAlreadyExistsError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/fee-charges/{charge_id}/issue", response_model=ProfessionalFeeChargeOut)
def issue_charge(
    charge_id: uuid.UUID,
    payload: ProfessionalFeeChargeIssueRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ProfessionalFeeChargeOut:
    try:
        charge = professional_fee_service.get_charge(db, current_user.tenant_id, charge_id)
        charge = professional_fee_service.issue_charge(
            db,
            current_user.tenant_id,
            charge,
            due_date=payload.due_date,
            actor_user_id=current_user.id,
        )
        db.commit()
        db.refresh(charge)
        return charge
    except professional_fee_service.ChargeNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except professional_fee_service.InvalidChargeStatusTransitionError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


# --- pagos ---


@router.get("/fee-charges/{charge_id}/payments", response_model=list[ProfessionalFeePaymentOut])
def list_payments(
    charge_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ProfessionalFeePaymentOut]:
    try:
        professional_fee_service.get_charge(db, current_user.tenant_id, charge_id)
    except professional_fee_service.ChargeNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return professional_fee_payment_service.list_payments(db, current_user.tenant_id, charge_id)


@router.post(
    "/fee-charges/{charge_id}/payments",
    response_model=ProfessionalFeePaymentOut,
    status_code=status.HTTP_201_CREATED,
)
def create_payment(
    charge_id: uuid.UUID,
    payload: ProfessionalFeePaymentCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ProfessionalFeePaymentOut:
    try:
        charge = professional_fee_payment_service.lock_charge(db, current_user.tenant_id, charge_id)
        payment = professional_fee_payment_service.create_payment(
            db,
            current_user.tenant_id,
            charge,
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
            action=AuditAction.FEE_PAYMENT_CREATE,
            entity_type="professional_fee_payment",
            entity_id=payment.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(payment)
        return payment
    except professional_fee_payment_service.ChargeNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except professional_fee_payment_service.ChargeCancelledError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except professional_fee_payment_service.ChargeNotIssuedError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except professional_fee_payment_service.IdempotencyKeyConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except professional_fee_payment_service.OverpaymentConfirmationRequiredError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except professional_fee_payment_service.MissingOverpaymentReasonError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post(
    "/fee-charges/{charge_id}/payments/{payment_id}/void",
    response_model=ProfessionalFeePaymentOut,
)
def void_payment(
    charge_id: uuid.UUID,
    payment_id: uuid.UUID,
    payload: ProfessionalFeePaymentVoidRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ProfessionalFeePaymentOut:
    try:
        charge = professional_fee_payment_service.lock_charge(db, current_user.tenant_id, charge_id)
        payment = professional_fee_payment_service.void_payment(
            db,
            current_user.tenant_id,
            charge,
            payment_id,
            reason=payload.reason,
            actor_user_id=current_user.id,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.FEE_PAYMENT_VOID,
            entity_type="professional_fee_payment",
            entity_id=payment.id,
            metadata={"reason": payload.reason},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(payment)
        return payment
    except professional_fee_payment_service.ChargeNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except professional_fee_payment_service.PaymentNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except professional_fee_payment_service.PaymentAlreadyVoidedError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except professional_fee_payment_service.MissingVoidReasonError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


# --- recibos ---


@router.get("/fee-charges/{charge_id}/receipts", response_model=list[ProfessionalReceiptOut])
def list_receipts(
    charge_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ProfessionalReceiptOut]:
    try:
        professional_fee_service.get_charge(db, current_user.tenant_id, charge_id)
    except professional_fee_service.ChargeNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return professional_fee_receipt_service.list_receipts(db, current_user.tenant_id, charge_id)


@router.post(
    "/fee-charges/{charge_id}/receipts",
    response_model=ProfessionalReceiptOut,
    status_code=status.HTTP_201_CREATED,
)
def issue_receipt(
    charge_id: uuid.UUID,
    payload: ProfessionalReceiptCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ProfessionalReceiptOut:
    try:
        charge = professional_fee_service.get_charge(db, current_user.tenant_id, charge_id)
        receipt = professional_fee_receipt_service.issue_receipt(
            db,
            current_user.tenant_id,
            charge,
            receipt_type=payload.receipt_type,
            number=payload.number,
            issued_at=payload.issued_at,
            amount=payload.amount,
            issued_by=current_user.id,
            document_reference=payload.document_reference,
            observations=payload.observations,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.FEE_RECEIPT_ISSUE,
            entity_type="professional_receipt",
            entity_id=receipt.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(receipt)
        return receipt
    except professional_fee_service.ChargeNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except professional_fee_receipt_service.ReceiptExceedsChargeAmountError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post("/fee-receipts/{receipt_id}/void", response_model=ProfessionalReceiptOut)
def void_receipt(
    receipt_id: uuid.UUID,
    payload: ProfessionalReceiptVoidRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ProfessionalReceiptOut:
    try:
        receipt = professional_fee_receipt_service.get_receipt(db, current_user.tenant_id, receipt_id)
        receipt = professional_fee_receipt_service.void_receipt(
            db, current_user.tenant_id, receipt, reason=payload.reason, actor_user_id=current_user.id
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.FEE_RECEIPT_VOID,
            entity_type="professional_receipt",
            entity_id=receipt.id,
            metadata={"reason": payload.reason},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(receipt)
        return receipt
    except professional_fee_receipt_service.ReceiptNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except professional_fee_receipt_service.ReceiptAlreadyVoidedError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except professional_fee_receipt_service.MissingVoidReasonError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
