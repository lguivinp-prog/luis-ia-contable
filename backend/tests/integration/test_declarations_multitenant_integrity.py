"""Integridad multitenant reforzada mediante FKs compuestas (ver
ADR-0024/0025): una insercion SQL/ORM directa no debe poder mezclar datos de
tenants o alcances distintos, incluso pasando por alto la capa de servicio.
Cubre los 10 casos exigidos entre la segunda y tercera ronda de correccion."""

import uuid
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.declaration import Declaration, DeclarationDueDateHistory, DeclarationStatusHistory
from app.models.declaration_generation import DeclarationGenerationRun, DeclarationGenerationRunItem
from app.services import declaration_service
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


def _attempt_and_expect_rejection(db_session, obj) -> None:
    savepoint = db_session.begin_nested()
    with pytest.raises(IntegrityError):
        db_session.add(obj)
        db_session.flush()
    savepoint.rollback()


def test_declaration_rejects_client_id_of_another_tenant(db_session, tenant, base_setup) -> None:
    other_tenant, _ = make_other_tenant_with_user(db_session)
    other_client = make_client(db_session, other_tenant.id)

    bad = Declaration(
        tenant_id=tenant.id,
        client_id=other_client.id,  # de otro tenant
        client_obligation_id=base_setup["client_obligation"].id,
        tax_period_id=base_setup["period"].id,
        initial_effective_due_date=date(2026, 2, 12),
        current_original_due_date=date(2026, 2, 12),
        current_effective_due_date=date(2026, 2, 12),
        schedule_source_id=base_setup["source"].id,
        schedule_version_id=base_setup["version"].id,
        schedule_rule_type="ENTRY",
        schedule_entry_id=None,
        schedule_exception_id=None,
        schedule_snapshot={},
    )
    # ENTRY sin schedule_entry_id violaria el CHECK antes que la FK; usamos
    # un entry_id real solo para aislar la FK bajo prueba.
    bad.schedule_entry_id = uuid.uuid4()
    _attempt_and_expect_rejection(db_session, bad)


def test_declaration_rejects_client_obligation_of_another_client(db_session, tenant, base_setup) -> None:
    other_client = make_client(db_session, tenant.id, name="Otro cliente")
    other_obligation = base_setup["obligation"]
    other_co = make_client_obligation(db_session, tenant.id, other_client.id, other_obligation.id)

    bad = Declaration(
        tenant_id=tenant.id,
        client_id=base_setup["client"].id,
        client_obligation_id=other_co.id,  # pertenece a OTRO cliente
        tax_period_id=base_setup["period"].id,
        initial_effective_due_date=date(2026, 2, 12),
        current_original_due_date=date(2026, 2, 12),
        current_effective_due_date=date(2026, 2, 12),
        schedule_source_id=base_setup["source"].id,
        schedule_version_id=base_setup["version"].id,
        schedule_rule_type="ENTRY",
        schedule_entry_id=uuid.uuid4(),
        schedule_exception_id=None,
        schedule_snapshot={},
    )
    _attempt_and_expect_rejection(db_session, bad)


def test_declaration_rejects_schedule_version_of_another_source(db_session, tenant, base_setup) -> None:
    other_source = make_source(db_session, tenant.id, base_setup["obligation"].id, name="Otra fuente")
    other_version = make_version(db_session, tenant.id, other_source.id, fiscal_year=2027)

    bad = Declaration(
        tenant_id=tenant.id,
        client_id=base_setup["client"].id,
        client_obligation_id=base_setup["client_obligation"].id,
        tax_period_id=base_setup["period"].id,
        initial_effective_due_date=date(2026, 2, 12),
        current_original_due_date=date(2026, 2, 12),
        current_effective_due_date=date(2026, 2, 12),
        schedule_source_id=base_setup["source"].id,  # NO es la fuente de other_version
        schedule_version_id=other_version.id,
        schedule_rule_type="ENTRY",
        schedule_entry_id=uuid.uuid4(),
        schedule_exception_id=None,
        schedule_snapshot={},
    )
    _attempt_and_expect_rejection(db_session, bad)


def test_declaration_rejects_schedule_entry_of_another_version(db_session, tenant, base_setup) -> None:
    other_version = make_version(db_session, tenant.id, base_setup["source"].id, fiscal_year=2027)
    other_entry = make_entry(db_session, tenant.id, other_version.id, tax_period="2027-01")

    bad = Declaration(
        tenant_id=tenant.id,
        client_id=base_setup["client"].id,
        client_obligation_id=base_setup["client_obligation"].id,
        tax_period_id=base_setup["period"].id,
        initial_effective_due_date=date(2026, 2, 12),
        current_original_due_date=date(2026, 2, 12),
        current_effective_due_date=date(2026, 2, 12),
        schedule_source_id=base_setup["source"].id,
        schedule_version_id=base_setup["version"].id,  # NO es la version de other_entry
        schedule_rule_type="ENTRY",
        schedule_entry_id=other_entry.id,
        schedule_exception_id=None,
        schedule_snapshot={},
    )
    _attempt_and_expect_rejection(db_session, bad)


def test_declaration_rejects_assigned_user_of_another_tenant(db_session, tenant, base_setup) -> None:
    _, other_user = make_other_tenant_with_user(db_session)

    bad = Declaration(
        tenant_id=tenant.id,
        client_id=base_setup["client"].id,
        client_obligation_id=base_setup["client_obligation"].id,
        tax_period_id=base_setup["period"].id,
        assigned_user_id=other_user.id,  # de otro tenant
        initial_effective_due_date=date(2026, 2, 12),
        current_original_due_date=date(2026, 2, 12),
        current_effective_due_date=date(2026, 2, 12),
        schedule_source_id=base_setup["source"].id,
        schedule_version_id=base_setup["version"].id,
        schedule_rule_type="ENTRY",
        schedule_entry_id=uuid.uuid4(),
        schedule_exception_id=None,
        schedule_snapshot={},
    )
    _attempt_and_expect_rejection(db_session, bad)


def test_generation_run_rejects_requested_by_of_another_tenant(db_session, tenant, base_setup) -> None:
    _, other_user = make_other_tenant_with_user(db_session)

    bad = DeclarationGenerationRun(
        tenant_id=tenant.id,
        tax_period_id=base_setup["period"].id,
        requested_by=other_user.id,  # de otro tenant
        requested_at=base_setup["declaration"].created_at,
    )
    _attempt_and_expect_rejection(db_session, bad)


def _make_valid_run(db_session, tenant, base_setup) -> DeclarationGenerationRun:
    from sqlalchemy import select

    from app.models.user import User

    user = db_session.execute(select(User).where(User.tenant_id == tenant.id)).scalars().first()
    run = DeclarationGenerationRun(
        tenant_id=tenant.id,
        tax_period_id=base_setup["period"].id,
        requested_by=user.id,
        requested_at=base_setup["declaration"].created_at,
    )
    db_session.add(run)
    db_session.flush()
    return run


def test_generation_run_item_rejects_client_id_of_another_tenant(db_session, tenant, base_setup) -> None:
    other_tenant, _ = make_other_tenant_with_user(db_session)
    other_client = make_client(db_session, other_tenant.id)
    run = _make_valid_run(db_session, tenant, base_setup)

    bad_item = DeclarationGenerationRunItem(
        tenant_id=tenant.id,
        run_id=run.id,
        client_id=other_client.id,  # de otro tenant
        client_obligation_id=base_setup["client_obligation"].id,
        classification="APTO",
        created_at=base_setup["declaration"].created_at,
    )
    _attempt_and_expect_rejection(db_session, bad_item)


def test_generation_run_item_rejects_client_obligation_of_another_client(
    db_session, tenant, base_setup
) -> None:
    other_client = make_client(db_session, tenant.id, name="Otro cliente 2")
    other_co = make_client_obligation(db_session, tenant.id, other_client.id, base_setup["obligation"].id)
    run = _make_valid_run(db_session, tenant, base_setup)

    bad_item = DeclarationGenerationRunItem(
        tenant_id=tenant.id,
        run_id=run.id,
        client_id=base_setup["client"].id,
        client_obligation_id=other_co.id,  # pertenece a OTRO cliente
        classification="APTO",
        created_at=base_setup["declaration"].created_at,
    )
    _attempt_and_expect_rejection(db_session, bad_item)


def test_history_mixin_rejects_actor_user_of_another_tenant(db_session, tenant, base_setup) -> None:
    _, other_user = make_other_tenant_with_user(db_session)

    bad = DeclarationStatusHistory(
        tenant_id=tenant.id,
        declaration_id=base_setup["declaration"].id,
        actor_user_id=other_user.id,  # de otro tenant
        created_at=base_setup["declaration"].created_at,
        origin="MANUAL",
        status_field="WORKFLOW",
        from_value="PENDING",
        to_value="DOCUMENTS_REQUESTED",
    )
    _attempt_and_expect_rejection(db_session, bad)


def test_due_date_history_rejects_cross_tenant_historical_rule(db_session, tenant, base_setup) -> None:
    other_tenant, _ = make_other_tenant_with_user(db_session)
    other_source = make_source(db_session, other_tenant.id, base_setup["obligation"].id)
    other_version = make_version(db_session, other_tenant.id, other_source.id)

    bad = DeclarationDueDateHistory(
        tenant_id=tenant.id,
        declaration_id=base_setup["declaration"].id,
        created_at=base_setup["declaration"].created_at,
        origin="RECALCULATION",
        from_original_due_date=date(2026, 2, 12),
        to_original_due_date=date(2026, 2, 13),
        from_effective_due_date=date(2026, 2, 12),
        to_effective_due_date=date(2026, 2, 13),
        from_source_id=other_source.id,  # de otro tenant
        to_source_id=base_setup["source"].id,
        from_version_id=other_version.id,
        to_version_id=base_setup["version"].id,
        from_rule_type="ENTRY",
        to_rule_type="ENTRY",
        from_entry_id=None,
        to_entry_id=uuid.uuid4(),
        new_snapshot_schema_version=1,
        new_schedule_snapshot={},
    )
    _attempt_and_expect_rejection(db_session, bad)
