"""API HTTP de comunicaciones asistidas por WhatsApp (Fase 3, Bloque 3.2, ver
ADR-0067): catalogo de plantillas, creacion de borradores, validacion de
fuentes, ownership del asistente, maquina de estados con concurrencia
optimista, codificacion de la URL wa.me, y auditoria sin datos sensibles."""

import urllib.parse

from sqlalchemy import select

from app.core.security import hash_password
from app.models.audit_log import AuditLog
from app.models.user import User
from tests.conftest import _login_new_client
from tests.integration._declaration_helpers import make_client, make_other_tenant_with_user
from tests.integration._operational_task_helpers import make_declaration_setup, make_document_item

_TEMPLATES_URL = "/api/v1/communications/templates"
_DRAFTS_URL = "/api/v1/communications/drafts"


def _create_task(client_api, **overrides) -> dict:
    from datetime import date, timedelta

    payload = {
        "title": "Revisar documentacion pendiente",
        "task_type": "GENERAL",
        "priority": "NORMAL",
        "due": {"due_date": (date.today() + timedelta(days=5)).isoformat()},
    }
    payload.update(overrides)
    response = client_api.post("/api/v1/tasks", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _draft_payload(client_id: str, **overrides) -> dict:
    payload = {"client_id": client_id, "body": "Hola, este es un mensaje de prueba."}
    payload.update(overrides)
    return payload


# --- catalogo de plantillas ---


def test_templates_requires_authentication(client) -> None:
    response = client.get(_TEMPLATES_URL)
    assert response.status_code == 401


def test_list_templates_returns_5_items(auth_client) -> None:
    response = auth_client.get(_TEMPLATES_URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == 5
    codes = {item["code"] for item in body["items"]}
    assert codes == {
        "GENERAL_MESSAGE",
        "OBLIGATION_DUE_REMINDER",
        "DOCUMENT_REQUEST",
        "PAYMENT_PENDING_REMINDER",
        "TASK_FOLLOW_UP",
    }


def test_preview_template_never_persists_a_draft(auth_client, db_session, tenant) -> None:
    client_row = make_client(db_session, tenant.id)
    client_row.whatsapp_number = "987654321"
    db_session.flush()

    response = auth_client.post(
        f"{_TEMPLATES_URL}/GENERAL_MESSAGE/preview",
        json={
            "client_id": str(client_row.id),
            "variables": {"client_business_name": client_row.business_name, "professional_name": "Luis"},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert client_row.business_name in body["body_rendered"]
    assert body["recipient_phone_e164"] == "+51987654321"
    assert body["warnings"] == []

    listed = auth_client.get(_DRAFTS_URL)
    assert listed.json()["total"] == 0


def test_preview_template_reports_warning_when_client_has_no_whatsapp_number(
    auth_client, db_session, tenant
) -> None:
    client_row = make_client(db_session, tenant.id)
    db_session.flush()
    response = auth_client.post(
        f"{_TEMPLATES_URL}/GENERAL_MESSAGE/preview",
        json={"client_id": str(client_row.id), "variables": {}},
    )
    assert response.status_code == 200, response.text
    assert "WHATSAPP_NUMBER_REQUIRED" in response.json()["warnings"]


def test_preview_unknown_template_returns_404(auth_client, db_session, tenant) -> None:
    client_row = make_client(db_session, tenant.id)
    db_session.flush()
    response = auth_client.post(
        f"{_TEMPLATES_URL}/NO_EXISTE/preview", json={"client_id": str(client_row.id), "variables": {}}
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "TEMPLATE_NOT_FOUND"


def test_preview_with_unknown_variable_returns_422(auth_client, db_session, tenant) -> None:
    client_row = make_client(db_session, tenant.id)
    db_session.flush()
    response = auth_client.post(
        f"{_TEMPLATES_URL}/GENERAL_MESSAGE/preview",
        json={"client_id": str(client_row.id), "variables": {"sol_password": "x"}},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "UNKNOWN_TEMPLATE_VARIABLE"


# --- creacion de borradores ---


def test_create_draft_success(auth_client, db_session, tenant) -> None:
    client_row = make_client(db_session, tenant.id)
    client_row.whatsapp_number = "987654321"
    db_session.flush()

    response = auth_client.post(_DRAFTS_URL, json=_draft_payload(str(client_row.id)))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "DRAFT"
    assert body["version"] == 1
    assert body["client_id"] == str(client_row.id)
    assert "recipient_phone_e164" not in body


def test_create_draft_without_whatsapp_number_returns_422(auth_client, db_session, tenant) -> None:
    client_row = make_client(db_session, tenant.id)
    db_session.flush()
    response = auth_client.post(_DRAFTS_URL, json=_draft_payload(str(client_row.id)))
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "WHATSAPP_NUMBER_REQUIRED"


def test_create_draft_with_invalid_phone_override_returns_422(auth_client, db_session, tenant) -> None:
    client_row = make_client(db_session, tenant.id)
    db_session.flush()
    response = auth_client.post(
        _DRAFTS_URL, json=_draft_payload(str(client_row.id), recipient_phone="123")
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_WHATSAPP_NUMBER"


def test_create_draft_with_client_of_another_tenant_returns_404(auth_client, db_session) -> None:
    other_tenant, _other_user = make_other_tenant_with_user(db_session)
    other_client = make_client(db_session, other_tenant.id)
    db_session.flush()
    response = auth_client.post(_DRAFTS_URL, json=_draft_payload(str(other_client.id)))
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "SOURCE_NOT_FOUND"


def test_create_draft_with_task_of_different_client_returns_422_source_mismatch(
    auth_client, db_session, tenant
) -> None:
    client_a = make_client(db_session, tenant.id, name="Cliente A")
    client_b = make_client(db_session, tenant.id, name="Cliente B")
    client_a.whatsapp_number = "987654321"
    db_session.flush()

    task = _create_task(auth_client, client_id=str(client_b.id))
    response = auth_client.post(
        _DRAFTS_URL, json=_draft_payload(str(client_a.id), related_task_id=task["id"])
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "SOURCE_CLIENT_MISMATCH"


def test_asistente_cannot_use_task_assigned_to_another_user_as_source(
    auth_client, asistente_client, asistente_user, admin_user, db_session, tenant
) -> None:
    client_row = make_client(db_session, tenant.id)
    client_row.whatsapp_number = "987654321"
    db_session.flush()
    task = _create_task(auth_client, client_id=str(client_row.id), assigned_to=str(admin_user.id))

    response = asistente_client.post(
        _DRAFTS_URL, json=_draft_payload(str(client_row.id), related_task_id=task["id"])
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "SOURCE_NOT_ACCESSIBLE"


def test_create_draft_with_document_of_different_client_returns_422(
    auth_client, admin_user, db_session, tenant, obligation_catalog
) -> None:
    from app.core import clock
    from app.models.declaration_document_file import DeclarationDocumentFile

    setup = make_declaration_setup(db_session, tenant, admin_user, obligation_catalog)
    item = make_document_item(db_session, tenant, setup)
    doc_file = DeclarationDocumentFile(
        tenant_id=tenant.id,
        client_id=setup["client"].id,
        declaration_id=setup["declaration"].id,
        declaration_document_item_id=item.id,
        logical_file_id=item.id,
        version=1,
        original_filename="a.pdf",
        storage_key=f"k/{item.id}",
        mime_type="application/pdf",
        extension=".pdf",
        size_bytes=10,
        sha256_hash="0" * 64,
        status="ACTIVE",
        uploaded_by=admin_user.id,
        uploaded_at=clock.utcnow(),
    )
    db_session.add(doc_file)
    db_session.flush()

    other_client = make_client(db_session, tenant.id, name="Otro cliente")
    other_client.whatsapp_number = "987654321"
    db_session.flush()

    response = auth_client.post(
        _DRAFTS_URL,
        json=_draft_payload(str(other_client.id), related_document_file_id=str(doc_file.id)),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "SOURCE_CLIENT_MISMATCH"


# --- listado y ownership ---


def test_asistente_sees_only_own_drafts(
    asistente_client, asistente_user, admin_user, auth_client, db_session, tenant
) -> None:
    client_row = make_client(db_session, tenant.id)
    client_row.whatsapp_number = "987654321"
    db_session.flush()

    auth_client.post(_DRAFTS_URL, json=_draft_payload(str(client_row.id)))
    asistente_client.post(_DRAFTS_URL, json=_draft_payload(str(client_row.id)))

    admin_view = auth_client.get(_DRAFTS_URL)
    assert admin_view.json()["total"] == 2

    asistente_view = asistente_client.get(_DRAFTS_URL)
    assert asistente_view.json()["total"] == 1
    assert asistente_view.json()["items"][0]["created_by"] == str(asistente_user.id)


def test_get_draft_not_found_returns_404(auth_client) -> None:
    import uuid

    response = auth_client.get(f"{_DRAFTS_URL}/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "DRAFT_NOT_FOUND"


def test_asistente_cannot_read_another_users_draft(
    auth_client, asistente_client, db_session, tenant
) -> None:
    client_row = make_client(db_session, tenant.id)
    client_row.whatsapp_number = "987654321"
    db_session.flush()
    created = auth_client.post(_DRAFTS_URL, json=_draft_payload(str(client_row.id))).json()

    response = asistente_client.get(f"{_DRAFTS_URL}/{created['id']}")
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "DRAFT_NOT_OWNED"


def test_asistente_cannot_read_another_asistentes_draft(
    asistente_client, asistente_user, db_session, tenant
) -> None:
    """Ownership del asistente entre DOS asistentes reales del mismo tenant
    -- distinto del caso ya cubierto (asistente vs admin): confirma que la
    restriccion es "solo lo propio", no "cualquier cosa que no sea de un
    admin/contador"."""
    other_asistente = User(
        tenant_id=tenant.id,
        email="otro-asistente@luisiacontable-tests.dev",
        full_name="Otro asistente de prueba",
        password_hash=hash_password("TestPassword123!"),
        role="asistente",
        is_active=True,
    )
    db_session.add(other_asistente)
    db_session.flush()
    db_session.commit()
    other_asistente_client = _login_new_client(other_asistente)

    client_row = make_client(db_session, tenant.id)
    client_row.whatsapp_number = "987654321"
    db_session.flush()
    db_session.commit()

    created = asistente_client.post(_DRAFTS_URL, json=_draft_payload(str(client_row.id))).json()

    response = other_asistente_client.get(f"{_DRAFTS_URL}/{created['id']}")
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "DRAFT_NOT_OWNED"

    listed = other_asistente_client.get(_DRAFTS_URL).json()
    assert listed["total"] == 0, "el segundo asistente no debe ver el borrador del primero en su lista"


def test_contador_sees_all_drafts_in_tenant(
    auth_client, asistente_client, contador_client, db_session, tenant
) -> None:
    client_row = make_client(db_session, tenant.id)
    client_row.whatsapp_number = "987654321"
    db_session.flush()

    auth_client.post(_DRAFTS_URL, json=_draft_payload(str(client_row.id)))
    asistente_client.post(_DRAFTS_URL, json=_draft_payload(str(client_row.id)))

    contador_view = contador_client.get(_DRAFTS_URL)
    assert contador_view.json()["total"] == 2


def test_get_draft_from_another_tenant_returns_404(auth_client, db_session) -> None:
    other_tenant, other_user = make_other_tenant_with_user(db_session)
    other_client = make_client(db_session, other_tenant.id)
    other_client.whatsapp_number = "987654321"
    db_session.flush()

    other_tenant_client = _login_new_client(other_user)
    other_draft = other_tenant_client.post(
        _DRAFTS_URL, json=_draft_payload(str(other_client.id))
    ).json()

    response = auth_client.get(f"{_DRAFTS_URL}/{other_draft['id']}")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "DRAFT_NOT_FOUND"


def test_list_drafts_never_leaks_another_tenant(auth_client, db_session) -> None:
    other_tenant, other_user = make_other_tenant_with_user(db_session)
    other_client = make_client(db_session, other_tenant.id)
    other_client.whatsapp_number = "987654321"
    db_session.flush()

    other_tenant_client = _login_new_client(other_user)
    other_tenant_client.post(_DRAFTS_URL, json=_draft_payload(str(other_client.id)))

    own_view = auth_client.get(_DRAFTS_URL)
    assert own_view.json()["total"] == 0, "un tenant nunca debe ver los borradores de otro tenant"


def test_get_and_list_responses_never_expose_wa_url_or_e164(
    auth_client, db_session, tenant
) -> None:
    client_row = make_client(db_session, tenant.id)
    client_row.whatsapp_number = "987654321"
    db_session.flush()
    created = auth_client.post(_DRAFTS_URL, json=_draft_payload(str(client_row.id))).json()

    for body in (created, auth_client.get(f"{_DRAFTS_URL}/{created['id']}").json()):
        assert "wa_url" not in body
        assert "recipient_phone_e164" not in body

    listed = auth_client.get(_DRAFTS_URL).json()
    for item in listed["items"]:
        assert "wa_url" not in item
        assert "recipient_phone_e164" not in item


# --- maquina de estados ---


def _make_draft(client_api, client_id: str) -> dict:
    return client_api.post(_DRAFTS_URL, json=_draft_payload(client_id)).json()


def test_mark_open_requested_returns_encoded_wa_url_and_expected_message(
    auth_client, db_session, tenant
) -> None:
    client_row = make_client(db_session, tenant.id)
    client_row.whatsapp_number = "987654321"
    db_session.flush()
    draft = _make_draft(auth_client, str(client_row.id))

    response = auth_client.post(
        f"{_DRAFTS_URL}/{draft['id']}/mark-open-requested", json={"expected_version": 1}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["message"] == "Se solicitó abrir WhatsApp"
    assert body["draft"]["status"] == "OPEN_REQUESTED"
    assert body["draft"]["version"] == 2
    assert body["wa_url"].startswith("https://wa.me/51987654321?text=")
    expected_text = urllib.parse.quote("Hola, este es un mensaje de prueba.", safe="")
    assert body["wa_url"] == f"https://wa.me/51987654321?text={expected_text}"


def test_mark_open_requested_encodes_accents_ni_newlines_and_special_chars(
    auth_client, db_session, tenant
) -> None:
    client_row = make_client(db_session, tenant.id)
    client_row.whatsapp_number = "987654321"
    db_session.flush()
    tricky_body = "Estimado(a) señor Muñoz,\n¿Podría enviar el documento? Gracias & saludos."
    draft = auth_client.post(_DRAFTS_URL, json=_draft_payload(str(client_row.id), body=tricky_body)).json()

    response = auth_client.post(
        f"{_DRAFTS_URL}/{draft['id']}/mark-open-requested", json={"expected_version": 1}
    )
    assert response.status_code == 200, response.text
    wa_url = response.json()["wa_url"]
    query = wa_url.split("?text=", 1)[1]
    assert urllib.parse.unquote(query) == tricky_body
    # Nunca literal en la URL: espacios, "?", "&", "\n", "ñ" deben quedar codificados.
    for forbidden in (" ", "?", "&", "\n", "ñ", "Ñ"):
        assert forbidden not in query


def test_mark_open_requested_is_idempotent_on_repeat(auth_client, db_session, tenant) -> None:
    client_row = make_client(db_session, tenant.id)
    client_row.whatsapp_number = "987654321"
    db_session.flush()
    draft = _make_draft(auth_client, str(client_row.id))

    first = auth_client.post(
        f"{_DRAFTS_URL}/{draft['id']}/mark-open-requested", json={"expected_version": 1}
    ).json()
    assert first["draft"]["version"] == 2

    second = auth_client.post(
        f"{_DRAFTS_URL}/{draft['id']}/mark-open-requested", json={"expected_version": 2}
    ).json()
    assert second["draft"]["version"] == 2
    assert second["draft"]["open_requested_at"] == first["draft"]["open_requested_at"]
    assert second["wa_url"] == first["wa_url"]


def test_mark_open_requested_with_stale_version_returns_409(auth_client, db_session, tenant) -> None:
    client_row = make_client(db_session, tenant.id)
    client_row.whatsapp_number = "987654321"
    db_session.flush()
    draft = _make_draft(auth_client, str(client_row.id))

    response = auth_client.post(
        f"{_DRAFTS_URL}/{draft['id']}/mark-open-requested", json={"expected_version": 99}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "VERSION_CONFLICT"


def test_mark_sent_requires_open_requested_first(auth_client, db_session, tenant) -> None:
    client_row = make_client(db_session, tenant.id)
    client_row.whatsapp_number = "987654321"
    db_session.flush()
    draft = _make_draft(auth_client, str(client_row.id))

    response = auth_client.post(f"{_DRAFTS_URL}/{draft['id']}/mark-sent", json={"expected_version": 1})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "INVALID_COMMUNICATION_TRANSITION"


def test_full_lifecycle_open_requested_then_marked_sent(auth_client, db_session, tenant) -> None:
    client_row = make_client(db_session, tenant.id)
    client_row.whatsapp_number = "987654321"
    db_session.flush()
    draft = _make_draft(auth_client, str(client_row.id))

    auth_client.post(f"{_DRAFTS_URL}/{draft['id']}/mark-open-requested", json={"expected_version": 1})
    response = auth_client.post(f"{_DRAFTS_URL}/{draft['id']}/mark-sent", json={"expected_version": 2})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["message"] == "Marcado como enviado por ti"
    assert body["draft"]["status"] == "MARKED_SENT"
    assert body["draft"]["version"] == 3


def test_mark_sent_twice_returns_409(auth_client, db_session, tenant) -> None:
    client_row = make_client(db_session, tenant.id)
    client_row.whatsapp_number = "987654321"
    db_session.flush()
    draft = _make_draft(auth_client, str(client_row.id))
    auth_client.post(f"{_DRAFTS_URL}/{draft['id']}/mark-open-requested", json={"expected_version": 1})
    auth_client.post(f"{_DRAFTS_URL}/{draft['id']}/mark-sent", json={"expected_version": 2})

    response = auth_client.post(f"{_DRAFTS_URL}/{draft['id']}/mark-sent", json={"expected_version": 3})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "INVALID_COMMUNICATION_TRANSITION"


def test_discard_from_draft_succeeds(auth_client, db_session, tenant) -> None:
    client_row = make_client(db_session, tenant.id)
    client_row.whatsapp_number = "987654321"
    db_session.flush()
    draft = _make_draft(auth_client, str(client_row.id))

    response = auth_client.post(f"{_DRAFTS_URL}/{draft['id']}/discard", json={"expected_version": 1})
    assert response.status_code == 200, response.text
    assert response.json()["draft"]["status"] == "DISCARDED"


def test_discard_after_marked_sent_returns_409(auth_client, db_session, tenant) -> None:
    client_row = make_client(db_session, tenant.id)
    client_row.whatsapp_number = "987654321"
    db_session.flush()
    draft = _make_draft(auth_client, str(client_row.id))
    auth_client.post(f"{_DRAFTS_URL}/{draft['id']}/mark-open-requested", json={"expected_version": 1})
    auth_client.post(f"{_DRAFTS_URL}/{draft['id']}/mark-sent", json={"expected_version": 2})

    response = auth_client.post(f"{_DRAFTS_URL}/{draft['id']}/discard", json={"expected_version": 3})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "INVALID_COMMUNICATION_TRANSITION"


# --- auditoria ---


def test_audit_trail_never_contains_full_body_or_phone(auth_client, admin_user, db_session, tenant) -> None:
    client_row = make_client(db_session, tenant.id)
    client_row.whatsapp_number = "987654321"
    db_session.flush()
    secret_body = "Mensaje confidencial con datos del cliente 987654321"
    draft = auth_client.post(_DRAFTS_URL, json=_draft_payload(str(client_row.id), body=secret_body)).json()
    auth_client.post(f"{_DRAFTS_URL}/{draft['id']}/mark-open-requested", json={"expected_version": 1})
    auth_client.post(f"{_DRAFTS_URL}/{draft['id']}/mark-sent", json={"expected_version": 2})
    auth_client.post(f"{_DRAFTS_URL}/{draft['id']}/discard", json={"expected_version": 3})

    logs = (
        db_session.execute(select(AuditLog).where(AuditLog.entity_type == "communication_draft"))
        .scalars()
        .all()
    )
    assert len(logs) >= 3
    for log in logs:
        dumped = str(log.log_metadata)
        assert secret_body not in dumped
        assert "987654321" not in dumped
        assert "wa.me" not in dumped
