"""Estados operativos MANUALES del cockpit (M1): defaults sin persistir, creacion perezosa, cada estado, validacion,
RBAC, aislamiento, historial, workspace y efecto en filas/tarjetas del cockpit."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, func, select

from app.models.client_period_operation import ClientPeriodOperation, ClientPeriodOperationHistory
from app.models.declaration import Declaration
from tests.integration._declaration_helpers import (
    make_client,
    make_monthly_period,
    make_other_tenant_with_user,
)

BASE = "/api/v1/cockpit/clients/{client_id}/periods/{period_id}"


@pytest.fixture()
def scope(db_session, tenant, frozen_clock):
    frozen_clock["now"] = datetime(2026, 2, 10, 15, 0, tzinfo=UTC)
    client = make_client(db_session, tenant.id, name="Cliente Manual")
    period = make_monthly_period(db_session, tenant.id, fiscal_year=2026, month=1)
    db_session.commit()
    return client, period


def _url(scope, suffix=""):
    client, period = scope
    return BASE.format(client_id=client.id, period_id=period.id) + suffix


def _count(db_session, model):
    return db_session.execute(select(func.count()).select_from(model)).scalar_one()


def _put(http, scope, body):
    return http.put(_url(scope, "/operations"), json=body)


def _cockpit_row(http, scope):
    client, period = scope
    body = http.get("/api/v1/cockpit/monthly", params={"tax_period_id": str(period.id)}).json()
    return body, next(r for r in body["items"] if r["client_id"] == str(client.id))


def test_read_returns_defaults_and_persists_nothing(auth_client, scope, db_session):
    response = auth_client.get(_url(scope))
    assert response.status_code == 200, response.text
    ops = response.json()["operations"]
    assert ops["buzon_status"] == "NOT_REVIEWED"
    assert ops["accounting_status"] == "NOT_STARTED"
    assert ops["client_communication_status"] == "NOT_STARTED"
    assert ops["row_version"] == 0 and ops["notes"] is None
    _cockpit_row(auth_client, scope)
    auth_client.get(_url(scope, "/history"))
    assert _count(db_session, ClientPeriodOperation) == 0
    assert _count(db_session, ClientPeriodOperationHistory) == 0


def test_workspace_read_does_not_create_declarations(auth_client, scope, db_session):
    before = _count(db_session, Declaration)
    body = auth_client.get(_url(scope)).json()
    assert body["summary"]["period_generated"] is False
    assert body["declarations"] == []
    assert _count(db_session, Declaration) == before == 0


def test_workspace_aggregate_shape_has_no_secrets(auth_client, scope):
    body = auth_client.get(_url(scope)).json()
    assert {"summary", "period", "operations", "declarations", "tasks", "communication_drafts", "can_update_operations"} <= set(body)
    assert body["can_update_operations"] is True
    flat = str(body).lower()
    for forbidden in ("password", "secret", "cipher", "token"):
        assert forbidden not in flat


def test_lazy_creation_on_first_update_then_each_state(auth_client, scope, db_session):
    first = _put(auth_client, scope, {"expected_row_version": 0, "buzon_status": "REVIEWED_CLEAR"})
    assert first.status_code == 200, first.text
    assert first.json()["row_version"] == 1 and first.json()["buzon_status"] == "REVIEWED_CLEAR"
    assert _count(db_session, ClientPeriodOperation) == 1

    second = _put(auth_client, scope, {"expected_row_version": 1, "accounting_status": "IN_PROGRESS"})
    assert second.json()["row_version"] == 2 and second.json()["accounting_status"] == "IN_PROGRESS"
    third = _put(auth_client, scope, {"expected_row_version": 2, "client_communication_status": "SENT", "notes": "  Nota  "})
    body = third.json()
    assert body["row_version"] == 3 and body["client_communication_status"] == "SENT" and body["notes"] == "Nota"
    assert body["buzon_status"] == "REVIEWED_CLEAR" and body["accounting_status"] == "IN_PROGRESS"
    assert body["updated_by_name"] == "Admin de prueba" or body["updated_by_name"]
    assert _count(db_session, ClientPeriodOperation) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("buzon_status", "REVIEWED_ACTION_REQUIRED"),
        ("buzon_status", "NOT_APPLICABLE"),
        ("accounting_status", "REVIEWED"),
        ("accounting_status", "COMPLETED"),
        ("accounting_status", "NOT_APPLICABLE"),
        ("client_communication_status", "PREPARED"),
        ("client_communication_status", "NOT_APPLICABLE"),
    ],
)
def test_every_enum_value_is_accepted(auth_client, scope, field, value):
    response = _put(auth_client, scope, {"expected_row_version": 0, field: value})
    assert response.status_code == 200, response.text
    assert response.json()[field] == value


def test_noop_update_creates_nothing_and_keeps_version(auth_client, scope, db_session):
    response = _put(auth_client, scope, {"expected_row_version": 0, "buzon_status": "NOT_REVIEWED"})
    assert response.status_code == 200 and response.json()["row_version"] == 0
    assert _count(db_session, ClientPeriodOperation) == 0
    _put(auth_client, scope, {"expected_row_version": 0, "accounting_status": "COMPLETED"})
    same = _put(auth_client, scope, {"expected_row_version": 1, "accounting_status": "COMPLETED"})
    assert same.json()["row_version"] == 1
    assert _count(db_session, ClientPeriodOperationHistory) == 1


def test_notes_can_be_cleared(auth_client, scope):
    _put(auth_client, scope, {"expected_row_version": 0, "notes": "algo"})
    cleared = _put(auth_client, scope, {"expected_row_version": 1, "notes": None})
    assert cleared.json()["notes"] is None and cleared.json()["row_version"] == 2


@pytest.mark.parametrize(
    "body",
    [
        {"expected_row_version": 0, "buzon_status": "INVENTADO"},
        {"expected_row_version": 0, "accounting_status": "NOT_REVIEWED"},
        {"expected_row_version": 0, "client_communication_status": "COMPLETED"},
        {"expected_row_version": 0, "buzon_status": "REVIEWED_CLEAR", "campo_extra": 1},
        {"expected_row_version": 0, "document_status": "COMPLETE"},
        {"expected_row_version": 0},
        {"buzon_status": "REVIEWED_CLEAR"},
        {"expected_row_version": -1, "buzon_status": "REVIEWED_CLEAR"},
        {"expected_row_version": 0, "buzon_status": None},
        {"expected_row_version": 0, "notes": "x" * 2001},
    ],
)
def test_invalid_payloads_are_rejected(auth_client, scope, db_session, body):
    assert _put(auth_client, scope, body).status_code == 422
    assert _count(db_session, ClientPeriodOperation) == 0


def test_rbac_admin_contador_allowed_asistente_denied(auth_client, contador_client, asistente_client, scope, db_session):
    assert _put(auth_client, scope, {"expected_row_version": 0, "buzon_status": "REVIEWED_CLEAR"}).status_code == 200
    assert _put(contador_client, scope, {"expected_row_version": 1, "accounting_status": "REVIEWED"}).status_code == 200
    denied = _put(asistente_client, scope, {"expected_row_version": 2, "client_communication_status": "SENT"})
    assert denied.status_code == 403
    assert db_session.execute(select(ClientPeriodOperation.row_version)).scalar_one() == 2
    read = asistente_client.get(_url(scope)).json()
    assert read["can_update_operations"] is False
    assert read["operations"]["row_version"] == 2
    assert asistente_client.get(_url(scope, "/history")).status_code == 200


def test_unauthenticated_and_missing_csrf(scope, auth_client):
    from fastapi.testclient import TestClient

    from app.main import app

    anon = TestClient(app)
    assert anon.get(_url(scope)).status_code == 401
    assert anon.put(_url(scope, "/operations"), json={"expected_row_version": 0, "notes": "x"}).status_code in (401, 403)
    auth_client.headers.pop("X-CSRF-Token")
    assert _put(auth_client, scope, {"expected_row_version": 0, "notes": "x"}).status_code == 403


def test_stale_version_gets_409_with_current_version(auth_client, scope, db_session):
    _put(auth_client, scope, {"expected_row_version": 0, "buzon_status": "REVIEWED_CLEAR"})
    stale = _put(auth_client, scope, {"expected_row_version": 0, "accounting_status": "COMPLETED"})
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["code"] == "CLIENT_PERIOD_OPERATIONS_VERSION_CONFLICT" and detail["current_row_version"] == 1
    wrong = _put(auth_client, scope, {"expected_row_version": 7, "accounting_status": "COMPLETED"})
    assert wrong.status_code == 409
    assert db_session.execute(select(ClientPeriodOperation.accounting_status)).scalar_one() == "NOT_STARTED"


def test_cross_tenant_client_and_period_are_404(auth_client, scope, db_session):
    other_tenant, _ = make_other_tenant_with_user(db_session)
    other_client = make_client(db_session, other_tenant.id, name="Ajeno")
    other_period = make_monthly_period(db_session, other_tenant.id, fiscal_year=2026, month=1)
    db_session.commit()
    client, period = scope
    for c, p in ((other_client.id, period.id), (client.id, other_period.id), (uuid.uuid4(), period.id)):
        base = BASE.format(client_id=c, period_id=p)
        assert auth_client.get(base).status_code == 404
        assert auth_client.get(base + "/history").status_code == 404
        put = auth_client.put(base + "/operations", json={"expected_row_version": 0, "buzon_status": "REVIEWED_CLEAR"})
        assert put.status_code == 404
    assert _count(db_session, ClientPeriodOperation) == 0


def test_state_is_isolated_per_client_and_period(auth_client, scope, db_session, tenant):
    client, period = scope
    other_client = make_client(db_session, tenant.id, name="Otro cliente")
    other_period = make_monthly_period(db_session, tenant.id, fiscal_year=2026, month=2)
    db_session.commit()
    _put(auth_client, scope, {"expected_row_version": 0, "buzon_status": "REVIEWED_CLEAR"})
    assert auth_client.get(BASE.format(client_id=other_client.id, period_id=period.id)).json()["operations"]["row_version"] == 0
    assert auth_client.get(BASE.format(client_id=client.id, period_id=other_period.id)).json()["operations"]["row_version"] == 0


def test_history_records_who_when_field_from_to(auth_client, contador_client, scope):
    _put(auth_client, scope, {"expected_row_version": 0, "buzon_status": "REVIEWED_CLEAR", "notes": "a"})
    _put(contador_client, scope, {"expected_row_version": 1, "buzon_status": "REVIEWED_ACTION_REQUIRED"})
    entries = auth_client.get(_url(scope, "/history")).json()
    assert len(entries) == 3
    latest = entries[0]
    assert latest["field_name"] == "buzon_status"
    assert (latest["from_value"], latest["to_value"]) == ("REVIEWED_CLEAR", "REVIEWED_ACTION_REQUIRED")
    assert latest["actor_name"] == "Contador de prueba" and latest["row_version_after"] == 2
    created = [e for e in entries if e["row_version_after"] == 1]
    assert {(e["field_name"], e["from_value"], e["to_value"]) for e in created} == {
        ("buzon_status", "NOT_REVIEWED", "REVIEWED_CLEAR"),
        ("notes", None, "a"),
    }


def test_cockpit_rows_and_cards_reflect_manual_state(auth_client, scope):
    body, row = _cockpit_row(auth_client, scope)
    assert (row["buzon_status"], row["accounting_status"], row["client_communication_status"]) == (
        "NOT_REVIEWED", "NOT_STARTED", "NOT_STARTED",
    )
    s = body["summary"]
    assert (s["buzon_pending"], s["accounting_pending"], s["client_communication_pending"]) == (1, 1, 1)

    _put(auth_client, scope, {"expected_row_version": 0, "buzon_status": "REVIEWED_CLEAR"})
    _put(auth_client, scope, {"expected_row_version": 1, "accounting_status": "COMPLETED"})
    _put(auth_client, scope, {"expected_row_version": 2, "client_communication_status": "NOT_APPLICABLE"})
    body, row = _cockpit_row(auth_client, scope)
    assert (row["buzon_status"], row["accounting_status"], row["client_communication_status"]) == (
        "REVIEWED_CLEAR", "COMPLETED", "NOT_APPLICABLE",
    )
    s = body["summary"]
    assert (s["buzon_pending"], s["accounting_pending"], s["client_communication_pending"]) == (0, 0, 0)
    assert body["total"] == 1

    _put(auth_client, scope, {"expected_row_version": 3, "buzon_status": "REVIEWED_ACTION_REQUIRED"})
    assert _cockpit_row(auth_client, scope)[0]["summary"]["buzon_pending"] == 1


def test_cockpit_query_count_is_constant_with_manual_rows(auth_client, scope, db_session, tenant):
    _client, period = scope

    def run():
        statements = []

        def _record(conn, cursor, statement, *args):
            statements.append(statement)

        engine = db_session.get_bind().engine
        event.listen(engine, "before_cursor_execute", _record)
        try:
            auth_client.get("/api/v1/cockpit/monthly", params={"tax_period_id": str(period.id)})
        finally:
            event.remove(engine, "before_cursor_execute", _record)
        # La autenticacion (sessions/users por PK) puede o no salir del identity map tras un commit: no es del cockpit.
        return [s for s in statements if "SAVEPOINT" not in s and "FROM sessions" not in s and "FROM users" not in s]

    run()  # calentamiento (la primera peticion hace consultas de sesion/arranque adicionales)
    baseline = run()
    for index in range(5):
        extra = make_client(db_session, tenant.id, name=f"Extra {index}")
        db_session.commit()
        auth_client.put(
            BASE.format(client_id=extra.id, period_id=period.id) + "/operations",
            json={"expected_row_version": 0, "buzon_status": "REVIEWED_CLEAR"},
        )
    after = run()
    # Sin N+1: exactamente UNA lectura de operaciones manuales (IN sobre todos los clientes) y el resto constante
    # (salvo refrescos del identity map tras los commit del propio test).
    ops_reads = [s for s in after if "FROM client_period_operations" in s]
    assert len(ops_reads) == 1
    assert len(after) <= len(baseline) + 1, [s[:120] for s in after]


def test_workspace_exposes_sol_capability_only_with_permissions(auth_client, asistente_client, scope):
    assert auth_client.get(_url(scope)).json()["sol_access"] is not None
    assert asistente_client.get(_url(scope)).json()["sol_access"] is None
