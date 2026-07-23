"""Generacion masiva idempotente (ver ADR-0021/0027/0028): itera
asignaciones reales (nunca un producto cartesiano cliente x catalogo),
persiste la resolucion completa por item, detecta preview obsoleto (STALE),
y confirma con limites transaccionales precisos (SAVEPOINT por item,
reconfirmacion idempotente, reintento real de un run FAILED)."""

from datetime import date

import pytest
from sqlalchemy import select

from app.models.tax_schedule import TaxScheduleSource, TaxScheduleVersion
from app.repositories import declaration_generation_repository, declaration_repository
from app.services import declaration_generation_service, declaration_service
from app.services.declaration_generation_service import GenerationPreviewStaleError
from tests.integration._declaration_helpers import (
    make_client,
    make_client_obligation,
    make_entry,
    make_monthly_period,
    make_source,
    make_version,
)


@pytest.fixture()
def two_clients_partial_assignment(db_session, tenant, obligation_catalog):
    """2 clientes, 2 obligaciones globales, cada cliente con solo UNA
    asignada (distinta entre si) -- nunca debe generarse la combinacion no
    asignada (ver "Generacion desde asignaciones reales")."""
    obligation_a = obligation_catalog["621"]
    other_obligations = [o for code, o in obligation_catalog.items() if code != "621"]
    obligation_b = obligation_catalog.get("601", other_obligations[0])

    client1 = make_client(db_session, tenant.id, name="Cliente Uno")
    client2 = make_client(db_session, tenant.id, name="Cliente Dos")

    co1 = make_client_obligation(db_session, tenant.id, client1.id, obligation_a.id)
    co2 = make_client_obligation(db_session, tenant.id, client2.id, obligation_b.id)

    source_a = make_source(db_session, tenant.id, obligation_a.id, name="Fuente A")
    version_a = make_version(db_session, tenant.id, source_a.id, fiscal_year=2026)
    make_entry(db_session, tenant.id, version_a.id, tax_period="2026-01", digits=list(range(10)))

    source_b = make_source(db_session, tenant.id, obligation_b.id, name="Fuente B")
    version_b = make_version(db_session, tenant.id, source_b.id, fiscal_year=2026)
    make_entry(db_session, tenant.id, version_b.id, tax_period="2026-01", digits=list(range(10)))

    period = make_monthly_period(db_session, tenant.id, fiscal_year=2026, month=1)

    return {
        "client1": client1,
        "client2": client2,
        "co1": co1,
        "co2": co2,
        "period": period,
    }


def test_generation_only_produces_assigned_combinations(
    db_session, tenant, admin_user, two_clients_partial_assignment
) -> None:
    setup = two_clients_partial_assignment
    run = declaration_generation_service.preview(
        db_session, tenant.id, admin_user.id, tax_period_id=setup["period"].id, filters=None
    )
    items = declaration_generation_service.list_items(db_session, tenant.id, run.id)
    assert len(items) == 2  # exactamente las 2 asignaciones reales, nunca 4 (2x2)

    pairs = {(i.client_id, i.client_obligation_id) for i in items}
    assert (setup["client1"].id, setup["co1"].id) in pairs
    assert (setup["client2"].id, setup["co2"].id) in pairs

    apto_items = [i for i in items if i.classification == "APTO"]
    assert len(apto_items) == 2


def test_confirm_is_idempotent_across_full_cycle(
    db_session, tenant, admin_user, two_clients_partial_assignment
) -> None:
    setup = two_clients_partial_assignment
    run = declaration_generation_service.preview(
        db_session, tenant.id, admin_user.id, tax_period_id=setup["period"].id, filters=None
    )
    first = declaration_generation_service.confirm(db_session, tenant.id, admin_user.id, run.id)
    assert first.status == "COMPLETED"
    assert first.total_created == 2

    second = declaration_generation_service.confirm(db_session, tenant.id, admin_user.id, run.id)
    assert second.status == "COMPLETED"
    assert second.total_created == 2  # no duplica, retorna el resultado existente

    declarations = declaration_repository.list_for_dashboard(db_session, tenant.id, limit=10, offset=0)[0]
    assert len(declarations) == 2  # nunca se duplicaron


def test_stale_preview_blocks_confirmation_and_requires_refresh(
    db_session, tenant, admin_user, two_clients_partial_assignment
) -> None:
    setup = two_clients_partial_assignment
    run = declaration_generation_service.preview(
        db_session, tenant.id, admin_user.id, tax_period_id=setup["period"].id, filters=None
    )
    items_before = declaration_generation_service.list_items(db_session, tenant.id, run.id)
    original_hash = next(i.preview_resolution_hash for i in items_before if i.classification == "APTO")

    # Se modifica el cronograma DESPUES del preview: nueva version aprobada
    # que reemplaza a la anterior para la misma obligacion/fuente/scope.
    co1_obligation_id = setup["co1"].obligation_id
    source = db_session.execute(
        select(TaxScheduleSource).where(
            TaxScheduleSource.tenant_id == tenant.id,
            TaxScheduleSource.obligation_id == co1_obligation_id,
        )
    ).scalars().first()
    # degradar la version vieja PRIMERO (el indice unico parcial solo permite
    # una APPROVED por scope a la vez) y solo despues crear/aprobar la nueva.
    old_source_versions = db_session.execute(
        select(TaxScheduleVersion).where(
            TaxScheduleVersion.source_id == source.id, TaxScheduleVersion.status == "APPROVED"
        )
    ).scalars().all()
    for v in old_source_versions:
        v.status = "SUPERSEDED"
    db_session.flush()

    new_version = make_version(db_session, tenant.id, source.id, fiscal_year=2026, status="APPROVED")
    make_entry(
        db_session,
        tenant.id,
        new_version.id,
        tax_period="2026-01",
        digits=list(range(10)),
        due_original=date(2026, 2, 20),
        due_effective=date(2026, 2, 20),
    )

    with pytest.raises(GenerationPreviewStaleError):
        declaration_generation_service.confirm(db_session, tenant.id, admin_user.id, run.id)

    refreshed_run = declaration_generation_repository.get_run(db_session, tenant.id, run.id)
    assert refreshed_run.status == "STALE"
    assert refreshed_run.stale_diff  # describe el cambio

    # cero declaraciones creadas
    declarations = declaration_repository.list_for_dashboard(db_session, tenant.id, limit=10, offset=0)[0]
    assert len(declarations) == 0

    refreshed = declaration_generation_service.refresh_preview(db_session, tenant.id, run.id)
    assert refreshed.status == "PREVIEWED"
    assert refreshed.stale_diff is None

    items_after_refresh = declaration_generation_service.list_items(db_session, tenant.id, run.id)
    new_hash = next(
        i.preview_resolution_hash
        for i in items_after_refresh
        if i.client_obligation_id == setup["co1"].id and i.classification == "APTO"
    )
    assert new_hash != original_hash  # la resolucion realmente cambio

    confirmed = declaration_generation_service.confirm(db_session, tenant.id, admin_user.id, run.id)
    assert confirmed.status == "COMPLETED"
    assert confirmed.total_created == 2


def test_confirm_recoverable_error_contained_by_savepoint(
    db_session, tenant, admin_user, two_clients_partial_assignment, monkeypatch
) -> None:
    setup = two_clients_partial_assignment
    run = declaration_generation_service.preview(
        db_session, tenant.id, admin_user.id, tax_period_id=setup["period"].id, filters=None
    )
    items = declaration_generation_service.list_items(db_session, tenant.id, run.id)
    apto_item = next(i for i in items if i.classification == "APTO")
    failing_obligation_id = apto_item.client_obligation_id

    original = declaration_service.create_declaration

    def _flaky_create(db, tenant_id, user_id, **kwargs):
        if kwargs.get("client_obligation_id") == failing_obligation_id:
            raise RuntimeError("fallo simulado de un solo item")
        return original(db, tenant_id, user_id, **kwargs)

    monkeypatch.setattr(declaration_service, "create_declaration", _flaky_create)
    monkeypatch.setattr(
        declaration_generation_service.declaration_service, "create_declaration", _flaky_create
    )

    result = declaration_generation_service.confirm(db_session, tenant.id, admin_user.id, run.id)
    assert result.status == "COMPLETED_WITH_ERRORS"
    assert result.total_created == 1
    assert result.total_failed == 1

    declarations = declaration_repository.list_for_dashboard(db_session, tenant.id, limit=10, offset=0)[0]
    assert len(declarations) == 1  # el item valido SI se creo, contenido por su SAVEPOINT


def test_confirm_systemic_error_creates_zero_declarations_and_marks_failed(
    db_session, tenant, admin_user, two_clients_partial_assignment, monkeypatch
) -> None:
    setup = two_clients_partial_assignment
    run = declaration_generation_service.preview(
        db_session, tenant.id, admin_user.id, tax_period_id=setup["period"].id, filters=None
    )
    # En produccion, preview y confirm son solicitudes HTTP separadas: el
    # router de preview hace su propio db.commit() antes de responder. Se
    # replica aqui para que el rollback() sistemico de confirm() no revierta
    # tambien la creacion del run/items (que ya estarian comprometidos).
    db_session.commit()

    # Forzamos el fallo FUERA de cualquier SAVEPOINT por item (justo despues
    # de la prevalidacion, al listar items para el bucle de confirmacion),
    # simulando un error sistemico tras iniciar la confirmacion.
    original_list_items = declaration_generation_repository.list_items

    call_count = {"n": 0}

    def _list_items_then_boom(db, tenant_id, run_id):
        call_count["n"] += 1
        if call_count["n"] > 1:  # la 2a llamada ocurre dentro de confirm(), tras la prevalidacion
            raise RuntimeError("error sistemico simulado")
        return original_list_items(db, tenant_id, run_id)

    monkeypatch.setattr(declaration_generation_repository, "list_items", _list_items_then_boom)

    with pytest.raises(RuntimeError):
        declaration_generation_service.confirm(db_session, tenant.id, admin_user.id, run.id)

    declarations = declaration_repository.list_for_dashboard(db_session, tenant.id, limit=10, offset=0)[0]
    assert len(declarations) == 0  # nunca queda un run FAILED con declaraciones parciales

    monkeypatch.setattr(declaration_generation_repository, "list_items", original_list_items)
    failed_run = declaration_generation_repository.get_run(db_session, tenant.id, run.id)
    assert failed_run.status == "FAILED"


def test_confirm_race_existing_declaration_reclassified(
    db_session, tenant, admin_user, two_clients_partial_assignment, monkeypatch
) -> None:
    """Carrera real: otra transaccion crea la declaracion en la ventana
    estrecha entre la prevalidacion de staleness y el INSERT dentro del
    SAVEPOINT del item. Se simula haciendo que la prevalidacion no vea la
    fila (como ocurriria si la otra transaccion aun no habia comprometido
    sus cambios al momento de leer), mientras que la restriccion UNIQUE real
    si la detecta en el INSERT -- exactamente el caso que el SAVEPOINT por
    item debe contener, reclasificando a YA_EXISTENTE sin abortar el run."""
    setup = two_clients_partial_assignment
    run = declaration_generation_service.preview(
        db_session, tenant.id, admin_user.id, tax_period_id=setup["period"].id, filters=None
    )
    items = declaration_generation_service.list_items(db_session, tenant.id, run.id)
    apto_item = next(i for i in items if i.classification == "APTO")

    # La declaracion "ganadora" de la carrera ya existe en la base de datos...
    declaration_service.create_declaration(
        db_session,
        tenant.id,
        admin_user.id,
        client_id=apto_item.client_id,
        client_obligation_id=apto_item.client_obligation_id,
        tax_period_id=setup["period"].id,
        origin="MANUAL",
    )

    # ...pero la prevalidacion de staleness no la ve (ventana de carrera).
    original_get_by_natural_key = declaration_repository.get_by_natural_key
    monkeypatch.setattr(
        declaration_generation_service.declaration_repository,
        "get_by_natural_key",
        lambda *a, **k: None,
    )

    result = declaration_generation_service.confirm(db_session, tenant.id, admin_user.id, run.id)
    assert result.status in ("COMPLETED", "COMPLETED_WITH_ERRORS")
    assert result.total_created == 1  # el otro item APTO
    assert result.total_skipped >= 1

    monkeypatch.setattr(
        declaration_generation_service.declaration_repository,
        "get_by_natural_key",
        original_get_by_natural_key,
    )
    refreshed_item = declaration_generation_repository.get_item(db_session, tenant.id, run.id, apto_item.id)
    assert refreshed_item.classification == "YA_EXISTENTE"
