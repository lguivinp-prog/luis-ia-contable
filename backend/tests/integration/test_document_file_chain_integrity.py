"""Integridad estructural de la cadena de versiones de
`declaration_document_files` (Bloque 5, Seccion 4/6 del plan): una
insercion SQL/ORM directa no debe poder violar el CHECK version/
replaces_file_id, ni enlazar `replaces_file_id` con el archivo de otro
cliente/item/cadena, incluso dentro del mismo tenant -- mismo patron que
`test_declarations_multitenant_integrity.py` (ADR-0024/0025/0036)."""

import uuid
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.core import clock
from app.models.declaration_document import (
    ClientDocumentRequirement,
    DeclarationDocumentItem,
    DocumentRequirementCatalog,
)
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


def _catalog_entry(db_session, tenant_id):
    entry = DocumentRequirementCatalog(
        tenant_id=tenant_id, is_system=False, code=f"CAT-{uuid.uuid4().hex[:6]}", name="Catalogo test"
    )
    db_session.add(entry)
    db_session.flush()
    return entry


@pytest.fixture()
def two_items(db_session, tenant, admin_user, obligation_catalog):
    """Dos items documentales distintos (de dos clientes distintos) del
    mismo tenant, cada uno con una version ACTIVE ya cargada -- base para
    los intentos de enlace cruzado."""
    obligation = obligation_catalog["621"]
    period = make_monthly_period(db_session, tenant.id, fiscal_year=2026, month=1)

    def _build_item(name: str):
        client_row = make_client(db_session, tenant.id, name=name)
        co = make_client_obligation(db_session, tenant.id, client_row.id, obligation.id)
        source = make_source(db_session, tenant.id, obligation.id, name=f"Cronograma {name}")
        version = make_version(db_session, tenant.id, source.id, fiscal_year=2026)
        make_entry(db_session, tenant.id, version.id, tax_period="2026-01", digits=list(range(10)))
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
        requirement = ClientDocumentRequirement(
            tenant_id=tenant.id,
            client_id=client_row.id,
            requirement_catalog_id=_catalog_entry(db_session, tenant.id).id,
            client_obligation_id=None,
            periodicity="MONTHLY",
            valid_from=date(2026, 1, 1),
        )
        db_session.add(requirement)
        db_session.flush()
        item = DeclarationDocumentItem(
            tenant_id=tenant.id,
            client_id=client_row.id,
            declaration_id=declaration.id,
            client_document_requirement_id=requirement.id,
            status="NOT_REQUESTED",
        )
        db_session.add(item)
        db_session.flush()
        return client_row, declaration, item

    client_a, declaration_a, item_a = _build_item("Cliente A")
    client_b, declaration_b, item_b = _build_item("Cliente B")
    db_session.commit()
    return {
        "admin_user_id": admin_user.id,
        "item_a": item_a,
        "declaration_a": declaration_a,
        "client_a": client_a,
        "item_b": item_b,
        "declaration_b": declaration_b,
        "client_b": client_b,
    }


def _make_active_file(
    db_session, tenant_id, client_id, declaration_id, item_id, uploaded_by, *, logical_file_id=None
):
    logical_file_id = logical_file_id or uuid.uuid4()
    file = DeclarationDocumentFile(
        tenant_id=tenant_id,
        client_id=client_id,
        declaration_id=declaration_id,
        declaration_document_item_id=item_id,
        logical_file_id=logical_file_id,
        version=1,
        original_filename="a.pdf",
        storage_key=f"k/{uuid.uuid4().hex}",
        mime_type="application/pdf",
        extension=".pdf",
        size_bytes=10,
        sha256_hash="0" * 64,
        status="ACTIVE",
        replaces_file_id=None,
        uploaded_by=uploaded_by,
        uploaded_at=clock.utcnow(),
    )
    db_session.add(file)
    db_session.flush()
    return file


def _attempt_and_expect_integrity_error(db_session, file: DeclarationDocumentFile) -> None:
    db_session.add(file)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_version_one_with_replaces_file_id_is_rejected(db_session, two_items) -> None:
    ctx = two_items
    existing = _make_active_file(
        db_session,
        ctx["item_a"].tenant_id,
        ctx["client_a"].id,
        ctx["declaration_a"].id,
        ctx["item_a"].id,
        ctx["admin_user_id"],
    )
    bad = DeclarationDocumentFile(
        tenant_id=ctx["item_a"].tenant_id,
        client_id=ctx["client_a"].id,
        declaration_id=ctx["declaration_a"].id,
        declaration_document_item_id=ctx["item_a"].id,
        logical_file_id=existing.logical_file_id,
        version=1,  # invalido: version=1 con replaces_file_id no NULL
        original_filename="bad.pdf",
        storage_key=f"k/{uuid.uuid4().hex}",
        mime_type="application/pdf",
        extension=".pdf",
        size_bytes=10,
        sha256_hash="1" * 64,
        status="ACTIVE",
        replaces_file_id=existing.id,
        uploaded_by=ctx["admin_user_id"],
        uploaded_at=clock.utcnow(),
    )
    _attempt_and_expect_integrity_error(db_session, bad)


def test_version_greater_than_one_without_replaces_file_id_is_rejected(db_session, two_items) -> None:
    ctx = two_items
    existing = _make_active_file(
        db_session,
        ctx["item_a"].tenant_id,
        ctx["client_a"].id,
        ctx["declaration_a"].id,
        ctx["item_a"].id,
        ctx["admin_user_id"],
    )
    bad = DeclarationDocumentFile(
        tenant_id=ctx["item_a"].tenant_id,
        client_id=ctx["client_a"].id,
        declaration_id=ctx["declaration_a"].id,
        declaration_document_item_id=ctx["item_a"].id,
        logical_file_id=existing.logical_file_id,
        version=2,  # invalido: version>1 sin replaces_file_id
        original_filename="bad.pdf",
        storage_key=f"k/{uuid.uuid4().hex}",
        mime_type="application/pdf",
        extension=".pdf",
        size_bytes=10,
        sha256_hash="2" * 64,
        status="ACTIVE",
        replaces_file_id=None,
        uploaded_by=ctx["admin_user_id"],
        uploaded_at=clock.utcnow(),
    )
    _attempt_and_expect_integrity_error(db_session, bad)


def test_replace_across_different_item_of_same_tenant_is_rejected(db_session, two_items) -> None:
    """Reemplazo cruzado de item: el archivo de B intenta declarar que
    reemplaza al archivo de A, aunque ambos items sean del mismo tenant."""
    ctx = two_items
    file_a = _make_active_file(
        db_session,
        ctx["item_a"].tenant_id,
        ctx["client_a"].id,
        ctx["declaration_a"].id,
        ctx["item_a"].id,
        ctx["admin_user_id"],
    )
    bad = DeclarationDocumentFile(
        tenant_id=ctx["item_b"].tenant_id,
        client_id=ctx["client_b"].id,
        declaration_id=ctx["declaration_b"].id,
        declaration_document_item_id=ctx["item_b"].id,  # item B
        logical_file_id=file_a.logical_file_id,  # cadena de A
        version=2,
        original_filename="cross.pdf",
        storage_key=f"k/{uuid.uuid4().hex}",
        mime_type="application/pdf",
        extension=".pdf",
        size_bytes=10,
        sha256_hash="3" * 64,
        status="ACTIVE",
        replaces_file_id=file_a.id,  # archivo de A
        uploaded_by=ctx["admin_user_id"],
        uploaded_at=clock.utcnow(),
    )
    _attempt_and_expect_integrity_error(db_session, bad)


def test_replace_across_different_chain_of_same_item_is_rejected(db_session, two_items) -> None:
    """Reemplazo cruzado de cadena: mismo item, pero `replaces_file_id`
    apunta a un archivo de OTRA cadena (otro logical_file_id) dentro del
    mismo item."""
    ctx = two_items
    file_chain_1 = _make_active_file(
        db_session,
        ctx["item_a"].tenant_id,
        ctx["client_a"].id,
        ctx["declaration_a"].id,
        ctx["item_a"].id,
        ctx["admin_user_id"],
    )
    other_logical_file_id = uuid.uuid4()
    bad = DeclarationDocumentFile(
        tenant_id=ctx["item_a"].tenant_id,
        client_id=ctx["client_a"].id,
        declaration_id=ctx["declaration_a"].id,
        declaration_document_item_id=ctx["item_a"].id,
        logical_file_id=other_logical_file_id,  # cadena distinta a la de file_chain_1
        version=2,
        original_filename="cross_chain.pdf",
        storage_key=f"k/{uuid.uuid4().hex}",
        mime_type="application/pdf",
        extension=".pdf",
        size_bytes=10,
        sha256_hash="4" * 64,
        status="ACTIVE",
        replaces_file_id=file_chain_1.id,  # archivo de otra cadena
        uploaded_by=ctx["admin_user_id"],
        uploaded_at=clock.utcnow(),
    )
    _attempt_and_expect_integrity_error(db_session, bad)


def test_two_active_versions_in_same_chain_rejected_by_partial_index(db_session, two_items) -> None:
    ctx = two_items
    file_1 = _make_active_file(
        db_session,
        ctx["item_a"].tenant_id,
        ctx["client_a"].id,
        ctx["declaration_a"].id,
        ctx["item_a"].id,
        ctx["admin_user_id"],
    )
    bad = DeclarationDocumentFile(
        tenant_id=ctx["item_a"].tenant_id,
        client_id=ctx["client_a"].id,
        declaration_id=ctx["declaration_a"].id,
        declaration_document_item_id=ctx["item_a"].id,
        logical_file_id=file_1.logical_file_id,
        version=2,
        original_filename="second_active.pdf",
        storage_key=f"k/{uuid.uuid4().hex}",
        mime_type="application/pdf",
        extension=".pdf",
        size_bytes=10,
        sha256_hash="5" * 64,
        status="ACTIVE",  # invalido: ya hay una ACTIVE en esta cadena (file_1)
        replaces_file_id=file_1.id,
        uploaded_by=ctx["admin_user_id"],
        uploaded_at=clock.utcnow(),
    )
    _attempt_and_expect_integrity_error(db_session, bad)
