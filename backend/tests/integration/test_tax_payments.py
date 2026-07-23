"""Pagos tributarios (Bloque 3.2, Dominio B): ver "Pagos tributarios" /
"Concurrencia financiera" del plan y ADR-0041 a ADR-0043. Cubre: pago
parcial/total, idempotencia (reenvio identico vs payload distinto),
sobrepago, anulacion, historial de nacimiento y anulacion, y el rollback
atomico si la escritura de historial falla despues de insertar el pago."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.tax_payment import TaxPayment, TaxPaymentHistory
from app.repositories import tax_payment_repository
from app.services import declaration_service, tax_assessment_service, tax_payment_service
from tests.integration._declaration_helpers import (
    make_client,
    make_client_obligation,
    make_entry,
    make_monthly_period,
    make_source,
    make_version,
)


@pytest.fixture()
def declaration_with_assessment(db_session, tenant, admin_user, obligation_catalog):
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
    locked = tax_assessment_service.lock_declaration(db_session, tenant.id, declaration.id)
    tax_assessment_service.create_initial_assessment(
        db_session,
        tenant.id,
        locked,
        gross_tax_amount=Decimal("1000"),
        credits_applied=Decimal("0"),
        observations=None,
        actor_user_id=admin_user.id,
    )
    return locked


def _pay(db_session, tenant, declaration, admin_user, *, amount, idempotency_key=None, **overrides):
    kwargs = {
        "idempotency_key": idempotency_key or uuid.uuid4(),
        "amount": amount,
        "paid_at": date(2026, 2, 1),
        "payment_method": "TRANSFER",
        "operation_number": None,
        "reference": None,
        "observations": None,
        "overpayment_confirmed": False,
        "overpayment_reason": None,
        "actor_user_id": admin_user.id,
    }
    kwargs.update(overrides)
    return tax_payment_service.create_payment(db_session, tenant.id, declaration, **kwargs)


def test_partial_payment_sets_partially_paid(
    db_session, tenant, admin_user, declaration_with_assessment
) -> None:
    _pay(db_session, tenant, declaration_with_assessment, admin_user, amount=Decimal("400"))
    assert declaration_with_assessment.tax_payment_status == "PARTIALLY_PAID"


def test_full_payment_sets_paid(db_session, tenant, admin_user, declaration_with_assessment) -> None:
    _pay(db_session, tenant, declaration_with_assessment, admin_user, amount=Decimal("1000"))
    assert declaration_with_assessment.tax_payment_status == "PAID"


def test_payment_writes_birth_history_row(
    db_session, tenant, admin_user, declaration_with_assessment
) -> None:
    payment = _pay(db_session, tenant, declaration_with_assessment, admin_user, amount=Decimal("400"))
    history = db_session.execute(
        select(TaxPaymentHistory).where(TaxPaymentHistory.payment_id == payment.id)
    ).scalars().all()
    assert len(history) == 1
    assert history[0].from_status is None
    assert history[0].to_status == "ACTIVE"


def test_idempotent_resend_returns_same_payment(
    db_session, tenant, admin_user, declaration_with_assessment
) -> None:
    key = uuid.uuid4()
    first = _pay(
        db_session,
        tenant,
        declaration_with_assessment,
        admin_user,
        amount=Decimal("400"),
        idempotency_key=key,
    )
    second = _pay(
        db_session,
        tenant,
        declaration_with_assessment,
        admin_user,
        amount=Decimal("400"),
        idempotency_key=key,
    )
    assert first.id == second.id

    all_payments = tax_payment_repository.list_for_declaration(
        db_session, tenant.id, declaration_with_assessment.id
    )
    assert len(all_payments) == 1


def test_idempotency_key_with_different_payload_returns_409(
    db_session, tenant, admin_user, declaration_with_assessment
) -> None:
    key = uuid.uuid4()
    _pay(
        db_session,
        tenant,
        declaration_with_assessment,
        admin_user,
        amount=Decimal("400"),
        idempotency_key=key,
    )
    with pytest.raises(tax_payment_service.IdempotencyKeyConflictError):
        _pay(
            db_session,
            tenant,
            declaration_with_assessment,
            admin_user,
            amount=Decimal("500"),
            idempotency_key=key,
        )


def test_overpayment_requires_confirmation(
    db_session, tenant, admin_user, declaration_with_assessment
) -> None:
    with pytest.raises(tax_payment_service.OverpaymentConfirmationRequiredError):
        _pay(db_session, tenant, declaration_with_assessment, admin_user, amount=Decimal("1500"))


def test_overpayment_requires_reason_even_if_confirmed(
    db_session, tenant, admin_user, declaration_with_assessment
) -> None:
    with pytest.raises(tax_payment_service.MissingOverpaymentReasonError):
        _pay(
            db_session,
            tenant,
            declaration_with_assessment,
            admin_user,
            amount=Decimal("1500"),
            overpayment_confirmed=True,
            overpayment_reason="   ",
        )


def test_overpayment_confirmed_with_reason_succeeds(
    db_session, tenant, admin_user, declaration_with_assessment
) -> None:
    payment = _pay(
        db_session,
        tenant,
        declaration_with_assessment,
        admin_user,
        amount=Decimal("1500"),
        overpayment_confirmed=True,
        overpayment_reason="Cliente deposito de mas por error",
    )
    assert payment.overpayment_confirmed is True
    assert payment.overpayment_amount == Decimal("500")
    assert payment.confirmed_by == admin_user.id


def test_void_payment_writes_history_and_recalculates_status(
    db_session, tenant, admin_user, declaration_with_assessment
) -> None:
    payment = _pay(db_session, tenant, declaration_with_assessment, admin_user, amount=Decimal("1000"))
    assert declaration_with_assessment.tax_payment_status == "PAID"

    voided = tax_payment_service.void_payment(
        db_session,
        tenant.id,
        declaration_with_assessment,
        payment.id,
        reason="Pago registrado por error",
        actor_user_id=admin_user.id,
    )
    assert voided.is_voided is True
    assert declaration_with_assessment.tax_payment_status == "PAYMENT_PENDING"

    history = db_session.execute(
        select(TaxPaymentHistory).where(TaxPaymentHistory.payment_id == payment.id)
    ).scalars().all()
    assert len(history) == 2
    assert history[-1].from_status == "ACTIVE"
    assert history[-1].to_status == "VOIDED"


def test_void_already_voided_payment_rejected(
    db_session, tenant, admin_user, declaration_with_assessment
) -> None:
    payment = _pay(db_session, tenant, declaration_with_assessment, admin_user, amount=Decimal("1000"))
    tax_payment_service.void_payment(
        db_session,
        tenant.id,
        declaration_with_assessment,
        payment.id,
        reason="Error",
        actor_user_id=admin_user.id,
    )
    with pytest.raises(tax_payment_service.PaymentAlreadyVoidedError):
        tax_payment_service.void_payment(
            db_session,
            tenant.id,
            declaration_with_assessment,
            payment.id,
            reason="Otro intento",
            actor_user_id=admin_user.id,
        )


def test_void_without_reason_rejected(
    db_session, tenant, admin_user, declaration_with_assessment
) -> None:
    payment = _pay(db_session, tenant, declaration_with_assessment, admin_user, amount=Decimal("1000"))
    with pytest.raises(tax_payment_service.MissingVoidReasonError):
        tax_payment_service.void_payment(
            db_session,
            tenant.id,
            declaration_with_assessment,
            payment.id,
            reason="",
            actor_user_id=admin_user.id,
        )


def test_payment_without_current_assessment_rejected(
    db_session, tenant, admin_user, obligation_catalog
) -> None:
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
    with pytest.raises(tax_payment_service.NoCurrentAssessmentError):
        _pay(db_session, tenant, declaration, admin_user, amount=Decimal("100"))


def test_idempotency_key_unique_index_rejects_direct_duplicate(
    db_session, tenant, admin_user, declaration_with_assessment
) -> None:
    payment = _pay(db_session, tenant, declaration_with_assessment, admin_user, amount=Decimal("100"))

    duplicate = TaxPayment(
        tenant_id=tenant.id,
        declaration_id=declaration_with_assessment.id,
        assessment_id=payment.assessment_id,
        paid_at=date(2026, 2, 2),
        amount=Decimal("50"),
        payment_method="TRANSFER",
        created_by=admin_user.id,
        idempotency_key=payment.idempotency_key,
    )
    savepoint = db_session.begin_nested()
    with pytest.raises(IntegrityError):
        db_session.add(duplicate)
        db_session.flush()
    savepoint.rollback()


def test_atomic_rollback_if_history_write_fails_after_payment_insert(
    db_session, tenant, admin_user, declaration_with_assessment, monkeypatch
) -> None:
    """Simula el fallo mandado por la seccion 4 del plan: forzar un fallo al
    escribir el historial DESPUES de haber hecho add()/flush() del pago. El
    router debe hacer rollback completo -- cero filas nuevas en
    tax_payments/tax_payment_history."""

    def _boom(*args, **kwargs):
        raise RuntimeError("fallo forzado al escribir el historial")

    monkeypatch.setattr(tax_payment_repository, "create_history", _boom)

    savepoint = db_session.begin_nested()
    with pytest.raises(RuntimeError):
        _pay(db_session, tenant, declaration_with_assessment, admin_user, amount=Decimal("100"))
    savepoint.rollback()

    payments = tax_payment_repository.list_for_declaration(
        db_session, tenant.id, declaration_with_assessment.id
    )
    assert payments == []
    history = db_session.execute(
        select(TaxPaymentHistory).where(TaxPaymentHistory.tenant_id == tenant.id)
    ).scalars().all()
    assert history == []
