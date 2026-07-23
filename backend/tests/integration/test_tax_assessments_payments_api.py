"""Pruebas HTTP de los endpoints de determinacion y pagos tributarios
(Bloque 3.2, Dominio B): creacion, correccion, pago, reenvio idempotente,
conflicto de idempotencia, sobrepago y anulacion -- incluyendo CSRF y
auditoria via el flujo real del router."""

import uuid

from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.services import declaration_service
from tests.integration._declaration_helpers import (
    make_client,
    make_client_obligation,
    make_entry,
    make_monthly_period,
    make_source,
    make_version,
)


def _setup_declaration(db_session, tenant, admin_user, obligation_catalog):
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
    return declaration


def test_assessment_and_payment_full_flow_via_api(
    auth_client, db_session, tenant, admin_user, obligation_catalog
) -> None:
    declaration = _setup_declaration(db_session, tenant, admin_user, obligation_catalog)

    create_response = auth_client.post(
        f"/api/v1/declarations/{declaration.id}/tax-assessments",
        json={"gross_tax_amount": "1000.00", "credits_applied": "100.00"},
    )
    assert create_response.status_code == 201
    body = create_response.json()
    assert body["total_payable"] == "900.00"
    assert body["sequence_number"] == 1

    correct_response = auth_client.post(
        f"/api/v1/declarations/{declaration.id}/tax-assessments/correct",
        json={"gross_tax_amount": "1200.00", "credits_applied": "0.00", "reason": "Ajuste tras revision"},
    )
    assert correct_response.status_code == 200
    assert correct_response.json()["sequence_number"] == 2
    assert correct_response.json()["total_payable"] == "1200.00"

    list_response = auth_client.get(f"/api/v1/declarations/{declaration.id}/tax-assessments")
    assert list_response.status_code == 200
    assert len(list_response.json()) == 2

    idempotency_key = str(uuid.uuid4())
    payment_response = auth_client.post(
        f"/api/v1/declarations/{declaration.id}/tax-payments",
        json={
            "idempotency_key": idempotency_key,
            "amount": "500.00",
            "paid_at": "2026-02-01",
            "payment_method": "TRANSFER",
        },
    )
    assert payment_response.status_code == 201
    payment_id = payment_response.json()["id"]

    resend_response = auth_client.post(
        f"/api/v1/declarations/{declaration.id}/tax-payments",
        json={
            "idempotency_key": idempotency_key,
            "amount": "500.00",
            "paid_at": "2026-02-01",
            "payment_method": "TRANSFER",
        },
    )
    assert resend_response.status_code == 201
    assert resend_response.json()["id"] == payment_id

    conflict_response = auth_client.post(
        f"/api/v1/declarations/{declaration.id}/tax-payments",
        json={
            "idempotency_key": idempotency_key,
            "amount": "999.00",
            "paid_at": "2026-02-01",
            "payment_method": "TRANSFER",
        },
    )
    assert conflict_response.status_code == 409

    void_response = auth_client.post(
        f"/api/v1/declarations/{declaration.id}/tax-payments/{payment_id}/void",
        json={"reason": "Pago registrado por error"},
    )
    assert void_response.status_code == 200
    assert void_response.json()["is_voided"] is True

    payments_list = auth_client.get(f"/api/v1/declarations/{declaration.id}/tax-payments")
    assert payments_list.status_code == 200
    assert len(payments_list.json()) == 1

    audit_actions = {
        row.action
        for row in db_session.execute(
            select(AuditLog).where(AuditLog.entity_type.in_(["tax_assessment", "tax_payment"]))
        ).scalars()
    }
    assert "TAX_ASSESSMENT_CREATE" in audit_actions
    assert "TAX_ASSESSMENT_CORRECT" in audit_actions
    assert "TAX_PAYMENT_CREATE" in audit_actions
    assert "TAX_PAYMENT_VOID" in audit_actions


def test_overpayment_without_confirmation_returns_409_via_api(
    auth_client, db_session, tenant, admin_user, obligation_catalog
) -> None:
    declaration = _setup_declaration(db_session, tenant, admin_user, obligation_catalog)
    auth_client.post(
        f"/api/v1/declarations/{declaration.id}/tax-assessments",
        json={"gross_tax_amount": "500.00", "credits_applied": "0.00"},
    )
    response = auth_client.post(
        f"/api/v1/declarations/{declaration.id}/tax-payments",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "amount": "1000.00",
            "paid_at": "2026-02-01",
            "payment_method": "TRANSFER",
        },
    )
    assert response.status_code == 409


def test_payment_without_assessment_returns_409_via_api(
    auth_client, db_session, tenant, admin_user, obligation_catalog
) -> None:
    declaration = _setup_declaration(db_session, tenant, admin_user, obligation_catalog)
    response = auth_client.post(
        f"/api/v1/declarations/{declaration.id}/tax-payments",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "amount": "100.00",
            "paid_at": "2026-02-01",
            "payment_method": "TRANSFER",
        },
    )
    assert response.status_code == 409


def test_assessment_unknown_declaration_returns_404(auth_client) -> None:
    response = auth_client.post(
        f"/api/v1/declarations/{uuid.uuid4()}/tax-assessments",
        json={"gross_tax_amount": "100.00", "credits_applied": "0.00"},
    )
    assert response.status_code == 404
