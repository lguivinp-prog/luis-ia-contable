"""Pruebas HTTP del router de honorarios profesionales (Bloque 3.2, Dominio
C): acuerdos (crear/supersede/cancel), cargos (crear/emitir), pagos
(crear/reenvio idempotente/conflicto/anular), y recibos (emitir/anular) --
incluyendo CSRF y auditoria via el flujo real del router."""

import uuid

from sqlalchemy import select

from app.models.audit_log import AuditLog
from tests.integration._declaration_helpers import make_client, make_monthly_period


def test_full_fee_lifecycle_via_api(auth_client, db_session, tenant, admin_user) -> None:
    client = make_client(db_session, tenant.id)
    period = make_monthly_period(db_session, tenant.id, fiscal_year=2026, month=1)
    db_session.commit()

    agreement_response = auth_client.post(
        f"/api/v1/clients/{client.id}/fee-agreements",
        json={
            "service_type": "CONTABILIDAD_MENSUAL",
            "amount": "500.00",
            "valid_from": "2026-01-01",
        },
    )
    assert agreement_response.status_code == 201
    agreement_id = agreement_response.json()["id"]
    assert agreement_response.json()["status"] == "ACTIVE"

    list_agreements = auth_client.get(f"/api/v1/clients/{client.id}/fee-agreements")
    assert list_agreements.status_code == 200
    assert len(list_agreements.json()) == 1

    charge_response = auth_client.post(
        f"/api/v1/fee-agreements/{agreement_id}/charges",
        json={"tax_period_id": str(period.id)},
    )
    assert charge_response.status_code == 201
    charge_id = charge_response.json()["id"]
    assert charge_response.json()["status"] == "PENDING_ISSUANCE"
    assert charge_response.json()["amount"] == "500.00"

    issue_response = auth_client.post(
        f"/api/v1/fee-charges/{charge_id}/issue",
        json={"due_date": "2026-02-05"},
    )
    assert issue_response.status_code == 200
    assert issue_response.json()["status"] == "PAYMENT_PENDING"

    idempotency_key = str(uuid.uuid4())
    payment_response = auth_client.post(
        f"/api/v1/fee-charges/{charge_id}/payments",
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
        f"/api/v1/fee-charges/{charge_id}/payments",
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
        f"/api/v1/fee-charges/{charge_id}/payments",
        json={
            "idempotency_key": idempotency_key,
            "amount": "999.00",
            "paid_at": "2026-02-01",
            "payment_method": "TRANSFER",
        },
    )
    assert conflict_response.status_code == 409

    payments_list = auth_client.get(f"/api/v1/fee-charges/{charge_id}/payments")
    assert payments_list.status_code == 200
    assert len(payments_list.json()) == 1

    receipt_response = auth_client.post(
        f"/api/v1/fee-charges/{charge_id}/receipts",
        json={
            "receipt_type": "RECIBO_HONORARIOS",
            "number": "API-001",
            "issued_at": "2026-02-01",
            "amount": "500.00",
        },
    )
    assert receipt_response.status_code == 201
    receipt_id = receipt_response.json()["id"]
    assert receipt_response.json()["issued_by"] == str(admin_user.id)

    receipts_list = auth_client.get(f"/api/v1/fee-charges/{charge_id}/receipts")
    assert receipts_list.status_code == 200
    assert len(receipts_list.json()) == 1

    void_receipt_response = auth_client.post(
        f"/api/v1/fee-receipts/{receipt_id}/void", json={"reason": "Numero incorrecto"}
    )
    assert void_receipt_response.status_code == 200
    assert void_receipt_response.json()["status"] == "VOIDED"

    void_payment_response = auth_client.post(
        f"/api/v1/fee-charges/{charge_id}/payments/{payment_id}/void",
        json={"reason": "Pago registrado por error"},
    )
    assert void_payment_response.status_code == 200
    assert void_payment_response.json()["is_voided"] is True

    supersede_response = auth_client.post(
        f"/api/v1/fee-agreements/{agreement_id}/supersede",
        json={"effective_date": "2026-06-30", "reason": "Renegociacion de tarifa"},
    )
    assert supersede_response.status_code == 200
    assert supersede_response.json()["status"] == "SUPERSEDED"

    new_agreement_response = auth_client.post(
        f"/api/v1/clients/{client.id}/fee-agreements",
        json={
            "service_type": "CONTABILIDAD_MENSUAL",
            "amount": "600.00",
            "valid_from": "2026-07-01",
        },
    )
    assert new_agreement_response.status_code == 201

    cancel_response = auth_client.post(
        f"/api/v1/fee-agreements/{new_agreement_response.json()['id']}/cancel",
        json={"effective_date": "2026-07-15", "reason": "Cliente se retira"},
    )
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "CANCELLED"

    audit_actions = {
        row.action
        for row in db_session.execute(
            select(AuditLog).where(
                AuditLog.entity_type.in_(
                    [
                        "professional_fee_agreement",
                        "professional_fee_charge",
                        "professional_fee_payment",
                        "professional_receipt",
                    ]
                )
            )
        ).scalars()
    }
    assert "FEE_AGREEMENT_CREATE" in audit_actions
    assert "FEE_AGREEMENT_SUPERSEDE" in audit_actions
    assert "FEE_AGREEMENT_CANCEL" in audit_actions
    assert "FEE_CHARGE_CREATE" in audit_actions
    assert "FEE_PAYMENT_CREATE" in audit_actions
    assert "FEE_PAYMENT_VOID" in audit_actions
    assert "FEE_RECEIPT_ISSUE" in audit_actions
    assert "FEE_RECEIPT_VOID" in audit_actions


def test_agreement_overlap_returns_409_via_api(auth_client, db_session, tenant) -> None:
    client = make_client(db_session, tenant.id)
    db_session.commit()

    payload = {"service_type": "PLANILLAS", "amount": "300.00", "valid_from": "2026-01-01"}
    first = auth_client.post(f"/api/v1/clients/{client.id}/fee-agreements", json=payload)
    assert first.status_code == 201

    second = auth_client.post(f"/api/v1/clients/{client.id}/fee-agreements", json=payload)
    assert second.status_code == 409


def test_payment_before_issuance_returns_409_via_api(auth_client, db_session, tenant) -> None:
    client = make_client(db_session, tenant.id)
    period = make_monthly_period(db_session, tenant.id, fiscal_year=2026, month=1)
    db_session.commit()

    agreement_response = auth_client.post(
        f"/api/v1/clients/{client.id}/fee-agreements",
        json={"service_type": "AUDITORIA", "amount": "1000.00", "valid_from": "2026-01-01"},
    )
    charge_response = auth_client.post(
        f"/api/v1/fee-agreements/{agreement_response.json()['id']}/charges",
        json={"tax_period_id": str(period.id)},
    )
    payment_response = auth_client.post(
        f"/api/v1/fee-charges/{charge_response.json()['id']}/payments",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "amount": "100.00",
            "paid_at": "2026-02-01",
            "payment_method": "TRANSFER",
        },
    )
    assert payment_response.status_code == 409


def test_receipt_exceeding_charge_amount_returns_422_via_api(auth_client, db_session, tenant) -> None:
    client = make_client(db_session, tenant.id)
    period = make_monthly_period(db_session, tenant.id, fiscal_year=2026, month=1)
    db_session.commit()

    agreement_response = auth_client.post(
        f"/api/v1/clients/{client.id}/fee-agreements",
        json={"service_type": "TRIBUTARIO", "amount": "200.00", "valid_from": "2026-01-01"},
    )
    charge_response = auth_client.post(
        f"/api/v1/fee-agreements/{agreement_response.json()['id']}/charges",
        json={"tax_period_id": str(period.id)},
    )
    charge_id = charge_response.json()["id"]
    auth_client.post(f"/api/v1/fee-charges/{charge_id}/issue", json={"due_date": "2026-02-05"})

    response = auth_client.post(
        f"/api/v1/fee-charges/{charge_id}/receipts",
        json={
            "receipt_type": "RECIBO_HONORARIOS",
            "number": "API-EXCESS",
            "issued_at": "2026-02-01",
            "amount": "500.00",
        },
    )
    assert response.status_code == 422
