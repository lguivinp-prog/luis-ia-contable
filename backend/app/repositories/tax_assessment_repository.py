import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tax_assessment import TaxAssessment, TaxAssessmentHistory


def create(db: Session, assessment: TaxAssessment) -> TaxAssessment:
    db.add(assessment)
    db.flush()
    return assessment


def get_current(
    db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID
) -> TaxAssessment | None:
    return db.execute(
        select(TaxAssessment).where(
            TaxAssessment.tenant_id == tenant_id,
            TaxAssessment.declaration_id == declaration_id,
            TaxAssessment.is_current.is_(True),
        )
    ).scalar_one_or_none()


def get_by_id(
    db: Session, tenant_id: uuid.UUID, assessment_id: uuid.UUID
) -> TaxAssessment | None:
    return db.execute(
        select(TaxAssessment).where(
            TaxAssessment.tenant_id == tenant_id, TaxAssessment.id == assessment_id
        )
    ).scalar_one_or_none()


def list_for_declaration(
    db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID
) -> list[TaxAssessment]:
    return list(
        db.execute(
            select(TaxAssessment)
            .where(
                TaxAssessment.tenant_id == tenant_id,
                TaxAssessment.declaration_id == declaration_id,
            )
            .order_by(TaxAssessment.sequence_number.asc())
        )
        .scalars()
        .all()
    )


def create_history(db: Session, entry: TaxAssessmentHistory) -> None:
    db.add(entry)
    db.flush()
