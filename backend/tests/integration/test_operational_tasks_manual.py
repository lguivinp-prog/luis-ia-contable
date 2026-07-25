"""Ciclo de vida de tareas manuales via API real (Fase 2, Bloque 1, Dominio
B): creacion (incluida la precision de `client_id` opcional), PATCH con
rechazo de mutaciones sin cambio real, transiciones de estado, reasignacion,
reprogramacion, comentarios y autorizacion por fila del asistente."""

import uuid
from datetime import date, timedelta

from tests.integration._declaration_helpers import make_client, make_other_tenant_with_user

_DUE = (date.today() + timedelta(days=5)).isoformat()


def _due_payload(due_date: str = _DUE) -> dict:
    return {"due_date": due_date}


def _create_task(client_api, **overrides) -> dict:
    payload = {
        "title": "Revisar documentacion pendiente",
        "description": "Descripcion de prueba",
        "task_type": "GENERAL",
        "priority": "NORMAL",
        "due": _due_payload(),
    }
    payload.update(overrides)
    response = client_api.post("/api/v1/tasks", json=payload)
    return response


def test_create_manual_task_without_anchor(auth_client) -> None:
    response = _create_task(auth_client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["client_id"] is None
    assert body["is_system_generated"] is False
    assert body["status"] == "PENDING"
    assert body["version"] == 1


def test_create_manual_task_with_client_id(auth_client, db_session, tenant) -> None:
    client_row = make_client(db_session, tenant.id)
    db_session.flush()
    response = _create_task(auth_client, client_id=str(client_row.id))
    assert response.status_code == 201, response.text
    assert response.json()["client_id"] == str(client_row.id)


def test_create_manual_task_rejects_client_of_another_tenant(auth_client, db_session) -> None:
    other_tenant, _ = make_other_tenant_with_user(db_session)
    other_client = make_client(db_session, other_tenant.id)
    db_session.flush()
    response = _create_task(auth_client, client_id=str(other_client.id))
    assert response.status_code == 422, response.text


def test_patch_rejects_client_id_field(auth_client) -> None:
    created = _create_task(auth_client).json()
    response = auth_client.patch(
        f"/api/v1/tasks/{created['id']}", json={"expected_version": 1, "client_id": str(uuid.uuid4())}
    )
    assert response.status_code == 422


def test_asistente_creates_task_for_self(asistente_client, asistente_user) -> None:
    response = _create_task(asistente_client, assigned_to=str(asistente_user.id))
    assert response.status_code == 201, response.text


def test_asistente_cannot_assign_task_to_another_user(asistente_client, admin_user) -> None:
    response = _create_task(asistente_client, assigned_to=str(admin_user.id))
    assert response.status_code == 403, response.text


def test_patch_without_editable_fields_returns_422(auth_client) -> None:
    created = _create_task(auth_client).json()
    response = auth_client.patch(f"/api/v1/tasks/{created['id']}", json={"expected_version": 1})
    assert response.status_code == 422


def test_patch_without_real_change_returns_409_task_no_changes(auth_client) -> None:
    created = _create_task(auth_client).json()
    response = auth_client.patch(
        f"/api/v1/tasks/{created['id']}",
        json={"expected_version": 1, "title": created["title"]},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TASK_NO_CHANGES"


def test_patch_with_real_change_increments_version(auth_client) -> None:
    created = _create_task(auth_client).json()
    response = auth_client.patch(
        f"/api/v1/tasks/{created['id']}",
        json={"expected_version": 1, "title": "Titulo actualizado"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "Titulo actualizado"
    assert body["version"] == 2


def test_patch_with_stale_version_returns_409_version_conflict(auth_client) -> None:
    created = _create_task(auth_client).json()
    response = auth_client.patch(
        f"/api/v1/tasks/{created['id']}",
        json={"expected_version": 99, "title": "Otro titulo"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TASK_VERSION_CONFLICT"


def test_start_complete_lifecycle(auth_client) -> None:
    created = _create_task(auth_client).json()
    started = auth_client.post(
        f"/api/v1/tasks/{created['id']}/start", json={"expected_version": 1}
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "IN_PROGRESS"

    completed = auth_client.post(
        f"/api/v1/tasks/{created['id']}/complete", json={"expected_version": 2}
    )
    assert completed.status_code == 200, completed.text
    body = completed.json()
    assert body["status"] == "COMPLETED"
    assert body["resolution_origin"] == "MANUAL"
    assert body["completed_by"] is not None


def test_complete_already_completed_task_returns_409(auth_client) -> None:
    created = _create_task(auth_client).json()
    auth_client.post(f"/api/v1/tasks/{created['id']}/complete", json={"expected_version": 1})
    response = auth_client.post(
        f"/api/v1/tasks/{created['id']}/complete", json={"expected_version": 2}
    )
    assert response.status_code == 409


def test_cancel_requires_reason(auth_client) -> None:
    created = _create_task(auth_client).json()
    response = auth_client.post(
        f"/api/v1/tasks/{created['id']}/cancel", json={"expected_version": 1, "reason": "   "}
    )
    assert response.status_code == 422


def test_cancel_with_reason_succeeds(auth_client) -> None:
    created = _create_task(auth_client).json()
    response = auth_client.post(
        f"/api/v1/tasks/{created['id']}/cancel",
        json={"expected_version": 1, "reason": "Ya no aplica"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "CANCELLED"
    assert body["cancellation_reason"] == "Ya no aplica"


def test_reassign_to_same_assignee_returns_409_no_changes(auth_client) -> None:
    created = _create_task(auth_client).json()
    response = auth_client.post(
        f"/api/v1/tasks/{created['id']}/reassign", json={"expected_version": 1, "assigned_to": None}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TASK_NO_CHANGES"


def test_reassign_to_inactive_user_returns_422(auth_client, db_session, tenant, admin_user) -> None:
    from datetime import UTC, datetime

    from app.core.security import hash_password
    from app.models.user import User

    inactive = User(
        tenant_id=tenant.id,
        email=f"inactivo-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
        full_name="Usuario inactivo",
        password_hash=hash_password("TestPassword123!"),
        role="asistente",
        is_active=False,
        deactivated_at=datetime.now(UTC),
        deactivated_by=admin_user.id,
        deactivation_reason="Prueba",
    )
    db_session.add(inactive)
    db_session.flush()

    created = _create_task(auth_client).json()
    response = auth_client.post(
        f"/api/v1/tasks/{created['id']}/reassign",
        json={"expected_version": 1, "assigned_to": str(inactive.id)},
    )
    assert response.status_code == 422


def test_reassign_requires_task_assign_permission(asistente_client) -> None:
    created = _create_task(asistente_client).json()
    response = asistente_client.post(
        f"/api/v1/tasks/{created['id']}/reassign", json={"expected_version": 1, "assigned_to": None}
    )
    assert response.status_code == 403


def test_asistente_cannot_operate_task_not_assigned_to_self(
    auth_client, asistente_client, asistente_user
) -> None:
    created = _create_task(auth_client).json()  # sin responsable
    response = asistente_client.post(
        f"/api/v1/tasks/{created['id']}/start", json={"expected_version": 1}
    )
    assert response.status_code == 403


def test_asistente_can_operate_own_assigned_task(auth_client, asistente_client, asistente_user) -> None:
    created = _create_task(auth_client, assigned_to=str(asistente_user.id)).json()
    response = asistente_client.post(
        f"/api/v1/tasks/{created['id']}/start", json={"expected_version": 1}
    )
    assert response.status_code == 200, response.text


def test_reschedule_updates_due_at(auth_client) -> None:
    created = _create_task(auth_client).json()
    new_due = (date.today() + timedelta(days=10)).isoformat()
    response = auth_client.post(
        f"/api/v1/tasks/{created['id']}/reschedule",
        json={"expected_version": 1, "due": {"due_date": new_due}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2


def test_reschedule_without_real_change_returns_409(auth_client) -> None:
    created = _create_task(auth_client).json()
    response = auth_client.post(
        f"/api/v1/tasks/{created['id']}/reschedule",
        json={"expected_version": 1, "due": _due_payload()},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TASK_NO_CHANGES"


def test_comment_flow(auth_client) -> None:
    created = _create_task(auth_client).json()
    response = auth_client.post(
        f"/api/v1/tasks/{created['id']}/comments", json={"body": "Comentario de prueba"}
    )
    assert response.status_code == 201, response.text

    listed = auth_client.get(f"/api/v1/tasks/{created['id']}/comments")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1


def test_comment_on_terminal_task_returns_409(auth_client) -> None:
    created = _create_task(auth_client).json()
    auth_client.post(f"/api/v1/tasks/{created['id']}/complete", json={"expected_version": 1})
    response = auth_client.post(
        f"/api/v1/tasks/{created['id']}/comments", json={"body": "Comentario tardio"}
    )
    assert response.status_code == 409


def test_comment_rejects_empty_body(auth_client) -> None:
    created = _create_task(auth_client).json()
    response = auth_client.post(f"/api/v1/tasks/{created['id']}/comments", json={"body": "   "})
    assert response.status_code == 422


def test_get_tasks_filters_by_client_id(auth_client, db_session, tenant) -> None:
    client_row = make_client(db_session, tenant.id)
    db_session.flush()
    _create_task(auth_client, client_id=str(client_row.id))
    _create_task(auth_client)

    response = auth_client.get("/api/v1/tasks", params={"client_id": str(client_row.id)})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["client_id"] == str(client_row.id)


def test_history_records_created_event(auth_client) -> None:
    created = _create_task(auth_client).json()
    response = auth_client.get(f"/api/v1/tasks/{created['id']}/history")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["event_type"] == "CREATED"


def test_get_task_detail_includes_computed_fields(auth_client) -> None:
    created = _create_task(auth_client).json()
    response = auth_client.get(f"/api/v1/tasks/{created['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["assignee_is_active"] is None
    assert body["source_due_mismatch"] is False


def test_manual_task_type_restricted_to_manual_set(auth_client) -> None:
    response = _create_task(auth_client, task_type="DECLARATION_FILING")
    assert response.status_code == 422
