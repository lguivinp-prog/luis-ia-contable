import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core import clock
from app.core.dependencies import get_client_ip, verify_csrf
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.models.audit_log import AuditAction
from app.models.operational_task import OperationalTask
from app.models.user import User
from app.repositories import operational_task_repository
from app.schemas.task import (
    AgendaSummaryOut,
    AlertsSummaryOut,
    TaskCancelRequest,
    TaskChangeHistoryListOut,
    TaskChangeHistoryOut,
    TaskCommentCreate,
    TaskCommentListOut,
    TaskCommentOut,
    TaskCreate,
    TaskDetailOut,
    TaskGenerationRunListOut,
    TaskGenerationRunOut,
    TaskListOut,
    TaskOut,
    TaskReassignRequest,
    TaskRescheduleRequest,
    TaskSyncResult,
    TaskSyncTrigger,
    TaskUpdate,
    TaskVersionedAction,
)
from app.services import operational_task_service, task_agenda_service, task_generation_service
from app.services.audit_service import record as record_audit
from app.services.operational_task_service import (
    AssignmentNotAuthorizedError,
    ForeignClientError,
    InactiveAssigneeError,
    InvalidTaskTransitionError,
    TaskNoChangesError,
    TaskNotFoundError,
    TaskNotOwnedByAsistenteError,
    TaskTypeImmutableError,
    TaskVersionConflictError,
    TerminalTaskError,
)

router = APIRouter(prefix="/api/v1", tags=["operational-tasks"])

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


def _pagination(limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT), offset: int = Query(0, ge=0)):
    return limit, offset


def _to_detail(db: Session, tenant_id: uuid.UUID, task: OperationalTask) -> TaskDetailOut:
    base = TaskOut.model_validate(task)
    return TaskDetailOut(
        **base.model_dump(),
        assignee_is_active=operational_task_service.compute_assignee_is_active(db, tenant_id, task),
        source_due_mismatch=operational_task_service.compute_source_due_mismatch(task),
    )


# --- Rutas estaticas ANTES de /tasks/{task_id} (obligatorio, ver Seccion 15
# del plan): de lo contrario FastAPI intentaria parsear "sync"/
# "generation-runs" como UUID contra la ruta dinamica registrada antes. ---


@router.post("/tasks/sync", response_model=TaskSyncResult)
def sync_tasks(
    payload: TaskSyncTrigger,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.TASK_SYNC)),
    _csrf: None = Depends(verify_csrf),
) -> TaskSyncResult:
    run = task_generation_service.sync_operational_tasks(
        db,
        current_user.tenant_id,
        trigger_source=payload.trigger_source,
        triggered_by=current_user.id,
    )
    db.commit()
    db.refresh(run)
    return TaskSyncResult(run_id=run.id, summary=run.summary)


@router.get("/tasks/generation-runs", response_model=TaskGenerationRunListOut)
def list_generation_runs(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.TASK_SYNC)),
    pagination: tuple[int, int] = Depends(_pagination),
) -> TaskGenerationRunListOut:
    limit, offset = pagination
    items, total = operational_task_repository.list_generation_runs(
        db, current_user.tenant_id, limit=limit, offset=offset
    )
    return TaskGenerationRunListOut(
        items=[TaskGenerationRunOut.model_validate(r) for r in items], total=total, limit=limit, offset=offset
    )


@router.post("/tasks", response_model=TaskDetailOut, status_code=status.HTTP_201_CREATED)
def create_task(
    payload: TaskCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.TASK_CREATE)),
    _csrf: None = Depends(verify_csrf),
) -> TaskDetailOut:
    try:
        task = operational_task_service.create_task(
            db,
            current_user.tenant_id,
            current_user,
            title=payload.title,
            description=payload.description,
            task_type=payload.task_type,
            priority=payload.priority,
            due=payload.due,
            assigned_to=payload.assigned_to,
            client_id=payload.client_id,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TASK_CREATE,
            entity_type="operational_task",
            entity_id=task.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(task)
        return _to_detail(db, current_user.tenant_id, task)
    except AssignmentNotAuthorizedError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except (ForeignClientError, InactiveAssigneeError, TaskTypeImmutableError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/tasks", response_model=TaskListOut)
def list_tasks(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.TASK_READ)),
    status_filter: str | None = Query(None, alias="status"),
    priority: str | None = Query(None),
    task_type: str | None = Query(None),
    time_bucket: str | None = Query(None),
    assigned_to: str | None = Query(None),
    client_id: uuid.UUID | None = Query(None),
    pagination: tuple[int, int] = Depends(_pagination),
) -> TaskListOut:
    limit, offset = pagination
    filters = []
    if status_filter is not None:
        filters.append(OperationalTask.status == status_filter)
    if priority is not None:
        filters.append(OperationalTask.priority == priority)
    if task_type is not None:
        filters.append(OperationalTask.task_type == task_type)
    if client_id is not None:
        filters.append(OperationalTask.client_id == client_id)
    if time_bucket is not None:
        try:
            filters.append(
                task_agenda_service.time_bucket_condition(time_bucket, clock.utcnow())
            )
        except task_agenda_service.InvalidAssignedToFilterError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if assigned_to is not None:
        try:
            filters.append(
                task_agenda_service.resolve_assigned_to_filter(assigned_to, current_user.id)
            )
        except task_agenda_service.InvalidAssignedToFilterError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    items, total = operational_task_repository.list_tasks(
        db, current_user.tenant_id, filters, limit=limit, offset=offset
    )
    return TaskListOut(
        items=[TaskOut.model_validate(t) for t in items], total=total, limit=limit, offset=offset
    )


@router.get("/tasks/agenda-summary", response_model=AgendaSummaryOut)
def agenda_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.TASK_READ)),
) -> AgendaSummaryOut:
    summary = task_agenda_service.compute_agenda_summary(db, current_user.tenant_id, clock.utcnow())
    return AgendaSummaryOut(**summary)


@router.get("/tasks/alerts-summary", response_model=AlertsSummaryOut)
def alerts_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.ALERT_READ)),
) -> AlertsSummaryOut:
    summary = task_agenda_service.compute_alerts_summary(db, current_user.tenant_id, clock.utcnow())
    return AlertsSummaryOut(**summary)


@router.get("/tasks/{task_id}", response_model=TaskDetailOut)
def get_task(
    task_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.TASK_READ)),
) -> TaskDetailOut:
    try:
        task = operational_task_service.get_task(db, current_user.tenant_id, task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _to_detail(db, current_user.tenant_id, task)


@router.patch("/tasks/{task_id}", response_model=TaskDetailOut)
def update_task(
    task_id: uuid.UUID,
    payload: TaskUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.TASK_UPDATE)),
    _csrf: None = Depends(verify_csrf),
) -> TaskDetailOut:
    fields_set = payload.model_fields_set
    if not (fields_set & {"title", "description", "priority", "task_type"}):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Debe enviarse al menos un campo editable",
        )
    try:
        task = operational_task_service.update_task(
            db,
            current_user.tenant_id,
            current_user,
            task_id,
            expected_version=payload.expected_version,
            title=payload.title,
            description=payload.description,
            description_provided="description" in fields_set,
            priority=payload.priority,
            task_type=payload.task_type,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TASK_UPDATE,
            entity_type="operational_task",
            entity_id=task.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(task)
        return _to_detail(db, current_user.tenant_id, task)
    except TaskNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TaskNotOwnedByAsistenteError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except TaskTypeImmutableError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except TerminalTaskError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except TaskVersionConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "TASK_VERSION_CONFLICT", "message": str(exc)},
        ) from exc
    except TaskNoChangesError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"code": "TASK_NO_CHANGES", "message": str(exc)}
        ) from exc


@router.post("/tasks/{task_id}/start", response_model=TaskDetailOut)
def start_task(
    task_id: uuid.UUID,
    payload: TaskVersionedAction,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.TASK_UPDATE)),
    _csrf: None = Depends(verify_csrf),
) -> TaskDetailOut:
    try:
        task = operational_task_service.start_task(
            db, current_user.tenant_id, current_user, task_id, expected_version=payload.expected_version
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TASK_START,
            entity_type="operational_task",
            entity_id=task.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(task)
        return _to_detail(db, current_user.tenant_id, task)
    except TaskNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TaskNotOwnedByAsistenteError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except TaskVersionConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "TASK_VERSION_CONFLICT", "message": str(exc)},
        ) from exc
    except InvalidTaskTransitionError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/complete", response_model=TaskDetailOut)
def complete_task(
    task_id: uuid.UUID,
    payload: TaskVersionedAction,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.TASK_COMPLETE)),
    _csrf: None = Depends(verify_csrf),
) -> TaskDetailOut:
    try:
        task = operational_task_service.complete_task(
            db, current_user.tenant_id, current_user, task_id, expected_version=payload.expected_version
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TASK_COMPLETE,
            entity_type="operational_task",
            entity_id=task.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(task)
        return _to_detail(db, current_user.tenant_id, task)
    except TaskNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TaskNotOwnedByAsistenteError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except TaskVersionConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "TASK_VERSION_CONFLICT", "message": str(exc)},
        ) from exc
    except InvalidTaskTransitionError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/cancel", response_model=TaskDetailOut)
def cancel_task(
    task_id: uuid.UUID,
    payload: TaskCancelRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.TASK_CANCEL)),
    _csrf: None = Depends(verify_csrf),
) -> TaskDetailOut:
    try:
        task = operational_task_service.cancel_task(
            db,
            current_user.tenant_id,
            current_user,
            task_id,
            expected_version=payload.expected_version,
            reason=payload.reason,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TASK_CANCEL,
            entity_type="operational_task",
            entity_id=task.id,
            metadata={"reason": payload.reason},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(task)
        return _to_detail(db, current_user.tenant_id, task)
    except TaskNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TaskVersionConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "TASK_VERSION_CONFLICT", "message": str(exc)},
        ) from exc
    except InvalidTaskTransitionError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/reassign", response_model=TaskDetailOut)
def reassign_task(
    task_id: uuid.UUID,
    payload: TaskReassignRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.TASK_ASSIGN)),
    _csrf: None = Depends(verify_csrf),
) -> TaskDetailOut:
    try:
        task = operational_task_service.reassign_task(
            db,
            current_user.tenant_id,
            current_user,
            task_id,
            expected_version=payload.expected_version,
            assigned_to=payload.assigned_to,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TASK_ASSIGN,
            entity_type="operational_task",
            entity_id=task.id,
            metadata={"assigned_to": str(payload.assigned_to) if payload.assigned_to else None},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(task)
        return _to_detail(db, current_user.tenant_id, task)
    except TaskNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InactiveAssigneeError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except TaskVersionConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "TASK_VERSION_CONFLICT", "message": str(exc)},
        ) from exc
    except TaskNoChangesError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"code": "TASK_NO_CHANGES", "message": str(exc)}
        ) from exc


@router.post("/tasks/{task_id}/reschedule", response_model=TaskDetailOut)
def reschedule_task(
    task_id: uuid.UUID,
    payload: TaskRescheduleRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.TASK_UPDATE)),
    _csrf: None = Depends(verify_csrf),
) -> TaskDetailOut:
    try:
        task = operational_task_service.reschedule_task(
            db,
            current_user.tenant_id,
            current_user,
            task_id,
            expected_version=payload.expected_version,
            due=payload.due,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TASK_RESCHEDULE,
            entity_type="operational_task",
            entity_id=task.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(task)
        return _to_detail(db, current_user.tenant_id, task)
    except TaskNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TaskNotOwnedByAsistenteError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except TerminalTaskError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except TaskVersionConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "TASK_VERSION_CONFLICT", "message": str(exc)},
        ) from exc
    except TaskNoChangesError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"code": "TASK_NO_CHANGES", "message": str(exc)}
        ) from exc


@router.get("/tasks/{task_id}/history", response_model=TaskChangeHistoryListOut)
def get_task_history(
    task_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.TASK_READ)),
    pagination: tuple[int, int] = Depends(_pagination),
) -> TaskChangeHistoryListOut:
    limit, offset = pagination
    try:
        items, total = operational_task_service.list_history(
            db, current_user.tenant_id, task_id, limit=limit, offset=offset
        )
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return TaskChangeHistoryListOut(
        items=[TaskChangeHistoryOut.model_validate(h) for h in items], total=total, limit=limit, offset=offset
    )


@router.post(
    "/tasks/{task_id}/comments", response_model=TaskCommentOut, status_code=status.HTTP_201_CREATED
)
def create_comment(
    task_id: uuid.UUID,
    payload: TaskCommentCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.TASK_UPDATE)),
    _csrf: None = Depends(verify_csrf),
) -> TaskCommentOut:
    try:
        comment = operational_task_service.add_comment(
            db, current_user.tenant_id, current_user, task_id, body=payload.body
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TASK_COMMENT,
            entity_type="task_comment",
            entity_id=comment.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(comment)
        return comment
    except TaskNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TaskNotOwnedByAsistenteError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except TerminalTaskError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/tasks/{task_id}/comments", response_model=TaskCommentListOut)
def list_comments(
    task_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.TASK_READ)),
    pagination: tuple[int, int] = Depends(_pagination),
) -> TaskCommentListOut:
    limit, offset = pagination
    try:
        items, total = operational_task_service.list_comments(
            db, current_user.tenant_id, task_id, limit=limit, offset=offset
        )
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return TaskCommentListOut(
        items=[TaskCommentOut.model_validate(c) for c in items], total=total, limit=limit, offset=offset
    )
