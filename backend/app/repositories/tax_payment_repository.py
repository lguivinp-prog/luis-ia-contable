import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tax_payment import TaxPayment, TaxPaymentHistory


def create(db: Session, payment: TaxPayment) -> TaxPayment:
    db.add(payment)
    db.flush()
    return payment


def get_by_id(db: Session, tenant_id: uuid.UUID, payment_id: uuid.UUID) -> TaxPayment | None:
    return db.execute(
        select(TaxPayment).where(TaxPayment.tenant_id == tenant_id, TaxPayment.id == payment_id)
    ).scalar_one_or_none()


def get_by_idempotency_key(
    db: Session, tenant_id: uuid.UUID, idempotency_key: uuid.UUID
) -> TaxPayment | None:
    return db.execute(
        select(TaxPayment).where(
            TaxPayment.tenant_id == tenant_id, TaxPayment.idempotency_key == idempotency_key
        )
    ).scalar_one_or_none()


def list_for_declaration(
    db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID
) -> list[TaxPayment]:
    return list(
        db.execute(
            select(TaxPayment)
            .where(TaxPayment.tenant_id == tenant_id, TaxPayment.declaration_id == declaration_id)
            .order_by(TaxPayment.created_at.asc())
        )
        .scalars()
        .all()
    )


def sum_valid_amount_for_declaration(
    db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID
) -> Decimal:
    """Suma TODOS los pagos validos (no anulados) de la declaracion, sin
    filtrar por `assessment_id` (ver "Concurrencia financiera": el saldo
    siempre se calcula contra la determinacion vigente actual, no contra la
    que tenia cada pago al momento de crearse)."""
    result = db.execute(
        select(func.coalesce(func.sum(TaxPayment.amount), 0)).where(
            TaxPayment.tenant_id == tenant_id,
            TaxPayment.declaration_id == declaration_id,
            TaxPayment.is_voided.is_(False),
        )
    ).scalar_one()
    return Decimal(result)


def create_history(db: Session, entry: TaxPaymentHistory) -> None:
    db.add(entry)
    db.flush()
