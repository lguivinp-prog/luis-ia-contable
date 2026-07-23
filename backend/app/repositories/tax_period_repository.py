import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tax_period import TaxPeriod, TaxPeriodStatusHistory


def list_periods(db: Session, tenant_id: uuid.UUID) -> list[TaxPeriod]:
    return list(
        db.execute(
            select(TaxPeriod)
            .where(TaxPeriod.tenant_id == tenant_id)
            .order_by(TaxPeriod.fiscal_year.desc(), TaxPeriod.month.desc().nulls_last())
        )
        .scalars()
        .all()
    )


def get_period(db: Session, tenant_id: uuid.UUID, period_id: uuid.UUID) -> TaxPeriod | None:
    return db.execute(
        select(TaxPeriod).where(TaxPeriod.tenant_id == tenant_id, TaxPeriod.id == period_id)
    ).scalar_one_or_none()


def create_period(db: Session, period: TaxPeriod) -> TaxPeriod:
    db.add(period)
    db.flush()
    return period


def create_status_history_entry(
    db: Session, entry: TaxPeriodStatusHistory
) -> TaxPeriodStatusHistory:
    db.add(entry)
    db.flush()
    return entry


def list_status_history(
    db: Session, tenant_id: uuid.UUID, period_id: uuid.UUID
) -> list[TaxPeriodStatusHistory]:
    return list(
        db.execute(
            select(TaxPeriodStatusHistory)
            .where(
                TaxPeriodStatusHistory.tenant_id == tenant_id,
                TaxPeriodStatusHistory.tax_period_id == period_id,
            )
            .order_by(TaxPeriodStatusHistory.created_at.asc())
        )
        .scalars()
        .all()
    )
