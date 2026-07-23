"""Pruebas de integracion de Control documental (Bloque 3.2, Dominio A): ver
"Control documental" / "Sincronizacion documental" del plan y ADR-0033 a ADR-0036.
Cubre: catalogo sistema/personalizado con rechazo cross-tenant, sincronizacion
idempotente, rechazo de cruce entre clientes por SQL directo, conservacion de
items historicos, sincronizacion administrativa masiva por periodo, y el
recalculo de document_status via cambio de estado de item."""

import uuid
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.declaration_document import (
    ClientDocumentRequirement,
    DeclarationDocumentItem,
    DocumentRequirementCatalog,
)
from app.services import declaration_document_service, declaration_service
from app.services.document_requirement_catalog_service import (
    DocumentCatalogEntryNotFoundError,
    ForeignDocumentCatalogEntryError,
)
from tests.integration._declaration_helpers import (
    make_client,
    make_client_obligation,
    make_entry,
    make_monthly_period,
    make_other_tenant_with_user,
    make_source,
    make_version,
)


@pytest.fixture()
def base_setup(db_session, tenant, admin_user, obligation_catalog):
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
    return {
        "client": client,
        "obligation": obligation,
        "client_obligation": co,
        "source": source,
        "version": version,
        "period": period,
        "declaration": declaration,
    }


def _make_catalog_entry(db_session, tenant_id=None, *, code="RENTA_ANUAL") -> DocumentRequirementCatalog:
    entry = DocumentRequirementCatalog(
        tenant_id=tenant_id,
        is_system=tenant_id is None,
        code=code,
        name="Constancia de renta anual",
        is_active=True,
    )
    db_session.add(entry)
    db_session.flush()
    return entry


def _assign(
    db_session,
    tenant_id,
    client_id,
    catalog_entry,
    *,
    client_obligation_id=None,
    valid_from=date(2026, 1, 1),
    valid_until=None,
) -> ClientDocumentRequirement:
    return declaration_document_service.assign_requirement(
        db_session,
        tenant_id,
        client_id,
        requirement_catalog_id=catalog_entry.id,
        client_obligation_id=client_obligation_id,
        is_mandatory=True,
        periodicity="MONTHLY",
        valid_from=valid_from,
        valid_until=valid_until,
        description=None,
        responsible_user_id=None,
        reminder_lead_days=5,
    )


# --- catalogo ---


def test_assign_requirement_accepts_system_catalog_entry(db_session, tenant, base_setup) -> None:
    catalog_entry = _make_catalog_entry(db_session, tenant_id=None)
    requirement = _assign(db_session, tenant.id, base_setup["client"].id, catalog_entry)
    assert requirement.id is not None
    assert requirement.client_obligation_id is None


def test_assign_requirement_rejects_catalog_entry_from_another_tenant(
    db_session, tenant, base_setup
) -> None:
    other_tenant, _ = make_other_tenant_with_user(db_session)
    foreign_entry = _make_catalog_entry(db_session, tenant_id=other_tenant.id, code="FOREIGN")

    with pytest.raises(ForeignDocumentCatalogEntryError):
        _assign(db_session, tenant.id, base_setup["client"].id, foreign_entry)


def test_assign_requirement_rejects_unknown_catalog_entry(db_session, tenant, base_setup) -> None:
    with pytest.raises(DocumentCatalogEntryNotFoundError):
        _assign(
            db_session,
            tenant.id,
            base_setup["client"].id,
            type("Fake", (), {"id": uuid.uuid4()})(),
        )


# --- sincronizacion idempotente ---


def test_sync_requirements_is_idempotent(db_session, tenant, admin_user, base_setup) -> None:
    catalog_entry = _make_catalog_entry(db_session, tenant_id=None)
    _assign(db_session, tenant.id, base_setup["client"].id, catalog_entry)

    declaration = base_setup["declaration"]

    first = declaration_document_service.sync_requirements_for_declaration(
        db_session, tenant.id, declaration, actor_user_id=admin_user.id
    )
    assert len(first["created"]) == 1
    assert first["existing"] == []

    second = declaration_document_service.sync_requirements_for_declaration(
        db_session, tenant.id, declaration, actor_user_id=admin_user.id
    )
    assert second["created"] == []
    assert len(second["existing"]) == 1

    items = declaration_document_service.list_items(db_session, tenant.id, declaration.id)
    assert len(items) == 1


def test_sync_requirements_matches_general_and_obligation_specific(
    db_session, tenant, admin_user, base_setup
) -> None:
    general_entry = _make_catalog_entry(db_session, tenant_id=None, code="GENERAL_DOC")
    specific_entry = _make_catalog_entry(db_session, tenant_id=None, code="SPECIFIC_DOC")

    _assign(db_session, tenant.id, base_setup["client"].id, general_entry, client_obligation_id=None)
    _assign(
        db_session,
        tenant.id,
        base_setup["client"].id,
        specific_entry,
        client_obligation_id=base_setup["client_obligation"].id,
    )

    result = declaration_document_service.sync_requirements_for_declaration(
        db_session, tenant.id, base_setup["declaration"], actor_user_id=admin_user.id
    )
    assert len(result["created"]) == 2


def test_sync_requirements_preserves_historical_items_after_deactivation(
    db_session, tenant, admin_user, base_setup
) -> None:
    catalog_entry = _make_catalog_entry(db_session, tenant_id=None)
    requirement = _assign(db_session, tenant.id, base_setup["client"].id, catalog_entry)

    declaration_document_service.sync_requirements_for_declaration(
        db_session, tenant.id, base_setup["declaration"], actor_user_id=admin_user.id
    )
    items_before = declaration_document_service.list_items(
        db_session, tenant.id, base_setup["declaration"].id
    )
    assert len(items_before) == 1

    requirement.is_active = False
    db_session.flush()

    items_after = declaration_document_service.list_items(
        db_session, tenant.id, base_setup["declaration"].id
    )
    assert len(items_after) == 1
    assert items_after[0].id == items_before[0].id


def test_bulk_sync_over_period_covers_preexisting_declarations(
    db_session, tenant, admin_user, base_setup
) -> None:
    """Simula una declaracion preexistente (creada sin sync automatico, como
    en 3.1 antes de existir control documental) y verifica que la accion
    administrativa masiva por periodo la sincroniza sin backfill destructivo."""
    catalog_entry = _make_catalog_entry(db_session, tenant_id=None)
    _assign(db_session, tenant.id, base_setup["client"].id, catalog_entry)

    declaration = base_setup["declaration"]
    assert declaration_document_service.list_items(db_session, tenant.id, declaration.id) == []

    from app.repositories import declaration_repository

    declarations, _total = declaration_repository.list_for_dashboard(
        db_session, tenant.id, tax_period_id=base_setup["period"].id, limit=100, offset=0
    )
    for decl in declarations:
        declaration_document_service.sync_requirements_for_declaration(
            db_session, tenant.id, decl, actor_user_id=admin_user.id
        )

    items = declaration_document_service.list_items(db_session, tenant.id, declaration.id)
    assert len(items) == 1


# --- rechazo de cruce entre clientes (prueba SQL directa) ---


def test_declaration_document_item_rejects_cross_client_mix(db_session, tenant, base_setup) -> None:
    other_client = make_client(db_session, tenant.id, name="Otro cliente del mismo tenant")
    catalog_entry = _make_catalog_entry(db_session, tenant_id=None)
    foreign_requirement = _assign(db_session, tenant.id, other_client.id, catalog_entry)

    bad_item = DeclarationDocumentItem(
        tenant_id=tenant.id,
        client_id=base_setup["client"].id,
        declaration_id=base_setup["declaration"].id,
        client_document_requirement_id=foreign_requirement.id,
        status="NOT_REQUESTED",
    )
    savepoint = db_session.begin_nested()
    with pytest.raises(IntegrityError):
        db_session.add(bad_item)
        db_session.flush()
    savepoint.rollback()


# --- cambio de estado de item + recalculo de document_status ---


def test_change_item_status_recalculates_document_status(
    db_session, tenant, admin_user, base_setup
) -> None:
    catalog_entry = _make_catalog_entry(db_session, tenant_id=None)
    _assign(db_session, tenant.id, base_setup["client"].id, catalog_entry)

    declaration = base_setup["declaration"]
    declaration_document_service.sync_requirements_for_declaration(
        db_session, tenant.id, declaration, actor_user_id=admin_user.id
    )
    assert declaration.document_status == "PENDING"

    items = declaration_document_service.list_items(db_session, tenant.id, declaration.id)
    item = items[0]

    declaration_document_service.change_item_status(
        db_session,
        tenant.id,
        declaration,
        item.id,
        target="ACCEPTED",
        reason=None,
        file_metadata=None,
        actor_user_id=admin_user.id,
    )
    assert declaration.document_status == "COMPLETE"

    history = declaration_document_service.declaration_document_repository.list_item_status_history(
        db_session, tenant.id, item.id
    )
    assert len(history) == 1
    assert history[0].from_status == "NOT_REQUESTED"
    assert history[0].to_status == "ACCEPTED"


def test_change_item_status_rejects_invalid_file_metadata(
    db_session, tenant, admin_user, base_setup
) -> None:
    catalog_entry = _make_catalog_entry(db_session, tenant_id=None)
    _assign(db_session, tenant.id, base_setup["client"].id, catalog_entry)
    declaration = base_setup["declaration"]
    declaration_document_service.sync_requirements_for_declaration(
        db_session, tenant.id, declaration, actor_user_id=admin_user.id
    )
    item = declaration_document_service.list_items(db_session, tenant.id, declaration.id)[0]

    from app.services.declaration_document_service import InvalidFileMetadataError

    with pytest.raises(InvalidFileMetadataError):
        declaration_document_service.change_item_status(
            db_session,
            tenant.id,
            declaration,
            item.id,
            target="RECEIVED",
            reason=None,
            file_metadata={"path": "/etc/passwd"},
            actor_user_id=admin_user.id,
        )
