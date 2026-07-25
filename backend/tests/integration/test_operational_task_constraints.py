"""Integridad a nivel de base de datos de `operational_tasks`/
`task_change_history`/`task_comments` (Fase 2, Bloque 1, Seccion 5 del
plan): intentos de insercion SQL/ORM directa que deben ser rechazados por
las 11 FK compuestas + 7 CHECK de `operational_tasks`, las 4 FK + 8 CHECK de
`task_change_history`, y el CHECK de `task_comments` -- incluso pasando por
alto la capa de servicio, mismo patron que
`test_declarations_multitenant_integrity.py`."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.operational_task import OperationalTask, TaskChangeHistory, TaskComment
from tests.integration._declaration_helpers import make_client, make_other_tenant_with_user
from tests.integration._operational_task_helpers import (
    make_declaration_setup,
    make_document_item,
    make_fee_agreement_and_charge,
    make_second_month_declaration,
    make_tax_payment,
)

_DUE = datetime(2026, 2, 12, 23, 59, 59, tzinfo=UTC)


def _attempt_and_expect_rejection(db_session, obj) -> None:
    savepoint = db_session.begin_nested()
    with pytest.raises(IntegrityError):
        db_session.add(obj)
        db_session.flush()
    savepoint.rollback()


def _manual_task(tenant, admin_user, **overrides) -> OperationalTask:
    kwargs = dict(
        tenant_id=tenant.id,
        title="Tarea manual de prueba",
        task_type="GENERAL",
        priority="NORMAL",
        status="PENDING",
        created_by=admin_user.id,
        due_at=_DUE,
        is_system_generated=False,
    )
    kwargs.update(overrides)
    return OperationalTask(**kwargs)


@pytest.fixture()
def base_setup(db_session, tenant, admin_user, obligation_catalog):
    setup = make_declaration_setup(db_session, tenant, admin_user, obligation_catalog)
    setup["document_item"] = make_document_item(db_session, tenant, setup)
    setup["fee_agreement"], setup["fee_charge"] = make_fee_agreement_and_charge(db_session, tenant, setup)
    setup["tax_payment"] = make_tax_payment(db_session, tenant, setup, admin_user)
    return setup


# --- forma 1: sin ancla, client_id opcional (creacion manual) ---


def test_manual_task_without_any_anchor_succeeds(db_session, tenant, admin_user) -> None:
    task = _manual_task(tenant, admin_user)
    db_session.add(task)
    db_session.flush()
    assert task.id is not None


def test_manual_task_with_only_client_id_succeeds(db_session, tenant, admin_user) -> None:
    client = make_client(db_session, tenant.id)
    task = _manual_task(tenant, admin_user, client_id=client.id)
    db_session.add(task)
    db_session.flush()
    assert task.client_id == client.id


# --- forma 2: declaracion sola ---


def test_declaration_only_task_requires_client_id(db_session, tenant, admin_user, base_setup) -> None:
    bad = _manual_task(
        tenant, admin_user, declaration_id=base_setup["declaration"].id, client_id=None
    )
    _attempt_and_expect_rejection(db_session, bad)


def test_declaration_only_task_rejects_client_of_another_client(
    db_session, tenant, admin_user, base_setup
) -> None:
    other_client = make_client(db_session, tenant.id, name="Otro cliente")
    bad = _manual_task(
        tenant, admin_user, declaration_id=base_setup["declaration"].id, client_id=other_client.id
    )
    _attempt_and_expect_rejection(db_session, bad)


def test_declaration_only_task_rejects_declaration_of_another_tenant(
    db_session, tenant, admin_user, base_setup
) -> None:
    other_tenant, other_user = make_other_tenant_with_user(db_session)
    bad = OperationalTask(
        tenant_id=tenant.id,
        title="Cruce de tenant",
        task_type="GENERAL",
        priority="NORMAL",
        status="PENDING",
        created_by=admin_user.id,
        due_at=_DUE,
        is_system_generated=False,
        declaration_id=base_setup["declaration"].id,
        client_id=base_setup["client"].id,
        assigned_to=other_user.id,  # de otro tenant
    )
    _attempt_and_expect_rejection(db_session, bad)


# --- forma 3: item + declaracion ---


def test_document_item_task_requires_matching_declaration(
    db_session, tenant, admin_user, base_setup
) -> None:
    other_setup = make_second_month_declaration(db_session, tenant, admin_user, base_setup, month=2)
    bad = _manual_task(
        tenant,
        admin_user,
        declaration_document_item_id=base_setup["document_item"].id,
        declaration_id=other_setup["declaration"].id,  # NO es la declaracion real del item
        client_id=base_setup["client"].id,
    )
    _attempt_and_expect_rejection(db_session, bad)


def test_document_item_task_succeeds_with_matching_declaration(
    db_session, tenant, admin_user, base_setup
) -> None:
    task = _manual_task(
        tenant,
        admin_user,
        declaration_document_item_id=base_setup["document_item"].id,
        declaration_id=base_setup["declaration"].id,
        client_id=base_setup["client"].id,
    )
    db_session.add(task)
    db_session.flush()
    assert task.id is not None


# --- forma 4: honorario (cadena charge -> agreement -> client) ---


def test_fee_task_requires_charge_and_agreement_together(
    db_session, tenant, admin_user, base_setup
) -> None:
    bad = _manual_task(
        tenant,
        admin_user,
        professional_fee_charge_id=base_setup["fee_charge"].id,
        professional_fee_agreement_id=None,  # falta el par
        client_id=base_setup["client"].id,
    )
    _attempt_and_expect_rejection(db_session, bad)


def test_fee_task_rejects_charge_not_belonging_to_agreement(
    db_session, tenant, admin_user, base_setup
) -> None:
    other_agreement, _other_charge = make_fee_agreement_and_charge(db_session, tenant, base_setup)
    bad = _manual_task(
        tenant,
        admin_user,
        professional_fee_charge_id=base_setup["fee_charge"].id,  # pertenece al acuerdo original
        professional_fee_agreement_id=other_agreement.id,  # NO es su acuerdo real
        client_id=base_setup["client"].id,
    )
    _attempt_and_expect_rejection(db_session, bad)


def test_fee_task_rejects_agreement_not_belonging_to_client(
    db_session, tenant, admin_user, base_setup
) -> None:
    other_client = make_client(db_session, tenant.id, name="Otro cliente honorarios")
    bad = _manual_task(
        tenant,
        admin_user,
        professional_fee_charge_id=base_setup["fee_charge"].id,
        professional_fee_agreement_id=base_setup["fee_agreement"].id,
        client_id=other_client.id,  # NO es el cliente real del acuerdo
    )
    _attempt_and_expect_rejection(db_session, bad)


def test_fee_task_succeeds_with_full_valid_chain(db_session, tenant, admin_user, base_setup) -> None:
    task = _manual_task(
        tenant,
        admin_user,
        professional_fee_charge_id=base_setup["fee_charge"].id,
        professional_fee_agreement_id=base_setup["fee_agreement"].id,
        client_id=base_setup["client"].id,
    )
    db_session.add(task)
    db_session.flush()
    assert task.id is not None


# --- forma 5: pago + declaracion ---


def test_tax_payment_task_requires_matching_declaration(
    db_session, tenant, admin_user, base_setup
) -> None:
    other_setup = make_second_month_declaration(db_session, tenant, admin_user, base_setup, month=3)
    bad = _manual_task(
        tenant,
        admin_user,
        tax_payment_id=base_setup["tax_payment"].id,
        declaration_id=other_setup["declaration"].id,
        client_id=base_setup["client"].id,
    )
    _attempt_and_expect_rejection(db_session, bad)


# --- multiples anclas incompatibles / formas cruzadas ---


def test_task_rejects_multiple_incompatible_anchors(db_session, tenant, admin_user, base_setup) -> None:
    bad = _manual_task(
        tenant,
        admin_user,
        declaration_id=base_setup["declaration"].id,
        professional_fee_charge_id=base_setup["fee_charge"].id,
        professional_fee_agreement_id=base_setup["fee_agreement"].id,
        client_id=base_setup["client"].id,
    )
    _attempt_and_expect_rejection(db_session, bad)


# --- generacion: coherencia is_system_generated / generation_key / created_by ---


def test_manual_task_rejects_generation_key_present(db_session, tenant, admin_user) -> None:
    bad = _manual_task(tenant, admin_user, generation_key="DECL_FILING:whatever")
    _attempt_and_expect_rejection(db_session, bad)


def test_manual_task_requires_created_by(db_session, tenant, admin_user) -> None:
    bad = _manual_task(tenant, admin_user, created_by=None)
    _attempt_and_expect_rejection(db_session, bad)


# --- source_due_coherence ---


def test_manual_task_rejects_source_due_at_present(db_session, tenant, admin_user) -> None:
    bad = _manual_task(tenant, admin_user, source_due_at=_DUE)
    _attempt_and_expect_rejection(db_session, bad)


def test_manual_task_rejects_due_is_overridden_true(db_session, tenant, admin_user) -> None:
    bad = _manual_task(tenant, admin_user, due_is_overridden=True)
    _attempt_and_expect_rejection(db_session, bad)


# --- status_resolution_coherence ---


def test_completed_task_requires_completed_at_and_by(db_session, tenant, admin_user) -> None:
    bad = _manual_task(
        tenant, admin_user, status="COMPLETED", completed_at=None, resolution_origin="MANUAL"
    )
    _attempt_and_expect_rejection(db_session, bad)


def test_cancelled_task_requires_reason(db_session, tenant, admin_user) -> None:
    bad = _manual_task(
        tenant,
        admin_user,
        status="CANCELLED",
        cancelled_at=datetime.now(UTC),
        cancelled_by=admin_user.id,
        cancellation_reason=None,
        resolution_origin="MANUAL",
    )
    _attempt_and_expect_rejection(db_session, bad)


# --- starts_before_due ---


def test_starts_at_after_due_at_rejected(db_session, tenant, admin_user) -> None:
    bad = _manual_task(tenant, admin_user, starts_at=datetime(2026, 2, 13, tzinfo=UTC))
    _attempt_and_expect_rejection(db_session, bad)


def test_starts_at_before_due_at_succeeds(db_session, tenant, admin_user) -> None:
    task = _manual_task(tenant, admin_user, starts_at=datetime(2026, 2, 10, tzinfo=UTC))
    db_session.add(task)
    db_session.flush()
    assert task.id is not None


# --- task_change_history ---


def _valid_task(db_session, tenant, admin_user) -> OperationalTask:
    task = _manual_task(tenant, admin_user)
    db_session.add(task)
    db_session.flush()
    return task


def test_task_change_history_rejects_cross_tenant_from_assigned_to(
    db_session, tenant, admin_user
) -> None:
    task = _valid_task(db_session, tenant, admin_user)
    _, other_user = make_other_tenant_with_user(db_session)
    bad = TaskChangeHistory(
        tenant_id=tenant.id,
        task_id=task.id,
        actor_user_id=admin_user.id,
        created_at=datetime.now(UTC),
        origin="MANUAL",
        event_type="ASSIGNED",
        from_assigned_to=other_user.id,  # de otro tenant
        to_assigned_to=admin_user.id,
    )
    _attempt_and_expect_rejection(db_session, bad)


def test_task_change_history_assigned_requires_real_change(db_session, tenant, admin_user) -> None:
    task = _valid_task(db_session, tenant, admin_user)
    bad = TaskChangeHistory(
        tenant_id=tenant.id,
        task_id=task.id,
        actor_user_id=admin_user.id,
        created_at=datetime.now(UTC),
        origin="MANUAL",
        event_type="ASSIGNED",
        from_assigned_to=admin_user.id,
        to_assigned_to=admin_user.id,  # sin cambio real
    )
    _attempt_and_expect_rejection(db_session, bad)


def test_task_change_history_origin_system_requires_null_actor(db_session, tenant, admin_user) -> None:
    task = _valid_task(db_session, tenant, admin_user)
    bad = TaskChangeHistory(
        tenant_id=tenant.id,
        task_id=task.id,
        actor_user_id=admin_user.id,  # origin SYSTEM exige actor NULL
        created_at=datetime.now(UTC),
        origin="SYSTEM",
        event_type="SOURCE_DUE_UPDATED",
        from_source_due_at=_DUE,
        to_source_due_at=datetime(2026, 2, 13, tzinfo=UTC),
    )
    _attempt_and_expect_rejection(db_session, bad)


def test_task_change_history_created_event_succeeds(db_session, tenant, admin_user) -> None:
    task = _valid_task(db_session, tenant, admin_user)
    history = TaskChangeHistory(
        tenant_id=tenant.id,
        task_id=task.id,
        actor_user_id=admin_user.id,
        created_at=datetime.now(UTC),
        origin="MANUAL",
        event_type="CREATED",
        to_status="PENDING",
        to_due_at=_DUE,
        to_priority="NORMAL",
    )
    db_session.add(history)
    db_session.flush()
    assert history.id is not None


# --- task_comments ---


def test_task_comment_rejects_empty_body(db_session, tenant, admin_user) -> None:
    task = _valid_task(db_session, tenant, admin_user)
    bad = TaskComment(
        tenant_id=tenant.id,
        task_id=task.id,
        author_user_id=admin_user.id,
        body="   ",
        created_at=datetime.now(UTC),
    )
    _attempt_and_expect_rejection(db_session, bad)


def test_task_comment_rejects_body_over_4000_chars(db_session, tenant, admin_user) -> None:
    task = _valid_task(db_session, tenant, admin_user)
    bad = TaskComment(
        tenant_id=tenant.id,
        task_id=task.id,
        author_user_id=admin_user.id,
        body="x" * 4001,
        created_at=datetime.now(UTC),
    )
    _attempt_and_expect_rejection(db_session, bad)


def test_task_comment_valid_body_succeeds(db_session, tenant, admin_user) -> None:
    task = _valid_task(db_session, tenant, admin_user)
    comment = TaskComment(
        tenant_id=tenant.id,
        task_id=task.id,
        author_user_id=admin_user.id,
        body="Comentario valido de prueba.",
        created_at=datetime.now(UTC),
    )
    db_session.add(comment)
    db_session.flush()
    assert comment.id is not None
