"""Paridad Python/SQL del riesgo de cobranza de honorarios (ver "Riesgo de
cobranza de honorarios" del plan / ADR-0045), mismo patron que
`test_declaration_risk_python_matches_sql`. Tambien verifica que el riesgo
cambia con el paso del tiempo sin alterar el `status` persistido del
cargo (usa `frozen_clock`)."""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from app.models.professional_fee import ProfessionalFeeCharge, ProfessionalFeePayment
from app.services import professional_fee_payment_service, professional_fee_service
from app.services.professional_fee_risk import (
    compute_fee_collection_risk,
    fee_collection_risk_sql_expression,
)
from tests.integration._declaration_helpers import make_client, make_monthly_period

TODAY = date(2026, 3, 15)


def _make_agreement(db_session, tenant, client, *, service_type):
    return professional_fee_service.create_agreement(
        db_session,
        tenant.id,
        client.id,
        service_type=service_type,
        amount=Decimal("500.00"),
        billing_day=None,
        included_services=None,
        description=None,
        valid_from=date(2026, 1, 1),
        valid_until=None,
        observations=None,
        actor_user_id=None,
    )


def test_fee_collection_risk_python_matches_sql(db_session, tenant, admin_user) -> None:
    client = make_client(db_session, tenant.id)
    period = make_monthly_period(db_session, tenant.id, fiscal_year=2026, month=1)

    scenarios = [
        # (service_type, due_date, paid_amount, cancel)
        ("SERVICIO_CANCELADO", date(2026, 3, 1), Decimal("0"), True),
        ("SERVICIO_PAGADO", date(2026, 3, 1), Decimal("500.00"), False),
        ("SERVICIO_SIN_VENCIMIENTO", None, Decimal("0"), False),
        ("SERVICIO_VENCIDO", date(2026, 3, 1), Decimal("0"), False),
        ("SERVICIO_VENCE_HOY", TODAY, Decimal("0"), False),
        ("SERVICIO_FUTURO", date(2026, 4, 1), Decimal("0"), False),
    ]

    charge_ids = []
    for service_type, due_date, paid_amount, cancel in scenarios:
        agreement = _make_agreement(db_session, tenant, client, service_type=service_type)
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
            db_session, tenant.id, charge, due_date=due_date, actor_user_id=admin_user.id
        )
        if paid_amount > 0:
            professional_fee_payment_service.create_payment(
                db_session,
                tenant.id,
                charge,
                idempotency_key=uuid.uuid4(),
                amount=paid_amount,
                paid_at=date(2026, 2, 1),
                payment_method="TRANSFER",
                operation_number=None,
                reference=None,
                observations=None,
                overpayment_confirmed=False,
                overpayment_reason=None,
                actor_user_id=admin_user.id,
            )
        if cancel:
            charge.status = "CANCELLED"
            db_session.flush()
        charge_ids.append(charge.id)

    paid_subquery = (
        select(func.coalesce(func.sum(ProfessionalFeePayment.amount), 0))
        .where(
            ProfessionalFeePayment.charge_id == ProfessionalFeeCharge.id,
            ProfessionalFeePayment.is_voided.is_(False),
        )
        .correlate(ProfessionalFeeCharge)
        .scalar_subquery()
    )
    balance_expression = ProfessionalFeeCharge.amount - paid_subquery

    rows = db_session.execute(
        select(
            ProfessionalFeeCharge.id,
            ProfessionalFeeCharge.status,
            ProfessionalFeeCharge.due_date,
            ProfessionalFeeCharge.amount,
            fee_collection_risk_sql_expression(TODAY, balance_expression).label("sql_risk"),
        ).where(ProfessionalFeeCharge.id.in_(charge_ids))
    ).all()

    assert len(rows) == len(scenarios)
    for charge_id, status, due_date, amount, sql_risk in rows:
        total_paid = db_session.execute(
            select(func.coalesce(func.sum(ProfessionalFeePayment.amount), 0)).where(
                ProfessionalFeePayment.charge_id == charge_id,
                ProfessionalFeePayment.is_voided.is_(False),
            )
        ).scalar_one()
        balance = amount - Decimal(total_paid)
        python_risk = compute_fee_collection_risk(
            due_date=due_date, status=status, balance=balance, today=TODAY
        )
        assert python_risk == sql_risk, f"charge {charge_id}: python={python_risk} sql={sql_risk}"


def test_fee_collection_risk_changes_with_time_without_altering_persisted_status(
    db_session, tenant, admin_user, frozen_clock
) -> None:
    from datetime import UTC, datetime

    client = make_client(db_session, tenant.id)
    period = make_monthly_period(db_session, tenant.id, fiscal_year=2026, month=1)
    agreement = _make_agreement(db_session, tenant, client, service_type="SERVICIO_TIEMPO")

    frozen_clock["now"] = datetime(2026, 1, 10, tzinfo=UTC)
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
        db_session, tenant.id, charge, due_date=date(2026, 2, 1), actor_user_id=admin_user.id
    )
    assert charge.status == "PAYMENT_PENDING"

    risk_before = compute_fee_collection_risk(
        due_date=charge.due_date, status=charge.status, balance=charge.amount, today=date(2026, 1, 10)
    )
    assert risk_before == "ON_TRACK"

    # Avanza el reloj mas alla del vencimiento -- el riesgo calculado en vivo
    # cambia, pero el status persistido del cargo NO se toca (no hay job).
    frozen_clock["now"] = datetime(2026, 3, 1, tzinfo=UTC)
    risk_after = compute_fee_collection_risk(
        due_date=charge.due_date, status=charge.status, balance=charge.amount, today=date(2026, 3, 1)
    )
    assert risk_after == "OVERDUE"
    assert charge.status == "PAYMENT_PENDING"
