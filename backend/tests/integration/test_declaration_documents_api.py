"""Pruebas HTTP de los endpoints de Control documental (Bloque 3.2, Dominio
A): catalogo, asignacion por cliente, sincronizacion (individual y masiva por
periodo), y cambio de estado de item, incluyendo auditoria y CSRF."""

from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.services import declaration_service
from tests.integration._declaration_helpers import (
    make_client,
    make_client_obligation,
    make_entry,
    make_monthly_period,
    make_source,
    make_version,
)


def _setup_declaration(db_session, tenant, admin_user, obligation_catalog):
    client = make_client(db_session, tenant.id)
    obligation = obligation_catalog["621"]
    co = make_client_obligation(db_session, tenant.id, client.id, obligation.id)
    source = make_source(db_session, tenant.id, obligation.id)
    version = make_version(db_session, tenant.id, source.id, fiscal_year=2026)
    make_entry(db_session, tenant.id, version.id, tax_period="2026-01", digits=list(range(10)))
    period = make_monthly_period(db_session, tenant.id, fiscal_year=2026, month=1)

    declaration = declaration_service.create_declaration(
        db_session,
        tenant.id,
        admin_user.id,
        client_id=client.id,
        client_obligation_id=co.id,
        tax_period_id=period.id,
        origin="MANUAL",
        sync_documents=False,
    )
    db_session.commit()
    return client, co, period, declaration


def test_create_catalog_entry_via_api(auth_client, db_session, tenant) -> None:
    response = auth_client.post(
        "/api/v1/document-requirement-catalog",
        json={"code": "RENTA_ANUAL", "name": "Constancia de renta anual"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["is_system"] is False
    assert body["tenant_id"] == str(tenant.id)

    audit_rows = db_session.execute(
        select(AuditLog).where(AuditLog.action == "DOCUMENT_CATALOG_CREATE")
    ).scalars().all()
    assert len(audit_rows) == 1


def test_assign_requirement_and_sync_via_api(
    auth_client, db_session, tenant, admin_user, obligation_catalog
) -> None:
    client, co, period, declaration = _setup_declaration(
        db_session, tenant, admin_user, obligation_catalog
    )

    catalog_response = auth_client.post(
        "/api/v1/document-requirement-catalog",
        json={"code": "RENTA_ANUAL", "name": "Constancia de renta anual"},
    )
    assert catalog_response.status_code == 201
    catalog_id = catalog_response.json()["id"]

    assign_response = auth_client.post(
        f"/api/v1/clients/{client.id}/document-requirements",
        json={
            "requirement_catalog_id": catalog_id,
            "periodicity": "MONTHLY",
            "valid_from": "2026-01-01",
        },
    )
    assert assign_response.status_code == 201

    sync_response = auth_client.post(
        f"/api/v1/declarations/{declaration.id}/documents/sync-requirements"
    )
    assert sync_response.status_code == 200
    body = sync_response.json()
    assert len(body["created"]) == 1
    assert body["existing"] == []

    second_sync = auth_client.post(
        f"/api/v1/declarations/{declaration.id}/documents/sync-requirements"
    )
    assert second_sync.status_code == 200
    assert second_sync.json()["created"] == []
    assert len(second_sync.json()["existing"]) == 1

    list_response = auth_client.get(f"/api/v1/declarations/{declaration.id}/documents")
    assert list_response.status_code == 200
    items = list_response.json()
    assert len(items) == 1

    item_id = items[0]["id"]
    patch_response = auth_client.patch(
        f"/api/v1/declarations/{declaration.id}/documents/{item_id}",
        json={"target": "ACCEPTED"},
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["status"] == "ACCEPTED"


def test_sync_requirements_unknown_declaration_returns_404(auth_client) -> None:
    import uuid

    response = auth_client.post(
        f"/api/v1/declarations/{uuid.uuid4()}/documents/sync-requirements"
    )
    assert response.status_code == 404


def test_bulk_sync_by_period_via_api(
    auth_client, db_session, tenant, admin_user, obligation_catalog
) -> None:
    client, co, period, declaration = _setup_declaration(
        db_session, tenant, admin_user, obligation_catalog
    )

    catalog_response = auth_client.post(
        "/api/v1/document-requirement-catalog",
        json={"code": "RENTA_ANUAL", "name": "Constancia de renta anual"},
    )
    catalog_id = catalog_response.json()["id"]
    auth_client.post(
        f"/api/v1/clients/{client.id}/document-requirements",
        json={
            "requirement_catalog_id": catalog_id,
            "periodicity": "MONTHLY",
            "valid_from": "2026-01-01",
        },
    )

    bulk_response = auth_client.post(f"/api/v1/tax-periods/{period.id}/documents/sync-requirements")
    assert bulk_response.status_code == 200
    body = bulk_response.json()
    assert body["total_synced"] == 1
    assert body["total_errors"] == 0

    list_response = auth_client.get(f"/api/v1/declarations/{declaration.id}/documents")
    assert len(list_response.json()) == 1
