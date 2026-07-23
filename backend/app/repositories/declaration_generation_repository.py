import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.declaration_generation import DeclarationGenerationRun, DeclarationGenerationRunItem


def create_run(db: Session, run: DeclarationGenerationRun) -> DeclarationGenerationRun:
    db.add(run)
    db.flush()
    return run


def get_run(
    db: Session, tenant_id: uuid.UUID, run_id: uuid.UUID
) -> DeclarationGenerationRun | None:
    return db.execute(
        select(DeclarationGenerationRun).where(
            DeclarationGenerationRun.tenant_id == tenant_id, DeclarationGenerationRun.id == run_id
        )
    ).scalar_one_or_none()


def create_items(db: Session, items: list[DeclarationGenerationRunItem]) -> None:
    db.add_all(items)
    db.flush()


def list_items(
    db: Session, tenant_id: uuid.UUID, run_id: uuid.UUID
) -> list[DeclarationGenerationRunItem]:
    return list(
        db.execute(
            select(DeclarationGenerationRunItem)
            .where(
                DeclarationGenerationRunItem.tenant_id == tenant_id,
                DeclarationGenerationRunItem.run_id == run_id,
            )
            .order_by(DeclarationGenerationRunItem.created_at.asc())
        )
        .scalars()
        .all()
    )


def get_item(
    db: Session, tenant_id: uuid.UUID, run_id: uuid.UUID, item_id: uuid.UUID
) -> DeclarationGenerationRunItem | None:
    return db.execute(
        select(DeclarationGenerationRunItem).where(
            DeclarationGenerationRunItem.tenant_id == tenant_id,
            DeclarationGenerationRunItem.run_id == run_id,
            DeclarationGenerationRunItem.id == item_id,
        )
    ).scalar_one_or_none()


def delete_items_for_run(db: Session, tenant_id: uuid.UUID, run_id: uuid.UUID) -> None:
    db.execute(
        delete(DeclarationGenerationRunItem).where(
            DeclarationGenerationRunItem.tenant_id == tenant_id,
            DeclarationGenerationRunItem.run_id == run_id,
        )
    )
    db.flush()
