import uuid
from collections.abc import Sequence

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.models.operational_task import (
    OperationalTask,
    TaskChangeHistory,
    TaskComment,
    TaskGenerationRun,
)


def create(db: Session, task: OperationalTask) -> OperationalTask:
    db.add(task)
    db.flush()
    return task


def get(db: Session, tenant_id: uuid.UUID, task_id: uuid.UUID) -> OperationalTask | None:
    return db.execute(
        select(OperationalTask).where(
            OperationalTask.tenant_id == tenant_id, OperationalTask.id == task_id
        )
    ).scalar_one_or_none()


def lock_exact_task(db: Session, tenant_id: uuid.UUID, task_id: uuid.UUID) -> OperationalTask | None:
    """`SELECT ... FOR UPDATE` sobre la fila exacta, con `populate_existing`
    obligatorio (lección de Bloque 5: sin esto, un objeto ya cacheado en el
    identity map de la sesión podría devolver columnas obsoletas aunque el
    bloqueo SQL haya esperado correctamente a otra transacción)."""
    return db.execute(
        select(OperationalTask)
        .where(OperationalTask.tenant_id == tenant_id, OperationalTask.id == task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def get_by_generation_key(
    db: Session, tenant_id: uuid.UUID, generation_key: str
) -> OperationalTask | None:
    return db.execute(
        select(OperationalTask).where(
            OperationalTask.tenant_id == tenant_id, OperationalTask.generation_key == generation_key
        )
    ).scalar_one_or_none()


def list_tasks(
    db: Session,
    tenant_id: uuid.UUID,
    filters: Sequence[ColumnElement],
    *,
    limit: int,
    offset: int,
) -> tuple[list[OperationalTask], int]:
    base = select(OperationalTask).where(OperationalTask.tenant_id == tenant_id, *filters)
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    items = list(
        db.execute(
            base.order_by(OperationalTask.due_at.asc(), OperationalTask.id.asc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return items, total


def list_non_terminal_generated_tasks(db: Session, tenant_id: uuid.UUID) -> list[OperationalTask]:
    """Candidatas a realineacion/resolucion automatica (Seccion 8 del plan):
    solo tareas generadas y no terminales, ordenadas por `id ASC`
    (orden determinista de bloqueo obligatorio)."""
    return list(
        db.execute(
            select(OperationalTask)
            .where(
                OperationalTask.tenant_id == tenant_id,
                OperationalTask.is_system_generated.is_(True),
                OperationalTask.status.in_(["PENDING", "IN_PROGRESS"]),
            )
            .order_by(OperationalTask.id.asc())
        )
        .scalars()
        .all()
    )


def create_comment(db: Session, comment: TaskComment) -> TaskComment:
    db.add(comment)
    db.flush()
    return comment


def list_comments(
    db: Session, tenant_id: uuid.UUID, task_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[list[TaskComment], int]:
    base = select(TaskComment).where(TaskComment.tenant_id == tenant_id, TaskComment.task_id == task_id)
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    items = list(
        db.execute(base.order_by(TaskComment.created_at.asc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return items, total


def create_history(db: Session, entry: TaskChangeHistory) -> TaskChangeHistory:
    db.add(entry)
    db.flush()
    return entry


def list_history(
    db: Session, tenant_id: uuid.UUID, task_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[list[TaskChangeHistory], int]:
    base = select(TaskChangeHistory).where(
        TaskChangeHistory.tenant_id == tenant_id, TaskChangeHistory.task_id == task_id
    )
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    items = list(
        db.execute(base.order_by(TaskChangeHistory.created_at.asc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return items, total


def create_generation_run(db: Session, run: TaskGenerationRun) -> TaskGenerationRun:
    db.add(run)
    db.flush()
    return run


def get_generation_run(
    db: Session, tenant_id: uuid.UUID, run_id: uuid.UUID
) -> TaskGenerationRun | None:
    return db.execute(
        select(TaskGenerationRun).where(
            TaskGenerationRun.tenant_id == tenant_id, TaskGenerationRun.id == run_id
        )
    ).scalar_one_or_none()


def list_generation_runs(
    db: Session, tenant_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[list[TaskGenerationRun], int]:
    base = select(TaskGenerationRun).where(TaskGenerationRun.tenant_id == tenant_id)
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    items = list(
        db.execute(base.order_by(TaskGenerationRun.started_at.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return items, total
