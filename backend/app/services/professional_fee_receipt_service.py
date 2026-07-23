"""Recibos emitidos contra un cargo de honorarios (ver "Honorarios
profesionales" del plan / ADR-0046). Ningun metodo ejecuta
`commit()`/`rollback()`. Regla de importe (con prueba dedicada): un recibo
puede ser parcial, pero la suma de recibos `ISSUED` de un mismo cargo nunca
puede exceder `professional_fee_charges.amount` -- validado aqui porque es
una regla agregada, no expresable en un `CHECK` de una sola fila."""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core import clock
from app.models.professional_fee import ProfessionalFeeCharge, ProfessionalReceipt
from app.repositories import professional_fee_repository


class ReceiptNotFoundError(Exception):
    pass


class ReceiptExceedsChargeAmountError(Exception):
    pass


class ReceiptAlreadyVoidedError(Exception):
    pass


class MissingVoidReasonError(Exception):
    pass


def get_receipt(db: Session, tenant_id: uuid.UUID, receipt_id: uuid.UUID) -> ProfessionalReceipt:
    receipt = professional_fee_repository.get_receipt(db, tenant_id, receipt_id)
    if receipt is None:
        raise ReceiptNotFoundError(f"Recibo {receipt_id} no encontrado")
    return receipt


def list_receipts(
    db: Session, tenant_id: uuid.UUID, charge_id: uuid.UUID
) -> list[ProfessionalReceipt]:
    return professional_fee_repository.list_receipts_for_charge(db, tenant_id, charge_id)


def issue_receipt(
    db: Session,
    tenant_id: uuid.UUID,
    charge: ProfessionalFeeCharge,
    *,
    receipt_type: str,
    number: str,
    issued_at: date,
    amount: Decimal,
    issued_by: uuid.UUID,
    document_reference: str | None,
    observations: str | None,
) -> ProfessionalReceipt:
    already_issued = professional_fee_repository.sum_issued_receipts_for_charge(
        db, tenant_id, charge.id
    )
    if already_issued + amount > charge.amount:
        raise ReceiptExceedsChargeAmountError(
            "La suma de recibos emitidos no puede exceder el monto del cargo"
        )

    receipt = ProfessionalReceipt(
        tenant_id=tenant_id,
        charge_id=charge.id,
        issued_by=issued_by,
        receipt_type=receipt_type,
        number=number,
        issued_at=issued_at,
        amount=amount,
        status="ISSUED",
        document_reference=document_reference,
        observations=observations,
    )
    professional_fee_repository.create_receipt(db, receipt)
    db.flush()
    return receipt


def void_receipt(
    db: Session,
    tenant_id: uuid.UUID,
    receipt: ProfessionalReceipt,
    *,
    reason: str,
    actor_user_id: uuid.UUID | None,
) -> ProfessionalReceipt:
    if not reason or not reason.strip():
        raise MissingVoidReasonError("La anulacion requiere un motivo explicito")
    if receipt.status == "VOIDED":
        raise ReceiptAlreadyVoidedError("El recibo ya se encuentra anulado")

    receipt.status = "VOIDED"
    receipt.voided_at = clock.utcnow()
    receipt.voided_by = actor_user_id
    receipt.void_reason = reason
    db.flush()
    return receipt
