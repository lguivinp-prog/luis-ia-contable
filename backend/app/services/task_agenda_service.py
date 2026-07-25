"""Agenda y alertas derivadas (Fase 2, Bloque 1, Secciones 11/12/15 del plan
/ ADR-0060). Semantica UNICA de "vencido" -- usada sin variante local en
`GET /tasks` (`time_bucket`), `agenda-summary`, `alerts-summary` y el
`task_summary` del dashboard. Nunca vuelve a consultar
`professional_fee_risk` directamente: una cobranza atrasada ya genero su
tarea `FEE_COLLECTION` (Regla 3 de `task_generation_service`); contarla de
nuevo duplicaria el mismo hecho."""

import uuid
from datetime import datetime

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.orm import Session

from app.core.timezone import end_of_day_utc, next_n_days_end_utc, today_lima
from app.models.operational_task import OperationalTask

_NON_TERMINAL = OperationalTask.status.notin_(["COMPLETED", "CANCELLED"])


class InvalidAssignedToFilterError(Exception):
    pass


def is_overdue_condition(now: datetime) -> ColumnElement:
    return and_(OperationalTask.due_at < now, _NON_TERMINAL)


def due_today_condition(now: datetime) -> ColumnElement:
    today = today_lima(now)
    return and_(
        OperationalTask.due_at >= now, OperationalTask.due_at <= end_of_day_utc(today), _NON_TERMINAL
    )


def due_next_7_days_condition(now: datetime) -> ColumnElement:
    today = today_lima(now)
    return and_(
        OperationalTask.due_at > end_of_day_utc(today),
        OperationalTask.due_at <= next_n_days_end_utc(today, 7),
        _NON_TERMINAL,
    )


_TIME_BUCKET_CONDITIONS = {
    "OVERDUE": is_overdue_condition,
    "TODAY": due_today_condition,
    "NEXT_7_DAYS": due_next_7_days_condition,
}


def time_bucket_condition(bucket: str, now: datetime) -> ColumnElement:
    builder = _TIME_BUCKET_CONDITIONS.get(bucket)
    if builder is None:
        raise InvalidAssignedToFilterError(f"time_bucket invalido: {bucket}")
    return builder(now)


def resolve_assigned_to_filter(value: str, current_user_id: uuid.UUID) -> ColumnElement:
    """`"me"` se resuelve en el backend contra el usuario autenticado,
    `"unassigned"` equivale a `IS NULL`, cualquier otro valor se parsea como
    UUID literal. Nunca se acepta cadena vacia/null como "sin responsable"
    implicito -- solo el literal `"unassigned"`."""
    if value == "me":
        return OperationalTask.assigned_to == current_user_id
    if value == "unassigned":
        return OperationalTask.assigned_to.is_(None)
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise InvalidAssignedToFilterError(f"assigned_to invalido: {value}") from exc
    return OperationalTask.assigned_to == parsed


def compute_agenda_summary(db: Session, tenant_id: uuid.UUID, now: datetime) -> dict:
    def _count(condition: ColumnElement) -> int:
        return db.execute(
            select(func.count())
            .select_from(OperationalTask)
            .where(OperationalTask.tenant_id == tenant_id, condition)
        ).scalar_one()

    return {
        "overdue_tasks": _count(is_overdue_condition(now)),
        "due_today": _count(due_today_condition(now)),
        "due_next_7_days": _count(due_next_7_days_condition(now)),
        "urgent_tasks": _count(and_(OperationalTask.priority == "URGENT", _NON_TERMINAL)),
        "unassigned_tasks": _count(and_(OperationalTask.assigned_to.is_(None), _NON_TERMINAL)),
    }


def compute_alerts_summary(db: Session, tenant_id: uuid.UUID, now: datetime) -> dict:
    def _count(condition: ColumnElement) -> int:
        return db.execute(
            select(func.count())
            .select_from(OperationalTask)
            .where(OperationalTask.tenant_id == tenant_id, condition)
        ).scalar_one()

    source_due_mismatch_condition = and_(
        OperationalTask.due_is_overridden.is_(True),
        OperationalTask.source_due_at.isnot(None),
        OperationalTask.source_due_at != OperationalTask.due_at,
        _NON_TERMINAL,
    )

    return {
        "overdue_tasks": _count(is_overdue_condition(now)),
        "due_today": _count(due_today_condition(now)),
        "urgent_tasks": _count(and_(OperationalTask.priority == "URGENT", _NON_TERMINAL)),
        "unassigned_tasks": _count(and_(OperationalTask.assigned_to.is_(None), _NON_TERMINAL)),
        "source_due_mismatch": _count(source_due_mismatch_condition),
    }


def compute_task_summary_for_dashboard(
    db: Session, tenant_id: uuid.UUID, current_user_id: uuid.UUID, now: datetime
) -> dict:
    summary = compute_agenda_summary(db, tenant_id, now)
    assigned_to_me = db.execute(
        select(func.count())
        .select_from(OperationalTask)
        .where(
            OperationalTask.tenant_id == tenant_id,
            OperationalTask.assigned_to == current_user_id,
            _NON_TERMINAL,
        )
    ).scalar_one()
    summary["assigned_to_me"] = assigned_to_me
    return summary
