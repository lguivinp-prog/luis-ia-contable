"""Pagos tributarios (ver "Pagos tributarios" / "Concurrencia financiera" del
plan / ADR-0041/0042/0043). Ningun metodo de este servicio ejecuta
`commit()`/`rollback()`: usa `flush()` unicamente; el router controla la
transaccion. El saldo SIEMPRE se calcula sumando TODOS los pagos validos de
la declaracion contra la determinacion vigente actual -- nunca filtrado por
`assessment_id` de un pago concreto."""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core import clock
from app.models.declaration import Declaration, DeclarationStatusHistory
from app.models.tax_payment import TaxPayment, TaxPaymentHistory
from app.repositories import declaration_repository, tax_assessment_repository, tax_payment_repository
from app.services.tax_payment_status import compute_tax_payment_status


class DeclarationNotFoundError(Exception):
    pass


class NoCurrentAssessmentError(Exception):
    pass


class TaxPaymentNotFoundError(Exception):
    pass


class IdempotencyKeyConflictError(Exception):
    pass


class OverpaymentConfirmationRequiredError(Exception):
    pass


class MissingOverpaymentReasonError(Exception):
    pass


class PaymentAlreadyVoidedError(Exception):
    pass


class MissingVoidReasonError(Exception):
    pass


def lock_declaration(db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID) -> Declaration:
    declaration = declaration_repository.lock_for_update(db, tenant_id, declaration_id)
    if declaration is None:
        raise DeclarationNotFoundError(f"Declaracion {declaration_id} no encontrada")
    return declaration


def list_payments(db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID) -> list[TaxPayment]:
    return tax_payment_repository.list_for_declaration(db, tenant_id, declaration_id)


def _idempotency_payload_matches(payment: TaxPayment, payload: dict) -> bool:
    return (
        payment.declaration_id == payload["declaration_id"]
        and payment.amount == payload["amount"]
        and payment.paid_at == payload["paid_at"]
        and payment.payment_method == payload["payment_method"]
        and payment.assessment_id == payload["assessment_id"]
    )


def _recalculate_tax_payment_status(
    db: Session,
    tenant_id: uuid.UUID,
    declaration: Declaration,
    assessment,
    *,
    actor_user_id: uuid.UUID | None,
    now,
    correlation_id: uuid.UUID,
    reason: str | None = None,
) -> None:
    total_paid = tax_payment_repository.sum_valid_amount_for_declaration(db, tenant_id, declaration.id)
    from_status = declaration.tax_payment_status
    new_status = compute_tax_payment_status(
        total_payable=assessment.total_payable,
        remaining_credit=assessment.remaining_credit,
        total_paid=total_paid,
    )
    if new_status == from_status:
        return
    declaration.tax_payment_status = new_status
    declaration_repository.create_status_history_entry(
        db,
        DeclarationStatusHistory(
            tenant_id=tenant_id,
            declaration_id=declaration.id,
            actor_user_id=actor_user_id,
            created_at=now,
            reason=reason,
            origin="MANUAL",
            audit_correlation_id=correlation_id,
            status_field="TAX_PAYMENT",
            from_value=from_status,
            to_value=new_status,
        ),
    )


def create_payment(
    db: Session,
    tenant_id: uuid.UUID,
    declaration: Declaration,
    *,
    idempotency_key: uuid.UUID,
    amount: Decimal,
    paid_at: date,
    payment_method: str,
    operation_number: str | None,
    reference: str | None,
    observations: str | None,
    overpayment_confirmed: bool,
    overpayment_reason: str | None,
    actor_user_id: uuid.UUID | None,
) -> TaxPayment:
    """Crea un pago dentro del bloqueo `FOR UPDATE` de `declaration` (ver
    `lock_declaration`). Idempotente por `(tenant_id, idempotency_key)`:
    mismo payload -> devuelve el pago existente; payload distinto -> 409
    (`IdempotencyKeyConflictError`), independiente del bloqueo (que protege
    contra carreras de saldo, no contra reenvios deliberados)."""
    current_assessment = tax_assessment_repository.get_current(db, tenant_id, declaration.id)
    if current_assessment is None:
        raise NoCurrentAssessmentError("La declaracion no tiene una determinacion vigente")

    payload = {
        "declaration_id": declaration.id,
        "amount": amount,
        "paid_at": paid_at,
        "payment_method": payment_method,
        "assessment_id": current_assessment.id,
    }
    existing = tax_payment_repository.get_by_idempotency_key(db, tenant_id, idempotency_key)
    if existing is not None:
        if _idempotency_payload_matches(existing, payload):
            return existing
        raise IdempotencyKeyConflictError(
            "La clave de idempotencia ya se uso con un pago de datos distintos"
        )

    total_paid_so_far = tax_payment_repository.sum_valid_amount_for_declaration(
        db, tenant_id, declaration.id
    )
    overpay_excess = (total_paid_so_far + amount) - current_assessment.total_payable
    is_overpayment = overpay_excess > 0
    if is_overpayment:
        if not overpayment_confirmed:
            raise OverpaymentConfirmationRequiredError(
                "El pago excede el saldo pendiente; confirme el sobrepago explicitamente"
            )
        if not overpayment_reason or not overpayment_reason.strip():
            raise MissingOverpaymentReasonError("Debe indicar el motivo del sobrepago")

    now = clock.utcnow()
    correlation_id = uuid.uuid4()

    payment = TaxPayment(
        tenant_id=tenant_id,
        declaration_id=declaration.id,
        assessment_id=current_assessment.id,
        paid_at=paid_at,
        amount=amount,
        payment_method=payment_method,
        operation_number=operation_number,
        reference=reference,
        observations=observations,
        overpayment_confirmed=is_overpayment,
        overpayment_reason=overpayment_reason if is_overpayment else None,
        overpayment_amount=overpay_excess if is_overpayment else None,
        confirmed_by=actor_user_id if is_overpayment else None,
        confirmed_at=now if is_overpayment else None,
        created_by=actor_user_id,
        idempotency_key=idempotency_key,
    )
    tax_payment_repository.create(db, payment)

    tax_payment_repository.create_history(
        db,
        TaxPaymentHistory(
            tenant_id=tenant_id,
            payment_id=payment.id,
            actor_user_id=actor_user_id,
            created_at=now,
            origin="MANUAL",
            audit_correlation_id=correlation_id,
            from_status=None,
            to_status="ACTIVE",
        ),
    )

    _recalculate_tax_payment_status(
        db,
        tenant_id,
        declaration,
        current_assessment,
        actor_user_id=actor_user_id,
        now=now,
        correlation_id=correlation_id,
    )
    db.flush()
    return payment


def void_payment(
    db: Session,
    tenant_id: uuid.UUID,
    declaration: Declaration,
    payment_id: uuid.UUID,
    *,
    reason: str,
    actor_user_id: uuid.UUID | None,
) -> TaxPayment:
    if not reason or not reason.strip():
        raise MissingVoidReasonError("La anulacion requiere un motivo explicito")

    payment = tax_payment_repository.get_by_id(db, tenant_id, payment_id)
    if payment is None or payment.declaration_id != declaration.id:
        raise TaxPaymentNotFoundError(f"Pago {payment_id} no encontrado")
    if payment.is_voided:
        raise PaymentAlreadyVoidedError("El pago ya se encuentra anulado")

    now = clock.utcnow()
    correlation_id = uuid.uuid4()

    payment.is_voided = True
    payment.voided_at = now
    payment.voided_by = actor_user_id
    payment.void_reason = reason

    tax_payment_repository.create_history(
        db,
        TaxPaymentHistory(
            tenant_id=tenant_id,
            payment_id=payment.id,
            actor_user_id=actor_user_id,
            created_at=now,
            reason=reason,
            origin="MANUAL",
            audit_correlation_id=correlation_id,
            from_status="ACTIVE",
            to_status="VOIDED",
        ),
    )

    current_assessment = tax_assessment_repository.get_current(db, tenant_id, declaration.id)
    if current_assessment is not None:
        _recalculate_tax_payment_status(
            db,
            tenant_id,
            declaration,
            current_assessment,
            actor_user_id=actor_user_id,
            now=now,
            correlation_id=correlation_id,
            reason=reason,
        )
    db.flush()
    return payment
