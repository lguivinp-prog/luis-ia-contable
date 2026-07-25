"""Ciclo de vida de tareas operativas manuales (Fase 2, Bloque 1, Secciones
6/9/13/15/16 del plan / ADR-0057/ADR-0058/ADR-0061). Nunca llama
`commit()`/`rollback()` -- solo `flush()`; el router controla la
transaccion (ADR-0037). Autorizacion por fila del asistente: unica
excepcion del repositorio al patron "permiso de rol, nunca de fila"."""

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.core import clock
from app.core.timezone import combine_local_to_utc
from app.models.operational_task import (
    MANUAL_TASK_TYPES,
    OperationalTask,
    TaskChangeHistory,
    TaskComment,
)
from app.models.user import User
from app.repositories import operational_task_repository, user_repository
from app.schemas.task import TaskContentChange, TaskDueInput
from app.services.client_service import ClientNotFoundError, get_client


class TaskNotFoundError(Exception):
    pass


class TaskVersionConflictError(Exception):
    pass


class TaskNoChangesError(Exception):
    pass


class TaskNotOwnedByAsistenteError(Exception):
    pass


class InactiveAssigneeError(Exception):
    pass


class ForeignClientError(Exception):
    pass


class TaskTypeImmutableError(Exception):
    pass


class TerminalTaskError(Exception):
    pass


class InvalidTaskTransitionError(Exception):
    pass


class AssignmentNotAuthorizedError(Exception):
    pass


_TERMINAL_STATUSES = ("COMPLETED", "CANCELLED")


def get_task(db: Session, tenant_id: uuid.UUID, task_id: uuid.UUID) -> OperationalTask:
    task = operational_task_repository.get(db, tenant_id, task_id)
    if task is None:
        raise TaskNotFoundError(f"Tarea {task_id} no encontrada")
    return task


def _resolve_due(due: TaskDueInput) -> tuple[datetime, bool, datetime | None]:
    due_at, due_has_time = combine_local_to_utc(due.due_date, due.due_time)
    starts_at: datetime | None = None
    if due.starts_date is not None:
        starts_at, _ = combine_local_to_utc(due.starts_date, due.starts_time)
    return due_at, due_has_time, starts_at


def _ensure_active_assignee(db: Session, tenant_id: uuid.UUID, assigned_to: uuid.UUID) -> User:
    user = user_repository.get_by_id_and_tenant(db, tenant_id, assigned_to)
    if user is None or not user.is_active:
        raise InactiveAssigneeError(
            f"El usuario {assigned_to} no existe, no pertenece al tenant o esta inactivo"
        )
    return user


def _check_asistente_can_assign(actor: User, assigned_to: uuid.UUID | None) -> None:
    """El asistente solo puede crear una tarea para si mismo o sin
    responsable (Seccion 9 del plan) -- asignarla a otro usuario sin
    `task:assign` es 403, nunca silenciosamente ignorado."""
    if actor.role != "asistente":
        return
    if assigned_to is not None and assigned_to != actor.id:
        raise AssignmentNotAuthorizedError(
            "El rol asistente solo puede crear tareas para si mismo o sin responsable"
        )


def create_task(
    db: Session,
    tenant_id: uuid.UUID,
    actor: User,
    *,
    title: str,
    description: str | None,
    task_type: str,
    priority: str,
    due: TaskDueInput,
    assigned_to: uuid.UUID | None,
    client_id: uuid.UUID | None,
) -> OperationalTask:
    if task_type not in MANUAL_TASK_TYPES:
        raise TaskTypeImmutableError(
            f"'{task_type}' no es un tipo disponible para creacion manual"
        )
    _check_asistente_can_assign(actor, assigned_to)

    if client_id is not None:
        try:
            get_client(db, tenant_id, client_id)
        except ClientNotFoundError as exc:
            raise ForeignClientError(str(exc)) from exc

    if assigned_to is not None:
        _ensure_active_assignee(db, tenant_id, assigned_to)

    due_at, due_has_time, starts_at = _resolve_due(due)

    task = OperationalTask(
        tenant_id=tenant_id,
        client_id=client_id,
        title=title,
        description=description,
        task_type=task_type,
        priority=priority,
        status="PENDING",
        assigned_to=assigned_to,
        created_by=actor.id,
        due_at=due_at,
        due_has_time=due_has_time,
        starts_at=starts_at,
        is_system_generated=False,
    )
    operational_task_repository.create(db, task)

    operational_task_repository.create_history(
        db,
        TaskChangeHistory(
            tenant_id=tenant_id,
            task_id=task.id,
            actor_user_id=actor.id,
            created_at=clock.utcnow(),
            origin="MANUAL",
            event_type="CREATED",
            to_status=task.status,
            to_due_at=task.due_at,
            to_priority=task.priority,
        ),
    )
    return task


def _check_version(task: OperationalTask, expected_version: int) -> None:
    if task.version != expected_version:
        raise TaskVersionConflictError(
            f"version desactualizada: esperada {expected_version}, actual {task.version}"
        )


def _ensure_asistente_owns_task(actor: User, task: OperationalTask) -> None:
    if actor.role != "asistente":
        return
    if task.assigned_to != actor.id:
        raise TaskNotOwnedByAsistenteError(
            "El rol asistente solo puede operar tareas asignadas a si mismo"
        )


def _ensure_not_terminal(task: OperationalTask) -> None:
    if task.status in _TERMINAL_STATUSES:
        raise TerminalTaskError(f"La tarea ya esta en estado terminal ({task.status})")


def update_task(
    db: Session,
    tenant_id: uuid.UUID,
    actor: User,
    task_id: uuid.UUID,
    *,
    expected_version: int,
    title: str | None,
    description: str | None,
    description_provided: bool,
    priority: str | None,
    task_type: str | None,
) -> OperationalTask:
    task = operational_task_repository.lock_exact_task(db, tenant_id, task_id)
    if task is None:
        raise TaskNotFoundError(f"Tarea {task_id} no encontrada")
    _check_version(task, expected_version)
    _ensure_asistente_owns_task(actor, task)
    _ensure_not_terminal(task)

    if task_type is not None and task.is_system_generated:
        raise TaskTypeImmutableError("task_type es inmutable en una tarea generada")

    raw_changes: dict = {}
    priority_changed = False
    from_priority = task.priority

    if title is not None and title.strip() != task.title:
        raw_changes["title"] = {"from": task.title, "to": title.strip()}
        task.title = title.strip()
    if description_provided:
        new_description = description.strip() if description is not None else None
        current_description = task.description
        if new_description != current_description:
            raw_changes["description"] = {"from": current_description, "to": new_description}
            task.description = new_description
    if task_type is not None and task_type != task.task_type:
        raw_changes["task_type"] = {"from": task.task_type, "to": task_type}
        task.task_type = task_type
    if priority is not None and priority != task.priority:
        task.priority = priority
        priority_changed = True

    changes = TaskContentChange.model_validate(raw_changes)

    if changes.is_empty() and not priority_changed:
        raise TaskNoChangesError("La solicitud no contiene ningun cambio real")

    task.version += 1
    db.flush()

    operational_task_repository.create_history(
        db,
        TaskChangeHistory(
            tenant_id=tenant_id,
            task_id=task.id,
            actor_user_id=actor.id,
            created_at=clock.utcnow(),
            origin="MANUAL",
            event_type="UPDATED",
            from_priority=from_priority if priority_changed else None,
            to_priority=task.priority if priority_changed else None,
            changes_json=changes.model_dump(by_alias=True, exclude_none=True) or None,
        ),
    )
    return task


def start_task(
    db: Session, tenant_id: uuid.UUID, actor: User, task_id: uuid.UUID, *, expected_version: int
) -> OperationalTask:
    task = operational_task_repository.lock_exact_task(db, tenant_id, task_id)
    if task is None:
        raise TaskNotFoundError(f"Tarea {task_id} no encontrada")
    _check_version(task, expected_version)
    _ensure_asistente_owns_task(actor, task)
    if task.status != "PENDING":
        raise InvalidTaskTransitionError(f"Transicion invalida: {task.status} -> IN_PROGRESS")

    from_status = task.status
    task.status = "IN_PROGRESS"
    task.version += 1
    db.flush()

    operational_task_repository.create_history(
        db,
        TaskChangeHistory(
            tenant_id=tenant_id,
            task_id=task.id,
            actor_user_id=actor.id,
            created_at=clock.utcnow(),
            origin="MANUAL",
            event_type="STARTED",
            from_status=from_status,
            to_status=task.status,
        ),
    )
    return task


def complete_task(
    db: Session, tenant_id: uuid.UUID, actor: User, task_id: uuid.UUID, *, expected_version: int
) -> OperationalTask:
    task = operational_task_repository.lock_exact_task(db, tenant_id, task_id)
    if task is None:
        raise TaskNotFoundError(f"Tarea {task_id} no encontrada")
    _check_version(task, expected_version)
    _ensure_asistente_owns_task(actor, task)
    if task.status not in ("PENDING", "IN_PROGRESS"):
        raise InvalidTaskTransitionError(f"Transicion invalida: {task.status} -> COMPLETED")

    now = clock.utcnow()
    from_status = task.status
    task.status = "COMPLETED"
    task.completed_at = now
    task.completed_by = actor.id
    task.resolution_origin = "MANUAL"
    task.version += 1
    db.flush()

    operational_task_repository.create_history(
        db,
        TaskChangeHistory(
            tenant_id=tenant_id,
            task_id=task.id,
            actor_user_id=actor.id,
            created_at=now,
            origin="MANUAL",
            event_type="COMPLETED",
            from_status=from_status,
            to_status=task.status,
        ),
    )
    return task


def cancel_task(
    db: Session,
    tenant_id: uuid.UUID,
    actor: User,
    task_id: uuid.UUID,
    *,
    expected_version: int,
    reason: str,
) -> OperationalTask:
    task = operational_task_repository.lock_exact_task(db, tenant_id, task_id)
    if task is None:
        raise TaskNotFoundError(f"Tarea {task_id} no encontrada")
    _check_version(task, expected_version)
    if task.status not in ("PENDING", "IN_PROGRESS"):
        raise InvalidTaskTransitionError(f"Transicion invalida: {task.status} -> CANCELLED")

    now = clock.utcnow()
    from_status = task.status
    task.status = "CANCELLED"
    task.cancelled_at = now
    task.cancelled_by = actor.id
    task.cancellation_reason = reason
    task.resolution_origin = "MANUAL"
    task.version += 1
    db.flush()

    operational_task_repository.create_history(
        db,
        TaskChangeHistory(
            tenant_id=tenant_id,
            task_id=task.id,
            actor_user_id=actor.id,
            created_at=now,
            origin="MANUAL",
            event_type="CANCELLED",
            from_status=from_status,
            to_status=task.status,
            reason=reason,
        ),
    )
    return task


def reassign_task(
    db: Session,
    tenant_id: uuid.UUID,
    actor: User,
    task_id: uuid.UUID,
    *,
    expected_version: int,
    assigned_to: uuid.UUID | None,
) -> OperationalTask:
    task = operational_task_repository.lock_exact_task(db, tenant_id, task_id)
    if task is None:
        raise TaskNotFoundError(f"Tarea {task_id} no encontrada")
    _check_version(task, expected_version)

    if assigned_to == task.assigned_to:
        raise TaskNoChangesError("El responsable indicado ya es el actual")

    if assigned_to is not None:
        _ensure_active_assignee(db, tenant_id, assigned_to)

    from_assigned_to = task.assigned_to
    task.assigned_to = assigned_to
    task.version += 1
    db.flush()

    operational_task_repository.create_history(
        db,
        TaskChangeHistory(
            tenant_id=tenant_id,
            task_id=task.id,
            actor_user_id=actor.id,
            created_at=clock.utcnow(),
            origin="MANUAL",
            event_type="ASSIGNED",
            from_assigned_to=from_assigned_to,
            to_assigned_to=task.assigned_to,
        ),
    )
    return task


def reschedule_task(
    db: Session,
    tenant_id: uuid.UUID,
    actor: User,
    task_id: uuid.UUID,
    *,
    expected_version: int,
    due: TaskDueInput,
) -> OperationalTask:
    task = operational_task_repository.lock_exact_task(db, tenant_id, task_id)
    if task is None:
        raise TaskNotFoundError(f"Tarea {task_id} no encontrada")
    _check_version(task, expected_version)
    _ensure_asistente_owns_task(actor, task)
    _ensure_not_terminal(task)

    due_at, due_has_time, starts_at = _resolve_due(due)

    from_due_at = task.due_at
    from_due_is_overridden = task.due_is_overridden
    from_due_has_time = task.due_has_time

    first_override_activation = task.is_system_generated and not task.due_is_overridden

    no_real_change = (
        due_at == task.due_at
        and due_has_time == task.due_has_time
        and starts_at == task.starts_at
        and not first_override_activation
    )
    if no_real_change:
        raise TaskNoChangesError("No hay un cambio real de vencimiento")

    task.due_at = due_at
    task.due_has_time = due_has_time
    task.starts_at = starts_at
    if task.is_system_generated:
        task.due_is_overridden = True
    task.version += 1
    db.flush()

    operational_task_repository.create_history(
        db,
        TaskChangeHistory(
            tenant_id=tenant_id,
            task_id=task.id,
            actor_user_id=actor.id,
            created_at=clock.utcnow(),
            origin="MANUAL",
            event_type="RESCHEDULED",
            from_due_at=from_due_at,
            to_due_at=task.due_at,
            from_due_is_overridden=from_due_is_overridden,
            to_due_is_overridden=task.due_is_overridden,
            from_due_has_time=from_due_has_time,
            to_due_has_time=task.due_has_time,
        ),
    )
    return task


def add_comment(
    db: Session, tenant_id: uuid.UUID, actor: User, task_id: uuid.UUID, *, body: str
) -> TaskComment:
    task = operational_task_repository.lock_exact_task(db, tenant_id, task_id)
    if task is None:
        raise TaskNotFoundError(f"Tarea {task_id} no encontrada")
    _ensure_not_terminal(task)
    _ensure_asistente_owns_task(actor, task)

    comment = TaskComment(
        tenant_id=tenant_id,
        task_id=task.id,
        author_user_id=actor.id,
        body=body,
        created_at=clock.utcnow(),
    )
    operational_task_repository.create_comment(db, comment)
    return comment


def list_comments(
    db: Session, tenant_id: uuid.UUID, task_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[list[TaskComment], int]:
    get_task(db, tenant_id, task_id)
    return operational_task_repository.list_comments(db, tenant_id, task_id, limit=limit, offset=offset)


def list_history(
    db: Session, tenant_id: uuid.UUID, task_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[list[TaskChangeHistory], int]:
    get_task(db, tenant_id, task_id)
    return operational_task_repository.list_history(db, tenant_id, task_id, limit=limit, offset=offset)


def compute_assignee_is_active(db: Session, tenant_id: uuid.UUID, task: OperationalTask) -> bool | None:
    if task.assigned_to is None:
        return None
    user = user_repository.get_by_id_and_tenant(db, tenant_id, task.assigned_to)
    return bool(user and user.is_active)


def compute_source_due_mismatch(task: OperationalTask) -> bool:
    return bool(
        task.due_is_overridden and task.source_due_at is not None and task.source_due_at != task.due_at
    )
