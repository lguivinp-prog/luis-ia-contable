"""Generacion automatica idempotente de tareas (Fase 2, Bloque 1, Dominio C,
Seccion 8 del plan): las 3 reglas con plantillas exactas, idempotencia,
inclusion de OBSERVED en DOCUMENT_COLLECTION, resolucion automatica con
precedencia, y realineacion de vencimiento cuando la fuente cambia."""

from datetime import UTC, date, datetime
from decimal import Decimal

from app.core.timezone import end_of_day_utc
from app.repositories import operational_task_repository
from app.services import task_generation_service
from tests.integration._operational_task_helpers import make_declaration_setup, make_document_item


def _sync(db_session, tenant, admin_user):
    return task_generation_service.sync_operational_tasks(
        db_session, tenant.id, trigger_source="MANUAL", triggered_by=admin_user.id
    )


def test_declaration_filing_generation_full_template(
    db_session, tenant, admin_user, obligation_catalog, frozen_clock
) -> None:
    frozen_clock["now"] = datetime(2026, 2, 10, 12, 0, tzinfo=UTC)
    setup = make_declaration_setup(db_session, tenant, admin_user, obligation_catalog)
    db_session.flush()

    run = _sync(db_session, tenant, admin_user)
    db_session.flush()

    task = operational_task_repository.get_by_generation_key(
        db_session, tenant.id, f"DECL_FILING:{setup['declaration'].id}"
    )
    assert task is not None
    assert task.title == (
        f"Presentar declaración — {setup['client'].business_name} — {setup['period'].to_display()}"
    )
    assert task.description == (
        f"Preparar y presentar la declaración del periodo {setup['period'].to_display()}. "
        "Vencimiento efectivo: 12/02/2026."
    )
    assert task.priority == "HIGH"
    assert task.is_system_generated is True
    assert task.generation_run_id == run.id
    assert task.due_at == end_of_day_utc(date(2026, 2, 12))
    assert run.summary["created"] == 1


def test_declaration_filing_generation_is_idempotent(
    db_session, tenant, admin_user, obligation_catalog, frozen_clock
) -> None:
    frozen_clock["now"] = datetime(2026, 2, 10, 12, 0, tzinfo=UTC)
    make_declaration_setup(db_session, tenant, admin_user, obligation_catalog)
    db_session.flush()

    run1 = _sync(db_session, tenant, admin_user)
    db_session.flush()
    assert run1.summary["created"] == 1

    run2 = _sync(db_session, tenant, admin_user)
    db_session.flush()
    assert run2.summary["created"] == 0
    assert run2.summary["already_exists"] == 1


def test_declaration_filing_overdue_gets_urgent_priority(
    db_session, tenant, admin_user, obligation_catalog, frozen_clock
) -> None:
    frozen_clock["now"] = datetime(2026, 2, 13, 12, 0, tzinfo=UTC)
    make_declaration_setup(db_session, tenant, admin_user, obligation_catalog)
    db_session.flush()

    _sync(db_session, tenant, admin_user)
    db_session.flush()

    tasks, total = operational_task_repository.list_tasks(db_session, tenant.id, [], limit=10, offset=0)
    assert total == 1
    assert tasks[0].priority == "URGENT"


def test_document_collection_generation_includes_observed_status(
    db_session, tenant, admin_user, obligation_catalog, frozen_clock
) -> None:
    frozen_clock["now"] = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
    setup = make_declaration_setup(db_session, tenant, admin_user, obligation_catalog)
    item = make_document_item(db_session, tenant, setup)
    item.status = "OBSERVED"
    db_session.flush()

    run = _sync(db_session, tenant, admin_user)
    db_session.flush()

    task = operational_task_repository.get_by_generation_key(
        db_session, tenant.id, f"DOC_COLLECTION:{item.id}"
    )
    assert task is not None
    assert task.task_type == "DOCUMENT_COLLECTION"
    assert run.summary["created"] >= 1


def test_document_collection_second_sync_returns_already_exists(
    db_session, tenant, admin_user, obligation_catalog, frozen_clock
) -> None:
    frozen_clock["now"] = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
    setup = make_declaration_setup(db_session, tenant, admin_user, obligation_catalog)
    item = make_document_item(db_session, tenant, setup)
    item.status = "OBSERVED"
    db_session.flush()

    _sync(db_session, tenant, admin_user)
    db_session.flush()
    run2 = _sync(db_session, tenant, admin_user)
    db_session.flush()

    assert run2.summary["already_exists"] >= 1


def test_document_collection_observed_with_closed_declaration_does_not_generate(
    db_session, tenant, admin_user, obligation_catalog, frozen_clock
) -> None:
    frozen_clock["now"] = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
    setup = make_declaration_setup(db_session, tenant, admin_user, obligation_catalog)
    item = make_document_item(db_session, tenant, setup)
    item.status = "OBSERVED"
    setup["declaration"].workflow_status = "CLOSED"
    db_session.flush()

    run = _sync(db_session, tenant, admin_user)
    db_session.flush()

    task = operational_task_repository.get_by_generation_key(
        db_session, tenant.id, f"DOC_COLLECTION:{item.id}"
    )
    assert task is None
    assert run.summary["created"] == 0


def test_fee_collection_generation_for_overdue_charge(
    db_session, tenant, admin_user, frozen_clock
) -> None:
    frozen_clock["now"] = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)
    from tests.integration._declaration_helpers import make_client

    client = make_client(db_session, tenant.id)
    from app.services import professional_fee_service
    from app.services.declaration_risk import UPCOMING_THRESHOLD_DAYS  # noqa: F401

    agreement = professional_fee_service.create_agreement(
        db_session,
        tenant.id,
        client.id,
        service_type="CONTABILIDAD_MENSUAL",
        amount=Decimal("500.00"),
        billing_day=5,
        included_services=None,
        description=None,
        valid_from=date(2026, 1, 1),
        valid_until=None,
        observations=None,
        actor_user_id=None,
    )
    from tests.integration._declaration_helpers import make_monthly_period

    period = make_monthly_period(db_session, tenant.id, fiscal_year=2026, month=2)
    charge = professional_fee_service.create_charge(
        db_session,
        tenant.id,
        agreement,
        tax_period_id=period.id,
        amount=None,
        due_date=date(2026, 2, 15),
        observations=None,
        actor_user_id=None,
    )
    db_session.flush()

    run = _sync(db_session, tenant, admin_user)
    db_session.flush()

    task = operational_task_repository.get_by_generation_key(
        db_session, tenant.id, f"FEE_COLLECTION:{charge.id}"
    )
    assert task is not None
    assert task.priority == "URGENT"
    assert task.professional_fee_agreement_id == agreement.id
    assert task.client_id == client.id
    assert run.summary["created"] == 1


def test_declaration_filed_resolves_task_to_completed(
    db_session, tenant, admin_user, obligation_catalog, frozen_clock
) -> None:
    frozen_clock["now"] = datetime(2026, 2, 10, 12, 0, tzinfo=UTC)
    setup = make_declaration_setup(db_session, tenant, admin_user, obligation_catalog)
    db_session.flush()
    _sync(db_session, tenant, admin_user)
    db_session.flush()

    setup["declaration"].filing_status = "FILED"
    db_session.flush()

    _sync(db_session, tenant, admin_user)
    db_session.flush()

    task = operational_task_repository.get_by_generation_key(
        db_session, tenant.id, f"DECL_FILING:{setup['declaration'].id}"
    )
    assert task.status == "COMPLETED"
    assert task.resolution_origin == "SYSTEM"
    assert task.completed_by is None


def test_declaration_due_date_change_realigns_task(
    db_session, tenant, admin_user, obligation_catalog, frozen_clock
) -> None:
    frozen_clock["now"] = datetime(2026, 2, 8, 12, 0, tzinfo=UTC)
    setup = make_declaration_setup(db_session, tenant, admin_user, obligation_catalog)
    db_session.flush()
    _sync(db_session, tenant, admin_user)
    db_session.flush()

    setup["declaration"].current_effective_due_date = date(2026, 2, 20)
    db_session.flush()

    _sync(db_session, tenant, admin_user)
    db_session.flush()

    task = operational_task_repository.get_by_generation_key(
        db_session, tenant.id, f"DECL_FILING:{setup['declaration'].id}"
    )
    assert task.due_at == end_of_day_utc(date(2026, 2, 20))
    assert task.source_due_at == end_of_day_utc(date(2026, 2, 20))
    assert task.due_is_overridden is False


def test_reschedule_then_realign_preserves_overridden_due_at(
    db_session, tenant, admin_user, obligation_catalog, frozen_clock
) -> None:
    from app.schemas.task import TaskDueInput
    from app.services import operational_task_service

    frozen_clock["now"] = datetime(2026, 2, 8, 12, 0, tzinfo=UTC)
    setup = make_declaration_setup(db_session, tenant, admin_user, obligation_catalog)
    db_session.flush()
    _sync(db_session, tenant, admin_user)
    db_session.flush()

    task = operational_task_repository.get_by_generation_key(
        db_session, tenant.id, f"DECL_FILING:{setup['declaration'].id}"
    )
    operational_task_service.reschedule_task(
        db_session,
        tenant.id,
        admin_user,
        task.id,
        expected_version=task.version,
        due=TaskDueInput(due_date=date(2026, 2, 25)),
    )
    db_session.flush()

    setup["declaration"].current_effective_due_date = date(2026, 2, 20)
    db_session.flush()
    _sync(db_session, tenant, admin_user)
    db_session.flush()

    refreshed = operational_task_repository.get_by_generation_key(
        db_session, tenant.id, f"DECL_FILING:{setup['declaration'].id}"
    )
    assert refreshed.due_is_overridden is True
    assert refreshed.due_at == end_of_day_utc(date(2026, 2, 25))
    assert refreshed.source_due_at == end_of_day_utc(date(2026, 2, 20))
