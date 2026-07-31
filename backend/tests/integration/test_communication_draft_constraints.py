"""Integridad a nivel de base de datos de `communication_drafts` (Fase 3,
Bloque 3.2, ver ADR-0067): intentos de insercion SQL/ORM directa que deben
ser rechazados por las 6 CHECK y por las FK compuestas -- incluso pasando
por alto la capa de servicio, mismo patron que
`test_operational_task_constraints.py`."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.core import clock
from app.models.communication_draft import CommunicationDraft
from app.models.declaration_document_file import DeclarationDocumentFile
from app.models.operational_task import OperationalTask
from tests.integration._declaration_helpers import make_client, make_other_tenant_with_user
from tests.integration._operational_task_helpers import make_declaration_setup, make_document_item

_NOW = datetime(2026, 2, 12, 12, 0, 0, tzinfo=UTC)


def _attempt_and_expect_rejection(db_session, obj) -> None:
    savepoint = db_session.begin_nested()
    with pytest.raises(IntegrityError):
        db_session.add(obj)
        db_session.flush()
    savepoint.rollback()


def _draft(tenant, client, created_by_user, **overrides) -> CommunicationDraft:
    kwargs = dict(
        tenant_id=tenant.id,
        client_id=client.id,
        channel="WHATSAPP",
        body="Mensaje de prueba",
        recipient_phone_display="987654321",
        recipient_phone_e164="+51987654321",
        status="DRAFT",
        created_by=created_by_user.id,
        version=1,
        created_at=_NOW,
        updated_at=_NOW,
    )
    kwargs.update(overrides)
    return CommunicationDraft(**kwargs)


@pytest.fixture()
def base_client(db_session, tenant):
    return make_client(db_session, tenant.id)


def test_valid_draft_succeeds(db_session, tenant, admin_user, base_client) -> None:
    draft = _draft(tenant, base_client, admin_user)
    db_session.add(draft)
    db_session.flush()
    assert draft.id is not None
    assert draft.status == "DRAFT"


def test_invalid_channel_is_rejected(db_session, tenant, admin_user, base_client) -> None:
    draft = _draft(tenant, base_client, admin_user, channel="TELEGRAM")
    _attempt_and_expect_rejection(db_session, draft)


def test_invalid_status_is_rejected(db_session, tenant, admin_user, base_client) -> None:
    draft = _draft(tenant, base_client, admin_user, status="SENT")
    _attempt_and_expect_rejection(db_session, draft)


def test_non_positive_version_is_rejected(db_session, tenant, admin_user, base_client) -> None:
    draft = _draft(tenant, base_client, admin_user, version=0)
    _attempt_and_expect_rejection(db_session, draft)


def test_empty_body_is_rejected(db_session, tenant, admin_user, base_client) -> None:
    draft = _draft(tenant, base_client, admin_user, body="   ")
    _attempt_and_expect_rejection(db_session, draft)


def test_body_over_4000_chars_is_rejected(db_session, tenant, admin_user, base_client) -> None:
    draft = _draft(tenant, base_client, admin_user, body="x" * 4001)
    _attempt_and_expect_rejection(db_session, draft)


def test_invalid_e164_format_is_rejected(db_session, tenant, admin_user, base_client) -> None:
    draft = _draft(tenant, base_client, admin_user, recipient_phone_e164="987654321")
    _attempt_and_expect_rejection(db_session, draft)


def test_status_coherence_rejects_draft_with_open_requested_at_set(
    db_session, tenant, admin_user, base_client
) -> None:
    draft = _draft(tenant, base_client, admin_user, status="DRAFT", open_requested_at=_NOW)
    _attempt_and_expect_rejection(db_session, draft)


def test_status_coherence_rejects_open_requested_without_timestamp(
    db_session, tenant, admin_user, base_client
) -> None:
    draft = _draft(tenant, base_client, admin_user, status="OPEN_REQUESTED", open_requested_at=None)
    _attempt_and_expect_rejection(db_session, draft)


def test_status_coherence_rejects_marked_sent_without_marked_sent_by(
    db_session, tenant, admin_user, base_client
) -> None:
    draft = _draft(
        tenant,
        base_client,
        admin_user,
        status="MARKED_SENT",
        open_requested_at=_NOW,
        marked_sent_at=_NOW,
        marked_sent_by=None,
    )
    _attempt_and_expect_rejection(db_session, draft)


def test_status_coherence_rejects_discarded_without_discarded_by(
    db_session, tenant, admin_user, base_client
) -> None:
    draft = _draft(tenant, base_client, admin_user, status="DISCARDED", discarded_at=_NOW, discarded_by=None)
    _attempt_and_expect_rejection(db_session, draft)


def test_discarded_allows_open_requested_at_null_or_not_null(
    db_session, tenant, admin_user, base_client
) -> None:
    """DISCARDED es el unico estado donde open_requested_at puede ser NULL o
    NOT NULL -- un borrador puede descartarse antes o despues de solicitar
    abrir WhatsApp (Seccion 6 del pedido)."""
    draft_a = _draft(
        tenant, base_client, admin_user, status="DISCARDED", discarded_at=_NOW, discarded_by=admin_user.id
    )
    db_session.add(draft_a)
    db_session.flush()

    draft_b = _draft(
        tenant,
        base_client,
        admin_user,
        status="DISCARDED",
        open_requested_at=_NOW,
        discarded_at=_NOW,
        discarded_by=admin_user.id,
    )
    db_session.add(draft_b)
    db_session.flush()
    assert draft_a.id is not None and draft_b.id is not None


def test_client_of_another_tenant_is_rejected(db_session, tenant, admin_user) -> None:
    other_tenant, _other_user = make_other_tenant_with_user(db_session)
    other_client = make_client(db_session, other_tenant.id)
    draft = _draft(tenant, other_client, admin_user)
    _attempt_and_expect_rejection(db_session, draft)


def test_related_task_of_another_tenant_is_rejected(db_session, tenant, admin_user, base_client) -> None:
    other_tenant, other_user = make_other_tenant_with_user(db_session)
    other_task = OperationalTask(
        tenant_id=other_tenant.id,
        title="Tarea de otro tenant",
        task_type="GENERAL",
        priority="NORMAL",
        status="PENDING",
        created_by=other_user.id,
        due_at=_NOW,
        is_system_generated=False,
    )
    db_session.add(other_task)
    db_session.flush()

    draft = _draft(tenant, base_client, admin_user, related_task_id=other_task.id)
    _attempt_and_expect_rejection(db_session, draft)


def test_related_document_of_another_client_is_rejected(
    db_session, tenant, admin_user, obligation_catalog
) -> None:
    setup_a = make_declaration_setup(db_session, tenant, admin_user, obligation_catalog)
    item_a = make_document_item(db_session, tenant, setup_a)
    file_a = DeclarationDocumentFile(
        tenant_id=tenant.id,
        client_id=setup_a["client"].id,
        declaration_id=setup_a["declaration"].id,
        declaration_document_item_id=item_a.id,
        logical_file_id=item_a.id,
        version=1,
        original_filename="a.pdf",
        storage_key=f"k/{item_a.id}",
        mime_type="application/pdf",
        extension=".pdf",
        size_bytes=10,
        sha256_hash="0" * 64,
        status="ACTIVE",
        replaces_file_id=None,
        uploaded_by=admin_user.id,
        uploaded_at=clock.utcnow(),
    )
    db_session.add(file_a)
    db_session.flush()

    # Otro cliente (mismo tenant) -- el borrador declara client_id distinto
    # al que realmente posee el archivo, la FK compuesta (id, tenant_id,
    # client_id) debe rechazarlo.
    other_client = make_client(db_session, tenant.id, name="Otro cliente")
    draft = _draft(tenant, other_client, admin_user, related_document_file_id=file_a.id)
    _attempt_and_expect_rejection(db_session, draft)


def test_related_document_of_correct_client_succeeds(
    db_session, tenant, admin_user, obligation_catalog
) -> None:
    setup = make_declaration_setup(db_session, tenant, admin_user, obligation_catalog)
    item = make_document_item(db_session, tenant, setup)
    file = DeclarationDocumentFile(
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
        replaces_file_id=None,
        uploaded_by=admin_user.id,
        uploaded_at=clock.utcnow(),
    )
    db_session.add(file)
    db_session.flush()

    draft = _draft(tenant, setup["client"], admin_user, related_document_file_id=file.id)
    db_session.add(draft)
    db_session.flush()
    assert draft.related_document_file_id == file.id
