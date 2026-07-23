import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.professional_fee import (
    ProfessionalFeeAgreement,
    ProfessionalFeeAgreementHistory,
    ProfessionalFeeCharge,
    ProfessionalFeeChargeStatusHistory,
    ProfessionalFeePayment,
    ProfessionalFeePaymentHistory,
    ProfessionalReceipt,
)

# --- acuerdos ---


def create_agreement(db: Session, agreement: ProfessionalFeeAgreement) -> ProfessionalFeeAgreement:
    db.add(agreement)
    db.flush()
    return agreement


def get_agreement(
    db: Session, tenant_id: uuid.UUID, agreement_id: uuid.UUID
) -> ProfessionalFeeAgreement | None:
    return db.execute(
        select(ProfessionalFeeAgreement).where(
            ProfessionalFeeAgreement.tenant_id == tenant_id,
            ProfessionalFeeAgreement.id == agreement_id,
        )
    ).scalar_one_or_none()


def list_agreements_for_client(
    db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID
) -> list[ProfessionalFeeAgreement]:
    return list(
        db.execute(
            select(ProfessionalFeeAgreement)
            .where(
                ProfessionalFeeAgreement.tenant_id == tenant_id,
                ProfessionalFeeAgreement.client_id == client_id,
            )
            .order_by(ProfessionalFeeAgreement.valid_from.asc())
        )
        .scalars()
        .all()
    )


def create_agreement_history(db: Session, entry: ProfessionalFeeAgreementHistory) -> None:
    db.add(entry)
    db.flush()


# --- cargos ---


def lock_charge_for_update(
    db: Session, tenant_id: uuid.UUID, charge_id: uuid.UUID
) -> ProfessionalFeeCharge | None:
    """Bloqueo pesimista para operaciones financieras de honorarios (ver
    "Concurrencia financiera"): raiz independiente de `declarations`."""
    return db.execute(
        select(ProfessionalFeeCharge)
        .where(ProfessionalFeeCharge.tenant_id == tenant_id, ProfessionalFeeCharge.id == charge_id)
        .with_for_update()
    ).scalar_one_or_none()


def get_charge(
    db: Session, tenant_id: uuid.UUID, charge_id: uuid.UUID
) -> ProfessionalFeeCharge | None:
    return db.execute(
        select(ProfessionalFeeCharge).where(
            ProfessionalFeeCharge.tenant_id == tenant_id, ProfessionalFeeCharge.id == charge_id
        )
    ).scalar_one_or_none()


def list_charges_for_agreement(
    db: Session, tenant_id: uuid.UUID, agreement_id: uuid.UUID
) -> list[ProfessionalFeeCharge]:
    return list(
        db.execute(
            select(ProfessionalFeeCharge)
            .where(
                ProfessionalFeeCharge.tenant_id == tenant_id,
                ProfessionalFeeCharge.agreement_id == agreement_id,
            )
            .order_by(ProfessionalFeeCharge.created_at.asc())
        )
        .scalars()
        .all()
    )


def create_charge(db: Session, charge: ProfessionalFeeCharge) -> ProfessionalFeeCharge:
    db.add(charge)
    db.flush()
    return charge


def create_charge_status_history(db: Session, entry: ProfessionalFeeChargeStatusHistory) -> None:
    db.add(entry)
    db.flush()


# --- pagos ---


def create_payment(db: Session, payment: ProfessionalFeePayment) -> ProfessionalFeePayment:
    db.add(payment)
    db.flush()
    return payment


def get_payment_by_id(
    db: Session, tenant_id: uuid.UUID, payment_id: uuid.UUID
) -> ProfessionalFeePayment | None:
    return db.execute(
        select(ProfessionalFeePayment).where(
            ProfessionalFeePayment.tenant_id == tenant_id, ProfessionalFeePayment.id == payment_id
        )
    ).scalar_one_or_none()


def get_payment_by_idempotency_key(
    db: Session, tenant_id: uuid.UUID, idempotency_key: uuid.UUID
) -> ProfessionalFeePayment | None:
    return db.execute(
        select(ProfessionalFeePayment).where(
            ProfessionalFeePayment.tenant_id == tenant_id,
            ProfessionalFeePayment.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()


def list_payments_for_charge(
    db: Session, tenant_id: uuid.UUID, charge_id: uuid.UUID
) -> list[ProfessionalFeePayment]:
    return list(
        db.execute(
            select(ProfessionalFeePayment)
            .where(
                ProfessionalFeePayment.tenant_id == tenant_id,
                ProfessionalFeePayment.charge_id == charge_id,
            )
            .order_by(ProfessionalFeePayment.created_at.asc())
        )
        .scalars()
        .all()
    )


def sum_valid_amount_for_charge(db: Session, tenant_id: uuid.UUID, charge_id: uuid.UUID) -> Decimal:
    result = db.execute(
        select(func.coalesce(func.sum(ProfessionalFeePayment.amount), 0)).where(
            ProfessionalFeePayment.tenant_id == tenant_id,
            ProfessionalFeePayment.charge_id == charge_id,
            ProfessionalFeePayment.is_voided.is_(False),
        )
    ).scalar_one()
    return Decimal(result)


def create_payment_history(db: Session, entry: ProfessionalFeePaymentHistory) -> None:
    db.add(entry)
    db.flush()


# --- recibos ---


def create_receipt(db: Session, receipt: ProfessionalReceipt) -> ProfessionalReceipt:
    db.add(receipt)
    db.flush()
    return receipt


def get_receipt(
    db: Session, tenant_id: uuid.UUID, receipt_id: uuid.UUID
) -> ProfessionalReceipt | None:
    return db.execute(
        select(ProfessionalReceipt).where(
            ProfessionalReceipt.tenant_id == tenant_id, ProfessionalReceipt.id == receipt_id
        )
    ).scalar_one_or_none()


def list_receipts_for_charge(
    db: Session, tenant_id: uuid.UUID, charge_id: uuid.UUID
) -> list[ProfessionalReceipt]:
    return list(
        db.execute(
            select(ProfessionalReceipt)
            .where(
                ProfessionalReceipt.tenant_id == tenant_id,
                ProfessionalReceipt.charge_id == charge_id,
            )
            .order_by(ProfessionalReceipt.created_at.asc())
        )
        .scalars()
        .all()
    )


# --- agregados para el tablero (ver "Tablero mensual completo -- sin
# duplicacion de filas"): un cargo de honorarios no esta atado 1:1 a una
# declaracion, se relaciona por (client_id, tax_period_id) via el acuerdo.
# Se resuelve con 2 consultas agregadas (nunca N+1 ni un JOIN que multiplique
# filas de declaraciones) para exactamente los pares pedidos. ---


def list_charges_for_client_period_pairs(
    db: Session, tenant_id: uuid.UUID, pairs: list[tuple[uuid.UUID, uuid.UUID]]
) -> list[tuple[uuid.UUID, ProfessionalFeeCharge]]:
    if not pairs:
        return []
    client_ids = {client_id for client_id, _ in pairs}
    period_ids = {tax_period_id for _, tax_period_id in pairs}
    rows = db.execute(
        select(ProfessionalFeeAgreement.client_id, ProfessionalFeeCharge)
        .join(
            ProfessionalFeeAgreement,
            ProfessionalFeeCharge.agreement_id == ProfessionalFeeAgreement.id,
        )
        .where(
            ProfessionalFeeCharge.tenant_id == tenant_id,
            ProfessionalFeeAgreement.client_id.in_(client_ids),
            ProfessionalFeeCharge.tax_period_id.in_(period_ids),
        )
    ).all()
    pair_set = set(pairs)
    return [
        (client_id, charge) for client_id, charge in rows if (client_id, charge.tax_period_id) in pair_set
    ]


def sum_valid_amounts_for_charges(
    db: Session, tenant_id: uuid.UUID, charge_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Decimal]:
    if not charge_ids:
        return {}
    rows = db.execute(
        select(
            ProfessionalFeePayment.charge_id,
            func.coalesce(func.sum(ProfessionalFeePayment.amount), 0),
        )
        .where(
            ProfessionalFeePayment.tenant_id == tenant_id,
            ProfessionalFeePayment.charge_id.in_(charge_ids),
            ProfessionalFeePayment.is_voided.is_(False),
        )
        .group_by(ProfessionalFeePayment.charge_id)
    ).all()
    return {charge_id: Decimal(total) for charge_id, total in rows}


def sum_issued_receipts_for_charge(db: Session, tenant_id: uuid.UUID, charge_id: uuid.UUID) -> Decimal:
    result = db.execute(
        select(func.coalesce(func.sum(ProfessionalReceipt.amount), 0)).where(
            ProfessionalReceipt.tenant_id == tenant_id,
            ProfessionalReceipt.charge_id == charge_id,
            ProfessionalReceipt.status == "ISSUED",
        )
    ).scalar_one()
    return Decimal(result)
