"""Tablero mensual completo (Bloque 3.2, Dominio E): ver "Tablero mensual
completo -- sin duplicacion de filas" del plan. Cubre: riesgo extendido
(PENDING_TAX/PENDING_DOCUMENTS/CLOSED_WITH_PENDING) visible via HTTP, y que
un cliente con multiples documentos/pagos/cargos de honorarios en el mismo
periodo siga mostrando EXACTAMENTE una fila por declaracion, con los
importes de honorarios agregados correctamente (nunca inflados por el
numero de filas hijas)."""

import uuid
from datetime import date
from decimal import Decimal

from app.services import (
    declaration_document_service,
    declaration_service,
    document_requirement_catalog_service,
    professional_fee_service,
    tax_assessment_service,
    tax_payment_service,
)
from tests.integration._declaration_helpers import (
    make_client,
    make_client_obligation,
    make_entry,
    make_monthly_period,
    make_source,
    make_version,
)


def test_dashboard_returns_computed_risk_status(
    auth_client, db_session, tenant, admin_user, obligation_catalog
) -> None:
    client = make_client(db_session, tenant.id)
    obligation = obligation_catalog["621"]
    co = make_client_obligation(db_session, tenant.id, client.id, obligation.id)
    source = make_source(db_session, tenant.id, obligation.id)
    version = make_version(db_session, tenant.id, source.id, fiscal_year=2026)
    make_entry(db_session, tenant.id, version.id, tax_period="2026-01", digits=list(range(10)))
    period = make_monthly_period(db_session, tenant.id, fiscal_year=2026, month=1)
    declaration_service.create_declaration(
        db_session, tenant.id, admin_user.id,
        client_id=client.id, client_obligation_id=co.id, tax_period_id=period.id,
        origin="MANUAL", sync_documents=False,
    )
    db_session.commit()

    response = auth_client.get("/api/v1/dashboard/monthly")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    row = next(r for r in body["items"] if r["client_id"] == str(client.id))
    # No esta FILED todavia, asi que el riesgo sigue siendo el basado en
    # fecha de vencimiento (ver test_declaration_risk.py para la derivacion
    # completa); aqui solo se verifica que el tablero lo expone.
    assert row["computed_risk_status"] is not None
    assert row["fee_total_charges"] == 0


def test_dashboard_single_row_per_declaration_with_correct_fee_aggregate(
    auth_client, db_session, tenant, admin_user, obligation_catalog
) -> None:
    client = make_client(db_session, tenant.id)
    obligation_621 = obligation_catalog["621"]
    obligation_plame = obligation_catalog["PLAME"]

    co_621 = make_client_obligation(db_session, tenant.id, client.id, obligation_621.id)
    source_621 = make_source(db_session, tenant.id, obligation_621.id)
    version_621 = make_version(db_session, tenant.id, source_621.id, fiscal_year=2026)
    make_entry(db_session, tenant.id, version_621.id, tax_period="2026-01", digits=list(range(10)))

    co_plame = make_client_obligation(db_session, tenant.id, client.id, obligation_plame.id)
    source_plame = make_source(db_session, tenant.id, obligation_plame.id)
    version_plame = make_version(db_session, tenant.id, source_plame.id, fiscal_year=2026)
    make_entry(db_session, tenant.id, version_plame.id, tax_period="2026-01", digits=list(range(10)))

    period = make_monthly_period(db_session, tenant.id, fiscal_year=2026, month=1)

    declaration_621 = declaration_service.create_declaration(
        db_session, tenant.id, admin_user.id,
        client_id=client.id, client_obligation_id=co_621.id, tax_period_id=period.id,
        origin="MANUAL", sync_documents=True,
    )
    declaration_plame = declaration_service.create_declaration(
        db_session, tenant.id, admin_user.id,
        client_id=client.id, client_obligation_id=co_plame.id, tax_period_id=period.id,
        origin="MANUAL", sync_documents=True,
    )

    # Multiples documentos para la primera declaracion.
    catalog_a = document_requirement_catalog_service.create_custom_catalog_entry(
        db_session, tenant.id, code="DOC_A", name="Documento A", description=None, category=None
    )
    catalog_b = document_requirement_catalog_service.create_custom_catalog_entry(
        db_session, tenant.id, code="DOC_B", name="Documento B", description=None, category=None
    )
    for catalog_entry in (catalog_a, catalog_b):
        declaration_document_service.assign_requirement(
            db_session, tenant.id, client.id,
            requirement_catalog_id=catalog_entry.id, client_obligation_id=None,
            is_mandatory=True, periodicity="MONTHLY", valid_from=date(2026, 1, 1),
            valid_until=None, description=None, responsible_user_id=None, reminder_lead_days=5,
        )
    declaration_document_service.sync_requirements_for_declaration(
        db_session, tenant.id, declaration_621, actor_user_id=admin_user.id
    )
    items = declaration_document_service.list_items(db_session, tenant.id, declaration_621.id)
    assert len(items) == 2

    # Multiples pagos parciales de impuestos para la primera declaracion.
    locked = tax_assessment_service.lock_declaration(db_session, tenant.id, declaration_621.id)
    tax_assessment_service.create_initial_assessment(
        db_session, tenant.id, locked,
        gross_tax_amount=Decimal("1000"), credits_applied=Decimal("0"),
        observations=None, actor_user_id=admin_user.id,
    )
    tax_payment_service.create_payment(
        db_session, tenant.id, locked, idempotency_key=uuid.uuid4(), amount=Decimal("300"),
        paid_at=date(2026, 2, 1), payment_method="TRANSFER", operation_number=None,
        reference=None, observations=None, overpayment_confirmed=False,
        overpayment_reason=None, actor_user_id=admin_user.id,
    )
    tax_payment_service.create_payment(
        db_session, tenant.id, locked, idempotency_key=uuid.uuid4(), amount=Decimal("200"),
        paid_at=date(2026, 2, 5), payment_method="TRANSFER", operation_number=None,
        reference=None, observations=None, overpayment_confirmed=False,
        overpayment_reason=None, actor_user_id=admin_user.id,
    )

    # Multiples cargos de honorarios para el MISMO cliente/periodo (acuerdos
    # de distinto service_type), que deben verse en AMBAS declaraciones del
    # cliente en ese periodo, repetidos -- nunca multiplicados.
    agreement_a = professional_fee_service.create_agreement(
        db_session, tenant.id, client.id,
        service_type="CONTABILIDAD", amount=Decimal("400.00"), billing_day=None,
        included_services=None, description=None, valid_from=date(2026, 1, 1),
        valid_until=None, observations=None, actor_user_id=admin_user.id,
    )
    agreement_b = professional_fee_service.create_agreement(
        db_session, tenant.id, client.id,
        service_type="PLANILLAS_HONORARIO", amount=Decimal("150.00"), billing_day=None,
        included_services=None, description=None, valid_from=date(2026, 1, 1),
        valid_until=None, observations=None, actor_user_id=admin_user.id,
    )
    charge_a = professional_fee_service.create_charge(
        db_session, tenant.id, agreement_a, tax_period_id=period.id,
        amount=None, due_date=None, observations=None, actor_user_id=admin_user.id,
    )
    charge_b = professional_fee_service.create_charge(
        db_session, tenant.id, agreement_b, tax_period_id=period.id,
        amount=None, due_date=None, observations=None, actor_user_id=admin_user.id,
    )
    db_session.commit()

    response = auth_client.get(
        "/api/v1/dashboard/monthly", params={"tax_period_id": str(period.id), "client_id": str(client.id)}
    )
    assert response.status_code == 200
    body = response.json()

    matching = [r for r in body["items"] if r["client_id"] == str(client.id)]
    assert len(matching) == 2  # una fila por declaracion, nunca multiplicada

    ids_seen = {r["id"] for r in matching}
    assert ids_seen == {str(declaration_621.id), str(declaration_plame.id)}

    for row in matching:
        # El agregado de honorarios (2 cargos, 400+150=550) se repite igual
        # en ambas declaraciones del mismo cliente/periodo -- nunca se suma
        # de mas ni se pierde por la presencia de 2 documentos + 2 pagos.
        assert row["fee_total_charges"] == 2
        assert Decimal(row["fee_total_amount"]) == Decimal("550.00")
        assert Decimal(row["fee_total_paid"]) == Decimal("0")
        assert Decimal(row["fee_balance"]) == Decimal("550.00")

    row_621 = next(r for r in matching if r["id"] == str(declaration_621.id))
    assert row_621["document_status"] in ("PENDING", "PARTIAL", "NOT_CONFIGURED")
    assert row_621["tax_payment_status"] == "PARTIALLY_PAID"
    assert Decimal(row_621["tax_gross_amount"]) == Decimal("1000.00")
    assert Decimal(row_621["tax_total_payable"]) == Decimal("1000.00")
    assert Decimal(row_621["tax_total_paid"]) == Decimal("500.00")
    assert row_621["missing_documents_count"] == 2

    row_plame = next(r for r in matching if r["id"] == str(declaration_plame.id))
    assert row_plame["tax_gross_amount"] is None
    assert row_plame["tax_total_payable"] is None
    assert Decimal(row_plame["tax_total_paid"]) == Decimal("0")
    assert row_plame["missing_documents_count"] == 0

    assert charge_a.id is not None and charge_b.id is not None  # sanity: ambos cargos existen
