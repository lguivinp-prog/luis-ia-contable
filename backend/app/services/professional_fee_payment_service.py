"""Pagos de cargos de honorarios (ver "Honorarios profesionales" /
"Concurrencia financiera" del plan / ADR-0041/0042/0043). Ningun metodo
ejecuta `commit()`/`rollback()`. El pago y la anulacion bloquean
`SELECT ... FOR UPDATE` sobre `professional_fee_charges`, NUNCA sobre
`declarations` -- raiz transaccional totalmente independiente de impuestos
(ver "Independencia impuestos/honorarios")."""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core import clock
from app.models.professional_fee import (
    ProfessionalFeeCharge,
    ProfessionalFeeChargeStatusHistory,
    ProfessionalFeePayment,
    ProfessionalFeePaymentHistory,
)
from app.repositories import professional_fee_repository
from app.services.professional_fee_charge_status import compute_charge_payment_status


class ChargeNotFoundError(Exception):
    pass


class ChargeNotIssuedError(Exception):
    pass


class ChargeCancelledError(Exception):
    pass


class PaymentNotFoundError(Exception):
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


def lock_charge(db: Session, tenant_id: uuid.UUID, charge_id: uuid.UUID) -> ProfessionalFeeCharge:
    charge = professional_fee_repository.lock_charge_for_update(db, tenant_id, charge_id)
    if charge is None:
        raise ChargeNotFoundError(f"Cargo {charge_id} no encontrado")
    return charge


def list_payments(
    db: Session, tenant_id: uuid.UUID, charge_id: uuid.UUID
) -> list[ProfessionalFeePayment]:
    return professional_fee_repository.list_payments_for_charge(db, tenant_id, charge_id)


def _idempotency_payload_matches(payment: ProfessionalFeePayment, payload: dict) -> bool:
    return (
        payment.charge_id == payload["charge_id"]
        and payment.amount == payload["amount"]
        and payment.paid_at == payload["paid_at"]
        and payment.payment_method == payload["payment_method"]
    )


def _recalculate_charge_status(
    db: Session,
    tenant_id: uuid.UUID,
    charge: ProfessionalFeeCharge,
    *,
    actor_user_id: uuid.UUID | None,
    now,
    correlation_id: uuid.UUID,
    reason: str | None = None,
) -> None:
    if charge.status in ("PENDING_ISSUANCE", "CANCELLED"):
        return
    if charge.due_date is None and charge.status == "ISSUED":
        # Sin fecha de vencimiento no hay seguimiento de cobranza exigible
        # (ver `issue_charge`/`compute_fee_collection_risk`).
        return

    total_paid = professional_fee_repository.sum_valid_amount_for_charge(db, tenant_id, charge.id)
    from_status = charge.status
    new_status = compute_charge_payment_status(amount=charge.amount, total_paid=total_paid)
    if new_status == from_status:
        return
    charge.status = new_status
    professional_fee_repository.create_charge_status_history(
        db,
        ProfessionalFeeChargeStatusHistory(
            tenant_id=tenant_id,
            charge_id=charge.id,
            actor_user_id=actor_user_id,
            created_at=now,
            reason=reason,
            origin="MANUAL",
            audit_correlation_id=correlation_id,
            from_status=from_status,
            to_status=new_status,
        ),
    )


def create_payment(
    db: Session,
    tenant_id: uuid.UUID,
    charge: ProfessionalFeeCharge,
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
) -> ProfessionalFeePayment:
    if charge.status == "CANCELLED":
        raise ChargeCancelledError("El cargo esta cancelado; no admite pagos")
    if charge.status == "PENDING_ISSUANCE":
        raise ChargeNotIssuedError("El cargo debe emitirse antes de registrar pagos")

    payload = {
        "charge_id": charge.id,
        "amount": amount,
        "paid_at": paid_at,
        "payment_method": payment_method,
    }
    existing = professional_fee_repository.get_payment_by_idempotency_key(db, tenant_id, idempotency_key)
    if existing is not None:
        if _idempotency_payload_matches(existing, payload):
            return existing
        raise IdempotencyKeyConflictError(
            "La clave de idempotencia ya se uso con un pago de datos distintos"
        )

    total_paid_so_far = professional_fee_repository.sum_valid_amount_for_charge(
        db, tenant_id, charge.id
    )
    overpay_excess = (total_paid_so_far + amount) - charge.amount
    is_overpayment = overpay_excess > 0
    if is_overpayment:
        if not overpayment_confirmed:
            raise OverpaymentConfirmationRequiredError(
                "El pago excede el saldo del cargo; confirme el sobrepago explicitamente"
            )
        if not overpayment_reason or not overpayment_reason.strip():
            raise MissingOverpaymentReasonError("Debe indicar el motivo del sobrepago")

    now = clock.utcnow()
    correlation_id = uuid.uuid4()

    payment = ProfessionalFeePayment(
        tenant_id=tenant_id,
        charge_id=charge.id,
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
    professional_fee_repository.create_payment(db, payment)

    professional_fee_repository.create_payment_history(
        db,
        ProfessionalFeePaymentHistory(
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

    _recalculate_charge_status(
        db, tenant_id, charge, actor_user_id=actor_user_id, now=now, correlation_id=correlation_id
    )
    db.flush()
    return payment


def void_payment(
    db: Session,
    tenant_id: uuid.UUID,
    charge: ProfessionalFeeCharge,
    payment_id: uuid.UUID,
    *,
    reason: str,
    actor_user_id: uuid.UUID | None,
) -> ProfessionalFeePayment:
    if not reason or not reason.strip():
        raise MissingVoidReasonError("La anulacion requiere un motivo explicito")

    payment = professional_fee_repository.get_payment_by_id(db, tenant_id, payment_id)
    if payment is None or payment.charge_id != charge.id:
        raise PaymentNotFoundError(f"Pago {payment_id} no encontrado")
    if payment.is_voided:
        raise PaymentAlreadyVoidedError("El pago ya se encuentra anulado")

    now = clock.utcnow()
    correlation_id = uuid.uuid4()

    payment.is_voided = True
    payment.voided_at = now
    payment.voided_by = actor_user_id
    payment.void_reason = reason

    professional_fee_repository.create_payment_history(
        db,
        ProfessionalFeePaymentHistory(
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

    _recalculate_charge_status(
        db,
        tenant_id,
        charge,
        actor_user_id=actor_user_id,
        now=now,
        correlation_id=correlation_id,
        reason=reason,
    )
    db.flush()
    return payment
