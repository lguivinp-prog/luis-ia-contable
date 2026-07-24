"""Reporte de integridad BD-disco (Bloque 5, Dominio F, ver Seccion 12 del
plan -- ADR-0055): las 5 categorias exactas (`missing_blob`, `orphan_blob`,
`hash_mismatch`, `retained_deleted_blob`, `temporary_part`), filtro por
categoria, permiso `document:manage`, y que ninguna entrada expone
`storage_key` ni ninguna ruta fisica."""

import io
import uuid

from app.core.file_storage import LocalFileStorageBackend
from app.models.declaration_document_file import DeclarationDocumentFile
from app.services import declaration_service
from tests.integration._declaration_helpers import (
    make_client,
    make_client_obligation,
    make_entry,
    make_monthly_period,
    make_source,
    make_version,
)

_PDF_BYTES = b"%PDF-1.4\n" + b"0" * 200


def _setup_item(auth_client, db_session, tenant, admin_user, obligation_catalog):
    client_row = make_client(db_session, tenant.id)
    obligation = obligation_catalog["621"]
    co = make_client_obligation(db_session, tenant.id, client_row.id, obligation.id)
    source = make_source(db_session, tenant.id, obligation.id)
    version = make_version(db_session, tenant.id, source.id, fiscal_year=2026)
    make_entry(db_session, tenant.id, version.id, tax_period="2026-01", digits=list(range(10)))
    period = make_monthly_period(db_session, tenant.id, fiscal_year=2026, month=1)

    declaration = declaration_service.create_declaration(
        db_session,
        tenant.id,
        admin_user.id,
        client_id=client_row.id,
        client_obligation_id=co.id,
        tax_period_id=period.id,
        origin="MANUAL",
        sync_documents=False,
    )
    db_session.commit()

    catalog_response = auth_client.post(
        "/api/v1/document-requirement-catalog",
        json={"code": f"COMPROBANTE_{uuid.uuid4().hex[:8]}", "name": "Comprobante de prueba"},
    )
    assert catalog_response.status_code == 201
    catalog_id = catalog_response.json()["id"]

    assign_response = auth_client.post(
        f"/api/v1/clients/{client_row.id}/document-requirements",
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

    items = auth_client.get(f"/api/v1/declarations/{declaration.id}/documents").json()
    assert len(items) == 1
    return declaration, items[0]["id"]


def _upload(client, declaration_id, item_id, *, filename="comprobante.pdf", data=_PDF_BYTES):
    files = {"file": (filename, io.BytesIO(data), "application/pdf")}
    return client.post(
        f"/api/v1/declarations/{declaration_id}/documents/{item_id}/files",
        files=files,
    )


def _file_row(db_session, file_id: str) -> DeclarationDocumentFile:
    return db_session.get(DeclarationDocumentFile, uuid.UUID(file_id))


def test_integrity_report_classifies_all_five_categories(
    auth_client, db_session, tenant, admin_user, obligation_catalog, tmp_path
) -> None:
    storage = LocalFileStorageBackend(tmp_path / "document_storage")
    declaration, item_id = _setup_item(auth_client, db_session, tenant, admin_user, obligation_catalog)

    # Fila ACTIVE sana (no debe generar ninguna entrada).
    healthy = _upload(auth_client, declaration.id, item_id, filename="sano.pdf")
    assert healthy.status_code == 201

    # missing_blob: fila ACTIVE cuyo blob se borra directamente del disco.
    missing = _upload(auth_client, declaration.id, item_id, filename="perdido.pdf")
    assert missing.status_code == 201
    missing_row = _file_row(db_session, missing.json()["id"])
    storage.delete(missing_row.storage_key)

    # hash_mismatch: fila ACTIVE cuyo contenido fisico se corrompe.
    mismatch = _upload(auth_client, declaration.id, item_id, filename="corrupto.pdf")
    assert mismatch.status_code == 201
    mismatch_row = _file_row(db_session, mismatch.json()["id"])
    (tmp_path / "document_storage" / mismatch_row.storage_key).write_bytes(b"%PDF-1.4\ncontenido alterado")

    # retained_deleted_blob: fila DELETED cuyo blob se conserva a proposito.
    to_delete = _upload(auth_client, declaration.id, item_id, filename="a-eliminar.pdf")
    assert to_delete.status_code == 201
    delete_response = auth_client.post(
        f"/api/v1/declarations/{declaration.id}/documents/{item_id}/files/{to_delete.json()['id']}/delete",
        json={"reason": "prueba de reporte de integridad"},
    )
    assert delete_response.status_code == 200
    deleted_row = _file_row(db_session, to_delete.json()["id"])
    assert storage.exists(deleted_row.storage_key)

    # orphan_blob: archivo fisico sin ninguna fila en BD.
    orphan_key = f"{tenant.id}/{item_id}/{uuid.uuid4()}/v1-huerfano.pdf"
    orphan_path = tmp_path / "document_storage" / orphan_key
    orphan_path.parent.mkdir(parents=True, exist_ok=True)
    orphan_path.write_bytes(b"%PDF-1.4\nhuerfano")

    # temporary_part: archivo .part abandonado (proceso interrumpido).
    part_key = f"{tenant.id}/{item_id}/{uuid.uuid4()}/v1.part"
    part_path = tmp_path / "document_storage" / part_key
    part_path.parent.mkdir(parents=True, exist_ok=True)
    part_path.write_bytes(b"contenido parcial")

    response = auth_client.get("/api/v1/document-files/integrity-report", params={"limit": 200})
    assert response.status_code == 200
    body = response.json()

    assert body["counts"]["missing_blob"] == 1
    assert body["counts"]["hash_mismatch"] == 1
    assert body["counts"]["retained_deleted_blob"] == 1
    assert body["counts"]["orphan_blob"] == 1
    assert body["counts"]["temporary_part"] == 1

    by_category = {}
    for entry in body["items"]:
        by_category.setdefault(entry["category"], []).append(entry)
        # Ninguna entrada expone storage_key ni ninguna ruta fisica.
        assert "storage_key" not in entry
        assert str(tmp_path) not in str(entry)

    assert by_category["missing_blob"][0]["file_id"] == str(missing_row.id)
    assert by_category["hash_mismatch"][0]["file_id"] == str(mismatch_row.id)
    assert by_category["hash_mismatch"][0]["expected_hash"] != by_category["hash_mismatch"][0]["actual_hash"]
    assert by_category["retained_deleted_blob"][0]["file_id"] == str(deleted_row.id)
    assert by_category["retained_deleted_blob"][0]["status"] == "DELETED"

    for entry in by_category["orphan_blob"] + by_category["temporary_part"]:
        assert entry["file_id"] is None
        assert entry["blob_fingerprint"] is not None
        assert entry["basename"] is not None


def test_integrity_report_filters_by_category(
    auth_client, db_session, tenant, admin_user, obligation_catalog, tmp_path
) -> None:
    storage = LocalFileStorageBackend(tmp_path / "document_storage")
    declaration, item_id = _setup_item(auth_client, db_session, tenant, admin_user, obligation_catalog)

    upload = _upload(auth_client, declaration.id, item_id)
    assert upload.status_code == 201
    row = _file_row(db_session, upload.json()["id"])
    storage.delete(row.storage_key)

    response = auth_client.get(
        "/api/v1/document-files/integrity-report", params={"category": "orphan_blob"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert all(item["category"] == "orphan_blob" for item in body["items"])

    response = auth_client.get(
        "/api/v1/document-files/integrity-report", params={"category": "missing_blob"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["category"] == "missing_blob"


def test_integrity_report_rejects_invalid_category(auth_client) -> None:
    response = auth_client.get(
        "/api/v1/document-files/integrity-report", params={"category": "no_existe"}
    )
    assert response.status_code == 422


def test_integrity_report_requires_document_manage_permission(asistente_client) -> None:
    response = asistente_client.get("/api/v1/document-files/integrity-report")
    assert response.status_code == 403


def test_integrity_report_isolated_by_tenant(
    auth_client, db_session, tenant, admin_user, obligation_catalog, tmp_path
) -> None:
    declaration, item_id = _setup_item(auth_client, db_session, tenant, admin_user, obligation_catalog)
    upload = _upload(auth_client, declaration.id, item_id)
    assert upload.status_code == 201

    other_tenant_key = f"{uuid.uuid4()}/{item_id}/{uuid.uuid4()}/v1-otro-tenant.pdf"
    other_path = tmp_path / "document_storage" / other_tenant_key
    other_path.parent.mkdir(parents=True, exist_ok=True)
    other_path.write_bytes(b"%PDF-1.4\nde otro tenant")

    response = auth_client.get("/api/v1/document-files/integrity-report", params={"limit": 200})
    assert response.status_code == 200
    body = response.json()
    assert body["counts"].get("orphan_blob", 0) == 0
