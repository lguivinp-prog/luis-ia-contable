"""Flujo HTTP completo de archivos documentales reales (Bloque 5, Dominios
B/C/D del plan): subir, listar, ver metadatos, descargar, reemplazar,
eliminar; permisos (asistente sube pero no reemplaza/eliminar); aislamiento
multitenant; reglas de estado (409 sobre version no ACTIVE); auditoria;
CSRF; legado de `file_metadata`."""

import io

from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.services import declaration_service
from tests.conftest import _login_new_client
from tests.integration._declaration_helpers import (
    make_client,
    make_client_obligation,
    make_entry,
    make_monthly_period,
    make_other_tenant_with_user,
    make_source,
    make_version,
)

_PDF_BYTES = b"%PDF-1.4\n" + b"0" * 200
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 200


def _setup_declaration(db_session, tenant, admin_user, obligation_catalog):
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
    return client_row, declaration


def _setup_item(auth_client, db_session, tenant, admin_user, obligation_catalog):
    client_row, declaration = _setup_declaration(db_session, tenant, admin_user, obligation_catalog)

    catalog_response = auth_client.post(
        "/api/v1/document-requirement-catalog",
        json={"code": "COMPROBANTE_TEST", "name": "Comprobante de prueba"},
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


def _upload(
    client, declaration_id, item_id, *, filename="comprobante.pdf", data=_PDF_BYTES, description=None
):
    files = {"file": (filename, io.BytesIO(data), "application/pdf")}
    form = {"description": description} if description is not None else {}
    return client.post(
        f"/api/v1/declarations/{declaration_id}/documents/{item_id}/files",
        files=files,
        data=form,
    )


def test_upload_creates_first_version_of_new_chain(
    auth_client, db_session, tenant, admin_user, obligation_catalog
) -> None:
    declaration, item_id = _setup_item(auth_client, db_session, tenant, admin_user, obligation_catalog)

    response = _upload(auth_client, declaration.id, item_id, description="primera carga")
    assert response.status_code == 201
    body = response.json()
    assert body["version"] == 1
    assert body["status"] == "ACTIVE"
    assert body["replaces_file_id"] is None
    assert body["original_filename"] == "comprobante.pdf"
    assert body["mime_type"] == "application/pdf"
    assert body["description"] == "primera carga"
    # Nunca debe exponerse storage_key ni ninguna ruta fisica.
    assert "storage_key" not in body

    audit_rows = db_session.execute(
        select(AuditLog).where(AuditLog.action == "DOCUMENT_ITEM_FILE_UPLOAD")
    ).scalars().all()
    assert len(audit_rows) == 1


def test_upload_rejects_content_that_does_not_match_allowlist(
    auth_client, db_session, tenant, admin_user, obligation_catalog
) -> None:
    declaration, item_id = _setup_item(auth_client, db_session, tenant, admin_user, obligation_catalog)
    response = _upload(
        auth_client, declaration.id, item_id, filename="malicioso.pdf", data=b"MZ" + b"\x00" * 200
    )
    assert response.status_code == 422


def test_upload_rejects_empty_file(
    auth_client, db_session, tenant, admin_user, obligation_catalog
) -> None:
    declaration, item_id = _setup_item(auth_client, db_session, tenant, admin_user, obligation_catalog)
    response = _upload(auth_client, declaration.id, item_id, data=b"")
    assert response.status_code == 400


def test_upload_rejects_oversized_file(
    auth_client, db_session, tenant, admin_user, obligation_catalog, monkeypatch
) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "document_file_max_bytes", 100)

    declaration, item_id = _setup_item(auth_client, db_session, tenant, admin_user, obligation_catalog)
    response = _upload(auth_client, declaration.id, item_id, data=_PDF_BYTES)
    assert response.status_code == 413


def test_upload_unknown_declaration_or_item_returns_404(auth_client) -> None:
    import uuid

    response = _upload(auth_client, uuid.uuid4(), uuid.uuid4())
    assert response.status_code == 404


def test_list_and_get_and_download_roundtrip(
    auth_client, db_session, tenant, admin_user, obligation_catalog
) -> None:
    declaration, item_id = _setup_item(auth_client, db_session, tenant, admin_user, obligation_catalog)
    upload_response = _upload(auth_client, declaration.id, item_id)
    file_id = upload_response.json()["id"]

    list_response = auth_client.get(
        f"/api/v1/declarations/{declaration.id}/documents/{item_id}/files"
    )
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1

    get_response = auth_client.get(
        f"/api/v1/declarations/{declaration.id}/documents/{item_id}/files/{file_id}"
    )
    assert get_response.status_code == 200
    assert get_response.json()["id"] == file_id

    download_response = auth_client.get(
        f"/api/v1/declarations/{declaration.id}/documents/{item_id}/files/{file_id}/download"
    )
    assert download_response.status_code == 200
    assert download_response.content == _PDF_BYTES
    assert "attachment" in download_response.headers["content-disposition"]
    assert download_response.headers["x-content-type-options"] == "nosniff"

    audit_rows = db_session.execute(
        select(AuditLog).where(AuditLog.action == "DOCUMENT_ITEM_FILE_DOWNLOAD")
    ).scalars().all()
    assert len(audit_rows) == 1


def test_replace_creates_new_version_and_supersedes_previous(
    auth_client, db_session, tenant, admin_user, obligation_catalog
) -> None:
    declaration, item_id = _setup_item(auth_client, db_session, tenant, admin_user, obligation_catalog)
    first = _upload(auth_client, declaration.id, item_id).json()

    files = {"file": ("v2.png", io.BytesIO(_PNG_BYTES), "image/png")}
    replace_response = auth_client.post(
        f"/api/v1/declarations/{declaration.id}/documents/{item_id}/files/{first['id']}/replace",
        files=files,
        data={"reason": "version corregida"},
    )
    assert replace_response.status_code == 201
    second = replace_response.json()
    assert second["version"] == 2
    assert second["logical_file_id"] == first["logical_file_id"]
    assert second["replaces_file_id"] == first["id"]
    assert second["status"] == "ACTIVE"

    previous = auth_client.get(
        f"/api/v1/declarations/{declaration.id}/documents/{item_id}/files/{first['id']}"
    ).json()
    assert previous["status"] == "SUPERSEDED"
    assert previous["superseded_at"] is not None

    audit_rows = db_session.execute(
        select(AuditLog).where(AuditLog.action == "DOCUMENT_ITEM_FILE_REPLACE")
    ).scalars().all()
    assert len(audit_rows) == 1


def test_replace_requires_non_blank_reason(
    auth_client, db_session, tenant, admin_user, obligation_catalog
) -> None:
    declaration, item_id = _setup_item(auth_client, db_session, tenant, admin_user, obligation_catalog)
    first = _upload(auth_client, declaration.id, item_id).json()

    files = {"file": ("v2.pdf", io.BytesIO(_PDF_BYTES), "application/pdf")}
    response = auth_client.post(
        f"/api/v1/declarations/{declaration.id}/documents/{item_id}/files/{first['id']}/replace",
        files=files,
        data={"reason": "   "},
    )
    assert response.status_code == 422


def test_replace_on_non_active_version_returns_409(
    auth_client, db_session, tenant, admin_user, obligation_catalog
) -> None:
    declaration, item_id = _setup_item(auth_client, db_session, tenant, admin_user, obligation_catalog)
    first = _upload(auth_client, declaration.id, item_id).json()

    files = {"file": ("v2.pdf", io.BytesIO(_PDF_BYTES), "application/pdf")}
    auth_client.post(
        f"/api/v1/declarations/{declaration.id}/documents/{item_id}/files/{first['id']}/replace",
        files=files,
        data={"reason": "primer reemplazo"},
    )

    # Segundo intento de reemplazar la MISMA version original (ya SUPERSEDED).
    files2 = {"file": ("v3.pdf", io.BytesIO(_PDF_BYTES), "application/pdf")}
    second_attempt = auth_client.post(
        f"/api/v1/declarations/{declaration.id}/documents/{item_id}/files/{first['id']}/replace",
        files=files2,
        data={"reason": "segundo intento sobre version vieja"},
    )
    assert second_attempt.status_code == 409


def test_delete_marks_deleted_and_keeps_physical_blob(
    auth_client, db_session, tenant, admin_user, obligation_catalog
) -> None:
    declaration, item_id = _setup_item(auth_client, db_session, tenant, admin_user, obligation_catalog)
    first = _upload(auth_client, declaration.id, item_id).json()

    delete_response = auth_client.post(
        f"/api/v1/declarations/{declaration.id}/documents/{item_id}/files/{first['id']}/delete",
        json={"reason": "documento incorrecto"},
    )
    assert delete_response.status_code == 200
    body = delete_response.json()
    assert body["status"] == "DELETED"
    assert body["deletion_reason"] == "documento incorrecto"
    assert body["deleted_at"] is not None
    assert body["deleted_by"] is not None

    # El archivo fisico se conserva -- la descarga sigue funcionando.
    download_response = auth_client.get(
        f"/api/v1/declarations/{declaration.id}/documents/{item_id}/files/{first['id']}/download"
    )
    assert download_response.status_code == 200

    audit_rows = db_session.execute(
        select(AuditLog).where(AuditLog.action == "DOCUMENT_ITEM_FILE_DELETE")
    ).scalars().all()
    assert len(audit_rows) == 1


def test_delete_requires_non_blank_reason(
    auth_client, db_session, tenant, admin_user, obligation_catalog
) -> None:
    declaration, item_id = _setup_item(auth_client, db_session, tenant, admin_user, obligation_catalog)
    first = _upload(auth_client, declaration.id, item_id).json()

    response = auth_client.post(
        f"/api/v1/declarations/{declaration.id}/documents/{item_id}/files/{first['id']}/delete",
        json={"reason": ""},
    )
    assert response.status_code == 422


def test_delete_already_deleted_returns_409(
    auth_client, db_session, tenant, admin_user, obligation_catalog
) -> None:
    declaration, item_id = _setup_item(auth_client, db_session, tenant, admin_user, obligation_catalog)
    first = _upload(auth_client, declaration.id, item_id).json()

    auth_client.post(
        f"/api/v1/declarations/{declaration.id}/documents/{item_id}/files/{first['id']}/delete",
        json={"reason": "primera eliminacion"},
    )
    second_attempt = auth_client.post(
        f"/api/v1/declarations/{declaration.id}/documents/{item_id}/files/{first['id']}/delete",
        json={"reason": "segundo intento"},
    )
    assert second_attempt.status_code == 409


def test_multiple_independent_chains_can_coexist_active(
    auth_client, db_session, tenant, admin_user, obligation_catalog
) -> None:
    declaration, item_id = _setup_item(auth_client, db_session, tenant, admin_user, obligation_catalog)
    first = _upload(auth_client, declaration.id, item_id, filename="a.pdf").json()
    second = _upload(auth_client, declaration.id, item_id, filename="b.pdf").json()

    assert first["logical_file_id"] != second["logical_file_id"]
    assert first["status"] == "ACTIVE"
    assert second["status"] == "ACTIVE"

    listing = auth_client.get(
        f"/api/v1/declarations/{declaration.id}/documents/{item_id}/files"
    ).json()
    assert len(listing) == 2


def test_asistente_can_upload_but_not_replace_or_delete(
    auth_client, asistente_client, db_session, tenant, admin_user, obligation_catalog
) -> None:
    declaration, item_id = _setup_item(auth_client, db_session, tenant, admin_user, obligation_catalog)

    upload_response = _upload(asistente_client, declaration.id, item_id)
    assert upload_response.status_code == 201
    file_id = upload_response.json()["id"]

    files = {"file": ("v2.pdf", io.BytesIO(_PDF_BYTES), "application/pdf")}
    replace_response = asistente_client.post(
        f"/api/v1/declarations/{declaration.id}/documents/{item_id}/files/{file_id}/replace",
        files=files,
        data={"reason": "intento de asistente"},
    )
    assert replace_response.status_code == 403

    delete_response = asistente_client.post(
        f"/api/v1/declarations/{declaration.id}/documents/{item_id}/files/{file_id}/delete",
        json={"reason": "intento de asistente"},
    )
    assert delete_response.status_code == 403

    # Descargar si esta permitido para los 3 roles (document:read).
    download_response = asistente_client.get(
        f"/api/v1/declarations/{declaration.id}/documents/{item_id}/files/{file_id}/download"
    )
    assert download_response.status_code == 200


def test_contador_can_upload_replace_and_delete(
    auth_client, contador_client, db_session, tenant, admin_user, obligation_catalog
) -> None:
    declaration, item_id = _setup_item(auth_client, db_session, tenant, admin_user, obligation_catalog)

    upload_response = _upload(contador_client, declaration.id, item_id)
    assert upload_response.status_code == 201
    file_id = upload_response.json()["id"]

    files = {"file": ("v2.pdf", io.BytesIO(_PDF_BYTES), "application/pdf")}
    replace_response = contador_client.post(
        f"/api/v1/declarations/{declaration.id}/documents/{item_id}/files/{file_id}/replace",
        files=files,
        data={"reason": "reemplazo de contador"},
    )
    assert replace_response.status_code == 201
    new_file_id = replace_response.json()["id"]

    delete_response = contador_client.post(
        f"/api/v1/declarations/{declaration.id}/documents/{item_id}/files/{new_file_id}/delete",
        json={"reason": "eliminacion de contador"},
    )
    assert delete_response.status_code == 200


def test_file_metadata_legacy_still_rejects_real_path(
    auth_client, db_session, tenant, admin_user, obligation_catalog
) -> None:
    """Reconfirma el contrato de Bloque 3.2 (test_change_item_status_rejects_
    invalid_file_metadata) tras introducir declaration_document_files -- el
    campo legado file_metadata NUNCA debe aceptar una ruta/URL real, incluso
    despues de que exista almacenamiento real para archivos (ver Seccion 6
    del plan de Bloque 5)."""
    declaration, item_id = _setup_item(auth_client, db_session, tenant, admin_user, obligation_catalog)

    response = auth_client.patch(
        f"/api/v1/declarations/{declaration.id}/documents/{item_id}",
        json={"target": "RECEIVED", "file_metadata": {"path": "/etc/passwd"}},
    )
    assert response.status_code == 422


def test_cross_tenant_download_returns_404(
    auth_client, db_session, tenant, admin_user, obligation_catalog
) -> None:
    declaration, item_id = _setup_item(auth_client, db_session, tenant, admin_user, obligation_catalog)
    file_id = _upload(auth_client, declaration.id, item_id).json()["id"]

    _, other_user = make_other_tenant_with_user(db_session)
    db_session.commit()
    other_client = _login_new_client(other_user)

    response = other_client.get(
        f"/api/v1/declarations/{declaration.id}/documents/{item_id}/files/{file_id}/download"
    )
    assert response.status_code == 404

    list_response = other_client.get(
        f"/api/v1/declarations/{declaration.id}/documents/{item_id}/files"
    )
    # 404 en el paso previo (declaracion/item no encontrados para ese tenant).
    assert list_response.status_code == 404


def test_upload_does_not_change_item_status(
    auth_client, db_session, tenant, admin_user, obligation_catalog
) -> None:
    declaration, item_id = _setup_item(auth_client, db_session, tenant, admin_user, obligation_catalog)

    before = auth_client.get(f"/api/v1/declarations/{declaration.id}/documents").json()[0]
    assert before["status"] == "NOT_REQUESTED"

    _upload(auth_client, declaration.id, item_id)

    after = auth_client.get(f"/api/v1/declarations/{declaration.id}/documents").json()[0]
    assert after["status"] == "NOT_REQUESTED"
