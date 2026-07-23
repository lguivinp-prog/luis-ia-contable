import uuid

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models.declaration import (
    Declaration,
    DeclarationAssignmentHistory,
    DeclarationDueDateHistory,
    DeclarationReopenHistory,
    DeclarationStatusHistory,
    FilingHistory,
)

# --- declarations ---


def get(db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID) -> Declaration | None:
    return db.execute(
        select(Declaration).where(
            Declaration.tenant_id == tenant_id, Declaration.id == declaration_id
        )
    ).scalar_one_or_none()


def get_by_natural_key(
    db: Session,
    tenant_id: uuid.UUID,
    client_id: uuid.UUID,
    client_obligation_id: uuid.UUID,
    tax_period_id: uuid.UUID,
) -> Declaration | None:
    return db.execute(
        select(Declaration).where(
            Declaration.tenant_id == tenant_id,
            Declaration.client_id == client_id,
            Declaration.client_obligation_id == client_obligation_id,
            Declaration.tax_period_id == tax_period_id,
        )
    ).scalar_one_or_none()


def create(db: Session, declaration: Declaration) -> Declaration:
    db.add(declaration)
    db.flush()
    return declaration


def base_query(tenant_id: uuid.UUID) -> Select:
    return select(Declaration).where(Declaration.tenant_id == tenant_id)


def list_for_dashboard(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    tax_period_id: uuid.UUID | None = None,
    client_id: uuid.UUID | None = None,
    workflow_status: str | None = None,
    filing_status: str | None = None,
    assigned_user_id: uuid.UUID | None = None,
    risk_status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Declaration], int]:
    query = base_query(tenant_id)
    if tax_period_id is not None:
        query = query.where(Declaration.tax_period_id == tax_period_id)
    if client_id is not None:
        query = query.where(Declaration.client_id == client_id)
    if workflow_status is not None:
        query = query.where(Declaration.workflow_status == workflow_status)
    if filing_status is not None:
        query = query.where(Declaration.filing_status == filing_status)
    if assigned_user_id is not None:
        query = query.where(Declaration.assigned_user_id == assigned_user_id)
    if risk_status is not None:
        from datetime import UTC, datetime

        from app.services.declaration_risk import risk_status_sql_expression

        today = datetime.now(UTC).date()
        query = query.where(risk_status_sql_expression(today) == risk_status)

    total = db.execute(
        select(func.count()).select_from(query.with_only_columns(Declaration.id).subquery())
    ).scalar_one()
    query = query.order_by(Declaration.current_effective_due_date.asc()).limit(limit).offset(offset)
    items = list(db.execute(query).scalars().all())
    return items, total


# --- historial (5 tablas, ver "Historial inicial completo de la declaracion") ---


def create_status_history_entry(db: Session, entry: DeclarationStatusHistory) -> None:
    db.add(entry)
    db.flush()


def create_due_date_history_entry(db: Session, entry: DeclarationDueDateHistory) -> None:
    db.add(entry)
    db.flush()


def create_assignment_history_entry(db: Session, entry: DeclarationAssignmentHistory) -> None:
    db.add(entry)
    db.flush()


def create_filing_history_entry(db: Session, entry: FilingHistory) -> None:
    db.add(entry)
    db.flush()


def create_reopen_history_entry(db: Session, entry: DeclarationReopenHistory) -> None:
    db.add(entry)
    db.flush()


def list_status_history(
    db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID
) -> list[DeclarationStatusHistory]:
    return list(
        db.execute(
            select(DeclarationStatusHistory)
            .where(
                DeclarationStatusHistory.tenant_id == tenant_id,
                DeclarationStatusHistory.declaration_id == declaration_id,
            )
            .order_by(DeclarationStatusHistory.created_at.asc())
        )
        .scalars()
        .all()
    )


def list_due_date_history(
    db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID
) -> list[DeclarationDueDateHistory]:
    return list(
        db.execute(
            select(DeclarationDueDateHistory)
            .where(
                DeclarationDueDateHistory.tenant_id == tenant_id,
                DeclarationDueDateHistory.declaration_id == declaration_id,
            )
            .order_by(DeclarationDueDateHistory.created_at.asc())
        )
        .scalars()
        .all()
    )


def list_assignment_history(
    db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID
) -> list[DeclarationAssignmentHistory]:
    return list(
        db.execute(
            select(DeclarationAssignmentHistory)
            .where(
                DeclarationAssignmentHistory.tenant_id == tenant_id,
                DeclarationAssignmentHistory.declaration_id == declaration_id,
            )
            .order_by(DeclarationAssignmentHistory.created_at.asc())
        )
        .scalars()
        .all()
    )


def list_filing_history(
    db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID
) -> list[FilingHistory]:
    return list(
        db.execute(
            select(FilingHistory)
            .where(
                FilingHistory.tenant_id == tenant_id,
                FilingHistory.declaration_id == declaration_id,
            )
            .order_by(FilingHistory.created_at.asc())
        )
        .scalars()
        .all()
    )


def list_reopen_history(
    db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID
) -> list[DeclarationReopenHistory]:
    return list(
        db.execute(
            select(DeclarationReopenHistory)
            .where(
                DeclarationReopenHistory.tenant_id == tenant_id,
                DeclarationReopenHistory.declaration_id == declaration_id,
            )
            .order_by(DeclarationReopenHistory.created_at.asc())
        )
        .scalars()
        .all()
    )
