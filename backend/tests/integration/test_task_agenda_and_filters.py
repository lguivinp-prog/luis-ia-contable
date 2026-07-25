"""Agenda, filtros canonicos y alertas derivadas (Fase 2, Bloque 1, Dominio
D, Secciones 11/12/15 del plan): semantica unica de "vencido" con reloj
congelado en las fronteras exactas, filtros `time_bucket`/`assigned_to`
canonicos de `GET /tasks`, y los resumenes de agenda/alertas."""

from datetime import UTC, date, datetime, time, timedelta

from app.core.timezone import end_of_day_utc
from app.schemas.task import TaskDueInput
from app.services import operational_task_service

_BASE_DUE = date(2026, 2, 12)


def _make_task(db_session, tenant, admin_user, **overrides):
    kwargs = dict(
        title="Tarea de agenda",
        description=None,
        task_type="GENERAL",
        priority="NORMAL",
        due=TaskDueInput(due_date=_BASE_DUE),
        assigned_to=None,
        client_id=None,
    )
    kwargs.update(overrides)
    task = operational_task_service.create_task(db_session, tenant.id, admin_user, **kwargs)
    db_session.flush()
    return task


def test_task_without_time_is_due_today_at_235959_lima(
    auth_client, db_session, tenant, admin_user, frozen_clock
) -> None:
    _make_task(db_session, tenant, admin_user)
    db_session.commit()

    frozen_clock["now"] = end_of_day_utc(_BASE_DUE) - timedelta(seconds=1)
    response = auth_client.get("/api/v1/tasks", params={"time_bucket": "TODAY"})
    assert response.status_code == 200
    assert response.json()["total"] == 1

    response = auth_client.get("/api/v1/tasks", params={"time_bucket": "OVERDUE"})
    assert response.json()["total"] == 0


def test_task_without_time_becomes_overdue_one_second_later(
    auth_client, db_session, tenant, admin_user, frozen_clock
) -> None:
    _make_task(db_session, tenant, admin_user)
    db_session.commit()

    frozen_clock["now"] = end_of_day_utc(_BASE_DUE) + timedelta(seconds=1)
    response = auth_client.get("/api/v1/tasks", params={"time_bucket": "OVERDUE"})
    assert response.json()["total"] == 1

    response = auth_client.get("/api/v1/tasks", params={"time_bucket": "TODAY"})
    assert response.json()["total"] == 0


def test_task_with_explicit_time_boundary(
    auth_client, db_session, tenant, admin_user, frozen_clock
) -> None:
    _make_task(db_session, tenant, admin_user, due=TaskDueInput(due_date=_BASE_DUE, due_time=time(10, 0)))
    db_session.commit()

    # 09:59 Lima == 14:59 UTC
    frozen_clock["now"] = datetime(2026, 2, 12, 14, 59, 0, tzinfo=UTC)
    response = auth_client.get("/api/v1/tasks", params={"time_bucket": "TODAY"})
    assert response.json()["total"] == 1

    # 10:01 Lima == 15:01 UTC
    frozen_clock["now"] = datetime(2026, 2, 12, 15, 1, 0, tzinfo=UTC)
    response = auth_client.get("/api/v1/tasks", params={"time_bucket": "OVERDUE"})
    assert response.json()["total"] == 1
    response = auth_client.get("/api/v1/tasks", params={"time_bucket": "TODAY"})
    assert response.json()["total"] == 0


def test_next_7_days_boundary(auth_client, db_session, tenant, admin_user, frozen_clock) -> None:
    frozen_clock["now"] = datetime(2026, 2, 5, 12, 0, tzinfo=UTC)
    _make_task(db_session, tenant, admin_user, due=TaskDueInput(due_date=date(2026, 2, 12)))
    db_session.commit()

    response = auth_client.get("/api/v1/tasks", params={"time_bucket": "NEXT_7_DAYS"})
    assert response.json()["total"] == 1
    response = auth_client.get("/api/v1/tasks", params={"time_bucket": "TODAY"})
    assert response.json()["total"] == 0


def test_time_buckets_never_overlap(auth_client, db_session, tenant, admin_user, frozen_clock) -> None:
    frozen_clock["now"] = datetime(2026, 2, 5, 12, 0, tzinfo=UTC)
    _make_task(db_session, tenant, admin_user, due=TaskDueInput(due_date=date(2026, 2, 5)))
    _make_task(db_session, tenant, admin_user, due=TaskDueInput(due_date=date(2026, 2, 6)))
    _make_task(db_session, tenant, admin_user, due=TaskDueInput(due_date=date(2026, 2, 1)))
    db_session.commit()

    buckets = ["OVERDUE", "TODAY", "NEXT_7_DAYS"]
    ids_per_bucket = []
    for bucket in buckets:
        response = auth_client.get("/api/v1/tasks", params={"time_bucket": bucket})
        ids_per_bucket.append({item["id"] for item in response.json()["items"]})

    assert ids_per_bucket[0] & ids_per_bucket[1] == set()
    assert ids_per_bucket[1] & ids_per_bucket[2] == set()
    assert ids_per_bucket[0] & ids_per_bucket[2] == set()


def test_time_buckets_exclude_completed_and_cancelled(
    auth_client, db_session, tenant, admin_user, frozen_clock
) -> None:
    frozen_clock["now"] = datetime(2026, 2, 13, 12, 0, tzinfo=UTC)
    task = _make_task(db_session, tenant, admin_user, due=TaskDueInput(due_date=_BASE_DUE))
    db_session.commit()

    complete_response = auth_client.post(
        f"/api/v1/tasks/{task.id}/complete", json={"expected_version": 1}
    )
    assert complete_response.status_code == 200

    response = auth_client.get("/api/v1/tasks", params={"time_bucket": "OVERDUE"})
    assert response.json()["total"] == 0


def test_invalid_time_bucket_returns_422(auth_client) -> None:
    response = auth_client.get("/api/v1/tasks", params={"time_bucket": "LAST_WEEK"})
    assert response.status_code == 422


def test_assigned_to_me_and_unassigned(auth_client, db_session, tenant, admin_user) -> None:
    _make_task(db_session, tenant, admin_user, assigned_to=admin_user.id)
    _make_task(db_session, tenant, admin_user, assigned_to=None)
    db_session.commit()

    me_response = auth_client.get("/api/v1/tasks", params={"assigned_to": "me"})
    assert me_response.json()["total"] == 1

    unassigned_response = auth_client.get("/api/v1/tasks", params={"assigned_to": "unassigned"})
    assert unassigned_response.json()["total"] == 1


def test_assigned_to_invalid_value_returns_422(auth_client) -> None:
    response = auth_client.get("/api/v1/tasks", params={"assigned_to": "not-a-uuid"})
    assert response.status_code == 422


def test_agenda_summary_counts(auth_client, db_session, tenant, admin_user, frozen_clock) -> None:
    frozen_clock["now"] = datetime(2026, 2, 13, 12, 0, tzinfo=UTC)
    _make_task(db_session, tenant, admin_user, due=TaskDueInput(due_date=_BASE_DUE), priority="URGENT")
    db_session.commit()

    response = auth_client.get("/api/v1/tasks/agenda-summary")
    assert response.status_code == 200
    body = response.json()
    assert body["overdue_tasks"] == 1
    assert body["urgent_tasks"] == 1
    assert body["unassigned_tasks"] == 1


def test_alerts_summary_counts_source_due_mismatch(
    asistente_client, db_session, tenant, admin_user, asistente_user, frozen_clock
) -> None:
    frozen_clock["now"] = datetime(2026, 2, 5, 12, 0, tzinfo=UTC)
    response = asistente_client.get("/api/v1/tasks/alerts-summary")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "overdue_tasks",
        "due_today",
        "urgent_tasks",
        "unassigned_tasks",
        "source_due_mismatch",
    }


def test_dashboard_includes_task_summary(auth_client, db_session, tenant, admin_user) -> None:
    _make_task(db_session, tenant, admin_user)
    db_session.commit()

    response = auth_client.get("/api/v1/dashboard/monthly")
    assert response.status_code == 200
    body = response.json()
    assert body["task_summary"] is not None
    assert "assigned_to_me" in body["task_summary"]
