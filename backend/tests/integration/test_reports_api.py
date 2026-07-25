"""Pruebas HTTP de exportacion de reportes (Bloque 3.2, Dominio F): ver
"Reportes CSV y Excel" del plan. Cubre: los 10 tipos de reporte responden,
CSV/Excel devuelven contenido valido, limite de filas configurable trunca
correctamente, auditoria por exportacion, y CSRF/tipo desconocido."""

import io

from openpyxl import load_workbook
from sqlalchemy import select

from app.core.config import get_settings
from app.models.audit_log import AuditLog
from app.services.report_service import REPORT_TYPES
from tests.integration._declaration_helpers import make_client

settings = get_settings()


def test_all_report_types_export_as_csv(auth_client, db_session, tenant) -> None:
    for report_type in REPORT_TYPES:
        response = auth_client.post(
            "/api/v1/reports/export", json={"report_type": report_type, "format": "csv"}
        )
        assert response.status_code == 200, f"{report_type}: {response.text}"
        assert response.headers["content-type"].startswith("text/csv")
        assert "attachment" in response.headers["content-disposition"]


def test_all_report_types_export_as_xlsx(auth_client, db_session, tenant) -> None:
    for report_type in REPORT_TYPES:
        response = auth_client.post(
            "/api/v1/reports/export", json={"report_type": report_type, "format": "xlsx"}
        )
        assert response.status_code == 200, f"{report_type}: {response.text}"
        workbook = load_workbook(io.BytesIO(response.content))
        assert set(workbook.sheetnames) == {"Datos", "Metadatos"}


def test_clients_report_contains_created_client(auth_client, db_session, tenant) -> None:
    client = make_client(db_session, tenant.id, name="Cliente para reporte")
    db_session.commit()

    response = auth_client.post(
        "/api/v1/reports/export", json={"report_type": "CLIENTS", "format": "csv"}
    )
    assert response.status_code == 200
    assert str(client.id) in response.text
    assert "Cliente para reporte" in response.text


def test_tasks_report_resolves_client_for_fee_collection_task(
    auth_client, db_session, tenant, admin_user
) -> None:
    from datetime import date

    from app.schemas.task import TaskDueInput
    from app.services import operational_task_service

    client = make_client(db_session, tenant.id, name="Cliente reporte tareas")

    task = operational_task_service.create_task(
        db_session,
        tenant.id,
        admin_user,
        title="Cobrar honorario reporte",
        description=None,
        task_type="FEE_COLLECTION",
        priority="URGENT",
        due=TaskDueInput(due_date=date(2026, 2, 15)),
        assigned_to=None,
        client_id=client.id,
    )
    db_session.commit()

    response = auth_client.post("/api/v1/reports/export", json={"report_type": "TASKS", "format": "csv"})
    assert response.status_code == 200
    assert str(task.id) in response.text
    assert str(client.id) in response.text


def test_unknown_report_type_returns_422(auth_client) -> None:
    response = auth_client.post(
        "/api/v1/reports/export", json={"report_type": "NO_EXISTE", "format": "csv"}
    )
    assert response.status_code == 422


def test_report_export_requires_csrf(client, tenant, admin_user) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "TestPassword123!"},
    )
    assert response.status_code == 200
    # Sin X-CSRF-Token (a diferencia de auth_client, que ya lo agrega).
    export_response = client.post(
        "/api/v1/reports/export", json={"report_type": "CLIENTS", "format": "csv"}
    )
    assert export_response.status_code == 403


def test_report_export_row_limit_truncates_and_is_audited(
    auth_client, db_session, tenant, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "report_export_max_rows", 2)
    for i in range(4):
        make_client(db_session, tenant.id, name=f"Cliente limite {i}")
    db_session.commit()

    response = auth_client.post(
        "/api/v1/reports/export", json={"report_type": "CLIENTS", "format": "csv"}
    )
    assert response.status_code == 200
    data_lines = [line for line in response.text.splitlines() if line]
    assert len(data_lines) == 1 + 2  # encabezado + max_rows

    audit_rows = db_session.execute(
        select(AuditLog).where(AuditLog.action == "REPORT_EXPORT")
    ).scalars().all()
    assert len(audit_rows) == 1
    assert audit_rows[0].log_metadata["truncated"] is True
    assert audit_rows[0].log_metadata["row_count"] == 2
