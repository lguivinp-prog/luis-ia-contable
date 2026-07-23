"""Determinacion tributaria e historial (Bloque 3.2, Dominio B): ver
"Determinacion tributaria"/"Correcciones e historial" del plan y
ADR-0038/0039. Cubre: determinacion inicial en una transaccion (4
filas), correcciones que aumentan/reducen/generan exceso, la regla de "no
escribir historial de estado si no cambio", y el UNIQUE(sequence_number)."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.audit_log import AuditLog
from app.models.declaration import DeclarationStatusHistory
from app.models.tax_assessment import TaxAssessment, TaxAssessmentHistory
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
def declaration_setup(db_session, tenant, admin_user, obligation_catalog):
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
    return declaration


def test_initial_assessment_creates_four_rows_in_one_transaction(
    db_session, tenant, admin_user, declaration_setup
) -> None:
    declaration = tax_assessment_service.lock_declaration(db_session, tenant.id, declaration_setup.id)
    assessment = tax_assessment_service.create_initial_assessment(
        db_session,
        tenant.id,
        declaration,
        gross_tax_amount=Decimal("1000"),
        credits_applied=Decimal("100"),
        observations=None,
        actor_user_id=admin_user.id,
    )

    assert assessment.assessment_type == "ORIGINAL"
    assert assessment.sequence_number == 1
    assert assessment.total_payable == Decimal("900")
    assert assessment.remaining_credit == Decimal("0")
    assert assessment.is_current is True
    assert declaration.tax_payment_status == "PAYMENT_PENDING"

    history = db_session.execute(
        select(TaxAssessmentHistory).where(TaxAssessmentHistory.assessment_id == assessment.id)
    ).scalars().all()
    assert len(history) == 1
    assert history[0].previous_assessment_snapshot is None
    assert history[0].new_assessment_snapshot["total_payable"] == "900"

    status_history = db_session.execute(
        select(DeclarationStatusHistory).where(
            DeclarationStatusHistory.declaration_id == declaration.id,
            DeclarationStatusHistory.status_field == "TAX_PAYMENT",
        )
    ).scalars().all()
    # 1 fila de nacimiento (creacion de declaracion, NULL->NOT_DETERMINED) +
    # 1 fila de esta determinacion (NOT_DETERMINED->PAYMENT_PENDING).
    assert len(status_history) == 2
    assert status_history[-1].from_value == "NOT_DETERMINED"
    assert status_history[-1].to_value == "PAYMENT_PENDING"


def test_second_initial_assessment_rejected(db_session, tenant, admin_user, declaration_setup) -> None:
    declaration = tax_assessment_service.lock_declaration(db_session, tenant.id, declaration_setup.id)
    tax_assessment_service.create_initial_assessment(
        db_session,
        tenant.id,
        declaration,
        gross_tax_amount=Decimal("1000"),
        credits_applied=Decimal("0"),
        observations=None,
        actor_user_id=admin_user.id,
    )
    with pytest.raises(tax_assessment_service.AssessmentAlreadyExistsError):
        tax_assessment_service.create_initial_assessment(
            db_session,
            tenant.id,
            declaration,
            gross_tax_amount=Decimal("500"),
            credits_applied=Decimal("0"),
            observations=None,
            actor_user_id=admin_user.id,
        )


def test_correction_increasing_amount_updates_sequence_and_snapshot(
    db_session, tenant, admin_user, declaration_setup
) -> None:
    declaration = tax_assessment_service.lock_declaration(db_session, tenant.id, declaration_setup.id)
    tax_assessment_service.create_initial_assessment(
        db_session,
        tenant.id,
        declaration,
        gross_tax_amount=Decimal("1000"),
        credits_applied=Decimal("0"),
        observations=None,
        actor_user_id=admin_user.id,
    )

    corrected = tax_assessment_service.correct_assessment(
        db_session,
        tenant.id,
        declaration,
        gross_tax_amount=Decimal("1500"),
        credits_applied=Decimal("0"),
        observations=None,
        reason="Ajuste por ingresos adicionales detectados",
        actor_user_id=admin_user.id,
    )

    assert corrected.assessment_type == "CORRECTION"
    assert corrected.sequence_number == 2
    assert corrected.total_payable == Decimal("1500")
    assert corrected.is_current is True

    all_assessments = tax_assessment_service.list_assessments(db_session, tenant.id, declaration.id)
    assert len(all_assessments) == 2
    assert all_assessments[0].is_current is False
    assert all_assessments[1].is_current is True


def test_correction_generating_overpayment_excess_reflected_in_status(
    db_session, tenant, admin_user, declaration_setup
) -> None:
    declaration = tax_assessment_service.lock_declaration(db_session, tenant.id, declaration_setup.id)
    tax_assessment_service.create_initial_assessment(
        db_session,
        tenant.id,
        declaration,
        gross_tax_amount=Decimal("1000"),
        credits_applied=Decimal("0"),
        observations=None,
        actor_user_id=admin_user.id,
    )

    tax_payment_service.create_payment(
        db_session,
        tenant.id,
        declaration,
        idempotency_key=uuid.uuid4(),
        amount=Decimal("1000"),
        paid_at=date(2026, 2, 1),
        payment_method="TRANSFER",
        operation_number=None,
        reference=None,
        observations=None,
        overpayment_confirmed=False,
        overpayment_reason=None,
        actor_user_id=admin_user.id,
    )
    assert declaration.tax_payment_status == "PAID"

    corrected = tax_assessment_service.correct_assessment(
        db_session,
        tenant.id,
        declaration,
        gross_tax_amount=Decimal("600"),
        credits_applied=Decimal("0"),
        observations=None,
        reason="Correccion que reduce el impuesto tras revision",
        actor_user_id=admin_user.id,
    )
    assert corrected.total_payable == Decimal("600")
    # El pago anterior (1000) nunca se modifica; el exceso queda visible como
    # dato calculado, la declaracion sigue PAID (total_paid >= total_payable).
    assert declaration.tax_payment_status == "PAID"


def test_correction_that_does_not_change_derived_status_writes_no_extra_history(
    db_session, tenant, admin_user, declaration_setup
) -> None:
    declaration = tax_assessment_service.lock_declaration(db_session, tenant.id, declaration_setup.id)
    tax_assessment_service.create_initial_assessment(
        db_session,
        tenant.id,
        declaration,
        gross_tax_amount=Decimal("1000"),
        credits_applied=Decimal("0"),
        observations=None,
        actor_user_id=admin_user.id,
    )
    before = len(
        db_session.execute(
            select(DeclarationStatusHistory).where(
                DeclarationStatusHistory.declaration_id == declaration.id,
                DeclarationStatusHistory.status_field == "TAX_PAYMENT",
            )
        ).scalars().all()
    )

    # No hay pagos todavia; la correccion sigue siendo PAYMENT_PENDING antes y
    # despues -- no debe escribir una fila nueva.
    tax_assessment_service.correct_assessment(
        db_session,
        tenant.id,
        declaration,
        gross_tax_amount=Decimal("2000"),
        credits_applied=Decimal("0"),
        observations=None,
        reason="Correccion que no cambia el estado derivado",
        actor_user_id=admin_user.id,
    )

    after = len(
        db_session.execute(
            select(DeclarationStatusHistory).where(
                DeclarationStatusHistory.declaration_id == declaration.id,
                DeclarationStatusHistory.status_field == "TAX_PAYMENT",
            )
        ).scalars().all()
    )
    assert after == before


def test_correction_without_reason_rejected(db_session, tenant, admin_user, declaration_setup) -> None:
    declaration = tax_assessment_service.lock_declaration(db_session, tenant.id, declaration_setup.id)
    tax_assessment_service.create_initial_assessment(
        db_session,
        tenant.id,
        declaration,
        gross_tax_amount=Decimal("1000"),
        credits_applied=Decimal("0"),
        observations=None,
        actor_user_id=admin_user.id,
    )
    with pytest.raises(tax_assessment_service.MissingCorrectionReasonError):
        tax_assessment_service.correct_assessment(
            db_session,
            tenant.id,
            declaration,
            gross_tax_amount=Decimal("2000"),
            credits_applied=Decimal("0"),
            observations=None,
            reason="   ",
            actor_user_id=admin_user.id,
        )


def test_correction_without_current_assessment_rejected(
    db_session, tenant, admin_user, declaration_setup
) -> None:
    declaration = tax_assessment_service.lock_declaration(db_session, tenant.id, declaration_setup.id)
    with pytest.raises(tax_assessment_service.NoCurrentAssessmentError):
        tax_assessment_service.correct_assessment(
            db_session,
            tenant.id,
            declaration,
            gross_tax_amount=Decimal("2000"),
            credits_applied=Decimal("0"),
            observations=None,
            reason="No deberia funcionar",
            actor_user_id=admin_user.id,
        )


def test_unique_sequence_number_rejects_duplicate_via_direct_insert(
    db_session, tenant, admin_user, declaration_setup
) -> None:
    declaration = tax_assessment_service.lock_declaration(db_session, tenant.id, declaration_setup.id)
    tax_assessment_service.create_initial_assessment(
        db_session,
        tenant.id,
        declaration,
        gross_tax_amount=Decimal("1000"),
        credits_applied=Decimal("0"),
        observations=None,
        actor_user_id=admin_user.id,
    )

    duplicate = TaxAssessment(
        tenant_id=tenant.id,
        declaration_id=declaration.id,
        assessment_type="ORIGINAL",
        sequence_number=1,
        gross_tax_amount=Decimal("1"),
        credits_applied=Decimal("0"),
        total_payable=Decimal("1"),
        remaining_credit=Decimal("0"),
        assessed_at=declaration.updated_at,
        assessed_by=admin_user.id,
        is_current=False,
    )
    savepoint = db_session.begin_nested()
    with pytest.raises(IntegrityError):
        db_session.add(duplicate)
        db_session.flush()
    savepoint.rollback()


def test_audit_log_written_via_router_flow(
    auth_client, db_session, tenant, admin_user, obligation_catalog
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
    db_session.commit()

    response = auth_client.post(
        f"/api/v1/declarations/{declaration.id}/tax-assessments",
        json={"gross_tax_amount": "1000.00", "credits_applied": "0.00"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["total_payable"] == "1000.00"

    audit_rows = db_session.execute(
        select(AuditLog).where(AuditLog.action == "TAX_ASSESSMENT_CREATE")
    ).scalars().all()
    assert len(audit_rows) == 1
