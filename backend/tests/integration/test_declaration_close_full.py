"""Cierre operativo completo (Bloque 3.2, Dominio D): ver "Cierre operativo
completo" del plan. Ahora que Dominios A/B/C existen, `document_status` y
`tax_payment_status` pueden alcanzar de verdad `COMPLETE`/`PAID` -- el
cierre normal (sin override) debe funcionar sin cambios adicionales en
`declaration_service.close()` (ya escrito de forma generica en 3.1). Este
archivo cubre: cierre normal exitoso con las 3 precondiciones satisfechas, y
el nuevo gateo de rol admin para `force_close_with_pending` (Dominio D)."""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.core.config import get_settings
from app.core.security import hash_password
from app.models.user import User
from app.services import (
    declaration_document_service,
    declaration_service,
    document_requirement_catalog_service,
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

settings = get_settings()


@pytest.fixture()
def fully_eligible_declaration(db_session, tenant, admin_user, obligation_catalog):
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
        sync_documents=True,
    )

    # Filing -> FILED
    declaration = declaration_service.change_workflow_status(
        db_session, tenant.id, admin_user.id, declaration.id,
        target="DOCUMENTS_REQUESTED", reason=None, row_version=declaration.row_version,
    )
    declaration = declaration_service.file_declaration(
        db_session, tenant.id, admin_user.id, declaration.id,
        target="READY", presentation_date=None, order_number=None, reason=None,
        row_version=declaration.row_version,
    )
    declaration = declaration_service.file_declaration(
        db_session, tenant.id, admin_user.id, declaration.id,
        target="FILED", presentation_date=date(2026, 2, 10), order_number="OP-1",
        reason=None, row_version=declaration.row_version,
    )
    assert declaration.filing_status == "FILED"

    # tax_payment_status -> PAID
    locked = tax_assessment_service.lock_declaration(db_session, tenant.id, declaration.id)
    tax_assessment_service.create_initial_assessment(
        db_session, tenant.id, locked,
        gross_tax_amount=Decimal("500"), credits_applied=Decimal("0"),
        observations=None, actor_user_id=admin_user.id,
    )
    tax_payment_service.create_payment(
        db_session, tenant.id, locked,
        idempotency_key=uuid.uuid4(), amount=Decimal("500"),
        paid_at=date(2026, 2, 1), payment_method="TRANSFER",
        operation_number=None, reference=None, observations=None,
        overpayment_confirmed=False, overpayment_reason=None, actor_user_id=admin_user.id,
    )
    assert declaration.tax_payment_status == "PAID"

    # document_status -> COMPLETE: se asigna un requisito y se acepta su
    # unico item (CLOSE_ELIGIBLE_DOCUMENT solo admite COMPLETE/NOT_APPLICABLE,
    # NOT_CONFIGURED -- cero requisitos -- no basta).
    catalog_entry = document_requirement_catalog_service.create_custom_catalog_entry(
        db_session, tenant.id, code="DOC_CIERRE", name="Documento de cierre", description=None,
        category=None,
    )
    declaration_document_service.assign_requirement(
        db_session, tenant.id, client.id,
        requirement_catalog_id=catalog_entry.id, client_obligation_id=None,
        is_mandatory=True, periodicity="MONTHLY", valid_from=date(2026, 1, 1),
        valid_until=None, description=None, responsible_user_id=None, reminder_lead_days=5,
    )
    declaration_document_service.sync_requirements_for_declaration(
        db_session, tenant.id, declaration, actor_user_id=admin_user.id
    )
    items = declaration_document_service.list_items(db_session, tenant.id, declaration.id)
    assert len(items) == 1
    declaration_document_service.change_item_status(
        db_session, tenant.id, declaration, items[0].id,
        target="ACCEPTED", reason=None, file_metadata=None, actor_user_id=admin_user.id,
    )
    assert declaration.document_status == "COMPLETE"

    return declaration


def test_close_normal_succeeds_when_all_preconditions_met(
    db_session, tenant, admin_user, fully_eligible_declaration
) -> None:
    declaration = declaration_service.close(
        db_session, tenant.id, admin_user.id, fully_eligible_declaration.id,
        force_close_with_pending=False, close_override_reason=None,
    )
    assert declaration.workflow_status == "CLOSED"
    assert declaration.closed_with_pending is False


def test_close_with_pending_document_requires_override(
    db_session, tenant, admin_user, fully_eligible_declaration
) -> None:
    catalog_entry = document_requirement_catalog_service.create_custom_catalog_entry(
        db_session, tenant.id, code="EXTRA_DOC", name="Documento extra", description=None, category=None
    )
    requirement = declaration_document_service.assign_requirement(
        db_session, tenant.id, fully_eligible_declaration.client_id,
        requirement_catalog_id=catalog_entry.id, client_obligation_id=None,
        is_mandatory=True, periodicity="MONTHLY", valid_from=date(2026, 1, 1),
        valid_until=None, description=None, responsible_user_id=None, reminder_lead_days=5,
    )
    assert requirement.id is not None
    declaration_document_service.sync_requirements_for_declaration(
        db_session, tenant.id, fully_eligible_declaration, actor_user_id=admin_user.id
    )
    assert fully_eligible_declaration.document_status == "PARTIAL"

    with pytest.raises(declaration_service.CloseRequiresPendingOverrideError):
        declaration_service.close(
            db_session, tenant.id, admin_user.id, fully_eligible_declaration.id,
            force_close_with_pending=False, close_override_reason=None,
        )


def test_close_override_succeeds_with_flag_enabled_and_reason(
    db_session, tenant, admin_user, fully_eligible_declaration, monkeypatch
) -> None:
    catalog_entry = document_requirement_catalog_service.create_custom_catalog_entry(
        db_session, tenant.id, code="EXTRA_DOC2", name="Documento extra 2", description=None, category=None
    )
    declaration_document_service.assign_requirement(
        db_session, tenant.id, fully_eligible_declaration.client_id,
        requirement_catalog_id=catalog_entry.id, client_obligation_id=None,
        is_mandatory=True, periodicity="MONTHLY", valid_from=date(2026, 1, 1),
        valid_until=None, description=None, responsible_user_id=None, reminder_lead_days=5,
    )
    declaration_document_service.sync_requirements_for_declaration(
        db_session, tenant.id, fully_eligible_declaration, actor_user_id=admin_user.id
    )

    monkeypatch.setattr(settings, "allow_close_with_pending_tax", True)
    declaration = declaration_service.close(
        db_session, tenant.id, admin_user.id, fully_eligible_declaration.id,
        force_close_with_pending=True, close_override_reason="Cierre excepcional aprobado por gerencia",
    )
    assert declaration.workflow_status == "CLOSED"
    assert declaration.closed_with_pending is True
    assert declaration.close_override_reason == "Cierre excepcional aprobado por gerencia"


def test_force_close_router_requires_admin_role(auth_client, db_session, tenant, monkeypatch) -> None:
    non_admin = User(
        tenant_id=tenant.id,
        email="staff@luisiacontable-tests.dev",
        full_name="Staff sin rol admin",
        password_hash=hash_password("TestPassword123!"),
        role="staff",
        is_active=True,
    )
    db_session.add(non_admin)
    db_session.commit()

    monkeypatch.setattr(settings, "allow_close_with_pending_tax", True)

    login_response = auth_client.post(
        "/api/v1/auth/login", json={"email": non_admin.email, "password": "TestPassword123!"}
    )
    assert login_response.status_code == 200
    csrf_token = login_response.cookies.get("csrf_token")
    auth_client.headers.update({"X-CSRF-Token": csrf_token})

    response = auth_client.post(
        "/api/v1/declarations/00000000-0000-0000-0000-000000000000/close",
        json={"force_close_with_pending": True, "close_override_reason": "Deberia rechazarse"},
    )
    assert response.status_code == 403
