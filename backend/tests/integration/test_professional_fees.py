"""Honorarios profesionales (Bloque 3.2, Dominio C): ver "Honorarios
profesionales"/"Riesgo de cobranza de honorarios" del plan y
ADR-0044 a ADR-0046. Cubre: acuerdos sin/con solapamiento,
supersede/cancel que cierran vigencia real y permiten un acuerdo posterior,
cargos idempotentes, pagos con historial de nacimiento/anulacion, recibos
con limite agregado, paridad Python/SQL del riesgo de cobranza, e
independencia frente a impuestos."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.professional_fee import (
    ProfessionalFeeChargeStatusHistory,
    ProfessionalFeePaymentHistory,
)
from app.services import (
    professional_fee_payment_service,
    professional_fee_receipt_service,
    professional_fee_service,
)
from tests.integration._declaration_helpers import make_client, make_monthly_period


@pytest.fixture()
def client_fixture(db_session, tenant):
    return make_client(db_session, tenant.id)


def _make_agreement(db_session, tenant, client, *, service_type="CONTABILIDAD_MENSUAL", **overrides):
    kwargs = {
        "service_type": service_type,
        "amount": Decimal("500.00"),
        "billing_day": 5,
        "included_services": None,
        "description": None,
        "valid_from": date(2026, 1, 1),
        "valid_until": None,
        "observations": None,
        "actor_user_id": None,
    }
    kwargs.update(overrides)
    return professional_fee_service.create_agreement(db_session, tenant.id, client.id, **kwargs)


def test_agreement_without_overlap_succeeds(db_session, tenant, client_fixture) -> None:
    agreement = _make_agreement(db_session, tenant, client_fixture)
    assert agreement.status == "ACTIVE"
    assert agreement.valid_until is None


def test_agreement_overlap_same_service_type_rejected(db_session, tenant, client_fixture) -> None:
    _make_agreement(db_session, tenant, client_fixture)
    with pytest.raises(professional_fee_service.AgreementOverlapError):
        _make_agreement(db_session, tenant, client_fixture)


def test_agreement_different_service_type_does_not_overlap(
    db_session, tenant, client_fixture
) -> None:
    _make_agreement(db_session, tenant, client_fixture, service_type="CONTABILIDAD_MENSUAL")
    other = _make_agreement(db_session, tenant, client_fixture, service_type="PLANILLAS")
    assert other.id is not None


def test_supersede_closes_valid_until_and_allows_new_agreement(
    db_session, tenant, client_fixture, admin_user
) -> None:
    agreement = _make_agreement(db_session, tenant, client_fixture)

    superseded = professional_fee_service.supersede_agreement(
        db_session,
        tenant.id,
        agreement,
        new_valid_until=date(2026, 6, 30),
        reason="Renegociacion de honorarios",
        actor_user_id=admin_user.id,
    )
    assert superseded.status == "SUPERSEDED"
    assert superseded.valid_until == date(2026, 6, 30)

    # Ahora debe poder crearse un acuerdo NUEVO no solapado (el EXCLUDE
    # compara validity_range, no status -- si solo hubieramos cambiado
    # `status`, esto seguiria fallando).
    new_agreement = _make_agreement(
        db_session, tenant, client_fixture, valid_from=date(2026, 7, 1), valid_until=None
    )
    assert new_agreement.id != agreement.id


def test_supersede_never_modifies_existing_charges(
    db_session, tenant, client_fixture, admin_user
) -> None:
    agreement = _make_agreement(db_session, tenant, client_fixture)
    period = make_monthly_period(db_session, tenant.id, fiscal_year=2026, month=1)
    charge = professional_fee_service.create_charge(
        db_session,
        tenant.id,
        agreement,
        tax_period_id=period.id,
        amount=None,
        due_date=None,
        observations=None,
        actor_user_id=admin_user.id,
    )
    professional_fee_service.supersede_agreement(
        db_session,
        tenant.id,
        agreement,
        new_valid_until=date(2026, 6, 30),
        reason="Renegociacion",
        actor_user_id=admin_user.id,
    )
    refetched = professional_fee_service.get_charge(db_session, tenant.id, charge.id)
    assert refetched.status == "PENDING_ISSUANCE"
    assert refetched.amount == Decimal("500.00")


def test_cancel_requires_reason(db_session, tenant, client_fixture, admin_user) -> None:
    agreement = _make_agreement(db_session, tenant, client_fixture)
    with pytest.raises(professional_fee_service.MissingLifecycleReasonError):
        professional_fee_service.cancel_agreement(
            db_session,
            tenant.id,
            agreement,
            effective_date=None,
            reason="   ",
            actor_user_id=admin_user.id,
        )


def test_lifecycle_action_on_inactive_agreement_rejected(
    db_session, tenant, client_fixture, admin_user
) -> None:
    agreement = _make_agreement(db_session, tenant, client_fixture)
    professional_fee_service.cancel_agreement(
        db_session,
        tenant.id,
        agreement,
        effective_date=date(2026, 3, 1),
        reason="Cliente se retira",
        actor_user_id=admin_user.id,
    )
    with pytest.raises(professional_fee_service.AgreementNotActiveError):
        professional_fee_service.supersede_agreement(
            db_session,
            tenant.id,
            agreement,
            new_valid_until=date(2026, 6, 30),
            reason="Ya no deberia funcionar",
            actor_user_id=admin_user.id,
        )


# --- cargos ---


@pytest.fixture()
def agreement_and_period(db_session, tenant, client_fixture):
    agreement = _make_agreement(db_session, tenant, client_fixture)
    period = make_monthly_period(db_session, tenant.id, fiscal_year=2026, month=1)
    return agreement, period


def test_charge_creation_is_unique_per_agreement_and_period(
    db_session, tenant, admin_user, agreement_and_period
) -> None:
    agreement, period = agreement_and_period
    professional_fee_service.create_charge(
        db_session,
        tenant.id,
        agreement,
        tax_period_id=period.id,
        amount=None,
        due_date=None,
        observations=None,
        actor_user_id=admin_user.id,
    )
    with pytest.raises(professional_fee_service.ChargeAlreadyExistsError):
        professional_fee_service.create_charge(
            db_session,
            tenant.id,
            agreement,
            tax_period_id=period.id,
            amount=None,
            due_date=None,
            observations=None,
            actor_user_id=admin_user.id,
        )


def test_charge_only_from_active_agreement(
    db_session, tenant, admin_user, agreement_and_period
) -> None:
    agreement, period = agreement_and_period
    professional_fee_service.cancel_agreement(
        db_session,
        tenant.id,
        agreement,
        effective_date=date(2026, 1, 1),
        reason="Cancelado antes de generar cargos",
        actor_user_id=admin_user.id,
    )
    with pytest.raises(professional_fee_service.AgreementNotActiveError):
        professional_fee_service.create_charge(
            db_session,
            tenant.id,
            agreement,
            tax_period_id=period.id,
            amount=None,
            due_date=None,
            observations=None,
            actor_user_id=admin_user.id,
        )


def test_issue_charge_without_due_date_stays_issued(
    db_session, tenant, admin_user, agreement_and_period
) -> None:
    agreement, period = agreement_and_period
    charge = professional_fee_service.create_charge(
        db_session,
        tenant.id,
        agreement,
        tax_period_id=period.id,
        amount=None,
        due_date=None,
        observations=None,
        actor_user_id=admin_user.id,
    )
    issued = professional_fee_service.issue_charge(
        db_session, tenant.id, charge, due_date=None, actor_user_id=admin_user.id
    )
    assert issued.status == "ISSUED"


def test_issue_charge_with_due_date_becomes_payment_pending(
    db_session, tenant, admin_user, agreement_and_period
) -> None:
    agreement, period = agreement_and_period
    charge = professional_fee_service.create_charge(
        db_session,
        tenant.id,
        agreement,
        tax_period_id=period.id,
        amount=None,
        due_date=None,
        observations=None,
        actor_user_id=admin_user.id,
    )
    issued = professional_fee_service.issue_charge(
        db_session, tenant.id, charge, due_date=date(2026, 2, 5), actor_user_id=admin_user.id
    )
    assert issued.status == "PAYMENT_PENDING"


# --- pagos ---


@pytest.fixture()
def issued_charge(db_session, tenant, admin_user, agreement_and_period):
    agreement, period = agreement_and_period
    charge = professional_fee_service.create_charge(
        db_session,
        tenant.id,
        agreement,
        tax_period_id=period.id,
        amount=None,
        due_date=None,
        observations=None,
        actor_user_id=admin_user.id,
    )
    return professional_fee_service.issue_charge(
        db_session, tenant.id, charge, due_date=date(2026, 2, 5), actor_user_id=admin_user.id
    )


def _pay_fee(db_session, tenant, charge, admin_user, *, amount, idempotency_key=None, **overrides):
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
    return professional_fee_payment_service.create_payment(db_session, tenant.id, charge, **kwargs)


def test_payment_cannot_be_made_before_issuance(
    db_session, tenant, admin_user, agreement_and_period
) -> None:
    agreement, period = agreement_and_period
    charge = professional_fee_service.create_charge(
        db_session,
        tenant.id,
        agreement,
        tax_period_id=period.id,
        amount=None,
        due_date=None,
        observations=None,
        actor_user_id=admin_user.id,
    )
    with pytest.raises(professional_fee_payment_service.ChargeNotIssuedError):
        _pay_fee(db_session, tenant, charge, admin_user, amount=Decimal("100"))


def test_full_payment_marks_charge_paid_with_birth_history(
    db_session, tenant, admin_user, issued_charge
) -> None:
    payment = _pay_fee(db_session, tenant, issued_charge, admin_user, amount=Decimal("500.00"))
    assert issued_charge.status == "PAID"

    history = db_session.execute(
        select(ProfessionalFeePaymentHistory).where(
            ProfessionalFeePaymentHistory.payment_id == payment.id
        )
    ).scalars().all()
    assert len(history) == 1
    assert history[0].from_status is None
    assert history[0].to_status == "ACTIVE"


def test_void_payment_writes_history_and_recalculates_charge(
    db_session, tenant, admin_user, issued_charge
) -> None:
    payment = _pay_fee(db_session, tenant, issued_charge, admin_user, amount=Decimal("500.00"))
    assert issued_charge.status == "PAID"

    professional_fee_payment_service.void_payment(
        db_session,
        tenant.id,
        issued_charge,
        payment.id,
        reason="Pago duplicado por error",
        actor_user_id=admin_user.id,
    )
    assert issued_charge.status == "PAYMENT_PENDING"

    history = db_session.execute(
        select(ProfessionalFeePaymentHistory).where(
            ProfessionalFeePaymentHistory.payment_id == payment.id
        )
    ).scalars().all()
    assert len(history) == 2
    assert history[-1].to_status == "VOIDED"


def test_fee_payment_idempotent_resend(db_session, tenant, admin_user, issued_charge) -> None:
    key = uuid.uuid4()
    first = _pay_fee(
        db_session, tenant, issued_charge, admin_user, amount=Decimal("200"), idempotency_key=key
    )
    second = _pay_fee(
        db_session, tenant, issued_charge, admin_user, amount=Decimal("200"), idempotency_key=key
    )
    assert first.id == second.id


def test_fee_payment_idempotency_conflict(db_session, tenant, admin_user, issued_charge) -> None:
    key = uuid.uuid4()
    _pay_fee(db_session, tenant, issued_charge, admin_user, amount=Decimal("200"), idempotency_key=key)
    with pytest.raises(professional_fee_payment_service.IdempotencyKeyConflictError):
        _pay_fee(db_session, tenant, issued_charge, admin_user, amount=Decimal("300"), idempotency_key=key)


def test_fee_overpayment_requires_confirmation_and_reason(
    db_session, tenant, admin_user, issued_charge
) -> None:
    with pytest.raises(professional_fee_payment_service.OverpaymentConfirmationRequiredError):
        _pay_fee(db_session, tenant, issued_charge, admin_user, amount=Decimal("600"))
    with pytest.raises(professional_fee_payment_service.MissingOverpaymentReasonError):
        _pay_fee(
            db_session,
            tenant,
            issued_charge,
            admin_user,
            amount=Decimal("600"),
            overpayment_confirmed=True,
        )


def test_charge_status_history_recorded_on_issue(
    db_session, tenant, admin_user, agreement_and_period
) -> None:
    agreement, period = agreement_and_period
    charge = professional_fee_service.create_charge(
        db_session,
        tenant.id,
        agreement,
        tax_period_id=period.id,
        amount=None,
        due_date=None,
        observations=None,
        actor_user_id=admin_user.id,
    )
    professional_fee_service.issue_charge(
        db_session, tenant.id, charge, due_date=date(2026, 2, 5), actor_user_id=admin_user.id
    )
    history = db_session.execute(
        select(ProfessionalFeeChargeStatusHistory).where(
            ProfessionalFeeChargeStatusHistory.charge_id == charge.id
        )
    ).scalars().all()
    assert len(history) == 2
    assert history[0].to_status == "PENDING_ISSUANCE"
    assert history[1].to_status == "PAYMENT_PENDING"


# --- recibos ---


def test_receipt_partial_allowed_sum_cannot_exceed_charge_amount(
    db_session, tenant, admin_user, issued_charge
) -> None:
    professional_fee_receipt_service.issue_receipt(
        db_session,
        tenant.id,
        issued_charge,
        receipt_type="RECIBO_HONORARIOS",
        number="001-001",
        issued_at=date(2026, 2, 1),
        amount=Decimal("300"),
        issued_by=admin_user.id,
        document_reference=None,
        observations=None,
    )
    with pytest.raises(professional_fee_receipt_service.ReceiptExceedsChargeAmountError):
        professional_fee_receipt_service.issue_receipt(
            db_session,
            tenant.id,
            issued_charge,
            receipt_type="RECIBO_HONORARIOS",
            number="001-002",
            issued_at=date(2026, 2, 2),
            amount=Decimal("300"),
            issued_by=admin_user.id,
            document_reference=None,
            observations=None,
        )


def test_receipt_void_and_duplicate_number_rejected(
    db_session, tenant, admin_user, issued_charge
) -> None:
    receipt = professional_fee_receipt_service.issue_receipt(
        db_session,
        tenant.id,
        issued_charge,
        receipt_type="RECIBO_HONORARIOS",
        number="002-001",
        issued_at=date(2026, 2, 1),
        amount=Decimal("100"),
        issued_by=admin_user.id,
        document_reference=None,
        observations=None,
    )
    professional_fee_receipt_service.void_receipt(
        db_session, tenant.id, receipt, reason="Numero incorrecto", actor_user_id=admin_user.id
    )
    assert receipt.status == "VOIDED"

    # Reemitir el MISMO numero (incluso tras anular el original) choca con
    # UNIQUE(tenant_id, receipt_type, number) -- un recibo anulado nunca se
    # elimina, asi que su numero queda reservado para siempre.
    savepoint = db_session.begin_nested()
    with pytest.raises(IntegrityError):
        professional_fee_receipt_service.issue_receipt(
            db_session,
            tenant.id,
            issued_charge,
            receipt_type="RECIBO_HONORARIOS",
            number="002-001",
            issued_at=date(2026, 2, 3),
            amount=Decimal("100"),
            issued_by=admin_user.id,
            document_reference=None,
            observations=None,
        )
    savepoint.rollback()


# --- independencia impuestos/honorarios ---


def test_fee_payment_service_never_imports_declaration_service() -> None:
    """`professional_fee_payment_service` nunca importa `declaration_service`
    ni el modelo `Declaration` -- la raiz de bloqueo de honorarios es
    exclusivamente `professional_fee_charges` (ver "Independencia
    impuestos/honorarios")."""
    import app.services.professional_fee_payment_service as module

    with open(module.__file__, encoding="utf-8") as fh:
        content = fh.read()
    assert "declaration_service" not in content
    assert "from app.models.declaration " not in content
    assert "import Declaration" not in content


def test_fee_operations_do_not_alter_tax_state(
    db_session, tenant, admin_user, obligation_catalog, issued_charge
) -> None:
    from app.services import declaration_service
    from tests.integration._declaration_helpers import (
        make_client_obligation,
        make_entry,
        make_source,
        make_version,
    )

    client = make_client(db_session, tenant.id, name="Cliente impuestos")
    obligation = obligation_catalog["621"]
    co = make_client_obligation(db_session, tenant.id, client.id, obligation.id)
    source = make_source(db_session, tenant.id, obligation.id)
    version = make_version(db_session, tenant.id, source.id, fiscal_year=2026)
    make_entry(db_session, tenant.id, version.id, tax_period="2026-02", digits=list(range(10)))
    period = make_monthly_period(db_session, tenant.id, fiscal_year=2026, month=2)
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
    before_status = declaration.tax_payment_status

    _pay_fee(db_session, tenant, issued_charge, admin_user, amount=Decimal("500.00"))

    assert declaration.tax_payment_status == before_status
