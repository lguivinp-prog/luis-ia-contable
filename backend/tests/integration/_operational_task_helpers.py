"""Helpers compartidos para las pruebas de Fase 2/Bloque 1 (gestion
operativa de tareas): construyen una declaracion completa (con item
documental, cargo de honorarios y pago tributario reales) para poder anclar
`operational_tasks` a cada una de las 5 formas estructurales del plan (ver
Seccion 5). Reutiliza los mismos helpers/servicios de Bloque 3.2, siguiendo
el patron de `_declaration_helpers.py`."""

import uuid
from datetime import date
from decimal import Decimal

from app.models.declaration_document import DeclarationDocumentItem, DocumentRequirementCatalog
from app.repositories import declaration_document_repository
from app.services import (
    declaration_document_service,
    declaration_service,
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


def make_declaration_setup(
    db_session, tenant, admin_user, obligation_catalog, *, month: int = 1, source=None, version=None
):
    """`get_active_source_for_obligation` (declaration_service) resuelve una
    UNICA fuente activa por obligacion -- igual que en produccion, donde solo
    existe un cronograma SUNAT autoritativo por tributo. Para poder crear
    varias declaraciones (distintos meses) del mismo cliente/obligacion, los
    llamadores que necesiten mas de un mes deben reutilizar la MISMA
    `source`/`version` devueltas por la primera llamada (parametros
    opcionales) y solo se agrega la entrada de cronograma del mes nuevo --
    nunca una fuente nueva por mes, que rompería esa resolucion unica."""
    client = make_client(db_session, tenant.id)
    obligation = obligation_catalog["621"]
    co = make_client_obligation(db_session, tenant.id, client.id, obligation.id)
    if source is None:
        source = make_source(db_session, tenant.id, obligation.id)
    if version is None:
        version = make_version(db_session, tenant.id, source.id, fiscal_year=2026)
    make_entry(
        db_session,
        tenant.id,
        version.id,
        tax_period=f"2026-{month:02d}",
        digits=list(range(10)),
    )
    period = make_monthly_period(db_session, tenant.id, fiscal_year=2026, month=month)

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
    return {
        "client": client,
        "obligation": obligation,
        "client_obligation": co,
        "source": source,
        "version": version,
        "period": period,
        "declaration": declaration,
    }


def make_second_month_declaration(db_session, tenant, admin_user, base_setup, *, month: int):
    """Declaracion adicional del MISMO cliente/obligacion en otro periodo,
    reutilizando la fuente/version ya activa (ver `make_declaration_setup`)
    -- usada para probar rechazos de FK compuesta con una declaracion real
    pero distinta a la ancla original."""
    return make_declaration_setup(
        db_session,
        tenant,
        admin_user,
        {"621": base_setup["obligation"]},
        month=month,
        source=base_setup["source"],
        version=base_setup["version"],
    )


def make_document_item(db_session, tenant, setup, *, code: str | None = None) -> DeclarationDocumentItem:
    catalog_entry = DocumentRequirementCatalog(
        tenant_id=None,
        is_system=True,
        code=code or f"REQ_{uuid.uuid4().hex[:8]}",
        name="Requisito de prueba",
        is_active=True,
    )
    db_session.add(catalog_entry)
    db_session.flush()

    requirement = declaration_document_service.assign_requirement(
        db_session,
        tenant.id,
        setup["client"].id,
        requirement_catalog_id=catalog_entry.id,
        client_obligation_id=None,
        is_mandatory=True,
        periodicity="MONTHLY",
        valid_from=date(2026, 1, 1),
        valid_until=None,
        description=None,
        responsible_user_id=None,
        reminder_lead_days=5,
    )
    declaration_document_service.sync_requirements_for_declaration(
        db_session, tenant.id, setup["declaration"], actor_user_id=None
    )
    item = declaration_document_repository.get_item_by_requirement(
        db_session, tenant.id, setup["declaration"].id, requirement.id
    )
    assert item is not None
    return item


def make_fee_agreement_and_charge(db_session, tenant, setup, *, amount=Decimal("500.00")):
    agreement = professional_fee_service.create_agreement(
        db_session,
        tenant.id,
        setup["client"].id,
        service_type=f"SERVICIO_{uuid.uuid4().hex[:6]}",
        amount=amount,
        billing_day=5,
        included_services=None,
        description=None,
        valid_from=date(2026, 1, 1),
        valid_until=None,
        observations=None,
        actor_user_id=None,
    )
    charge = professional_fee_service.create_charge(
        db_session,
        tenant.id,
        agreement,
        tax_period_id=setup["period"].id,
        amount=None,
        due_date=date(2026, 2, 15),
        observations=None,
        actor_user_id=None,
    )
    return agreement, charge


def make_tax_payment(db_session, tenant, setup, admin_user, *, amount=Decimal("100.00")):
    locked = tax_assessment_service.lock_declaration(db_session, tenant.id, setup["declaration"].id)
    tax_assessment_service.create_initial_assessment(
        db_session,
        tenant.id,
        locked,
        gross_tax_amount=Decimal("1000"),
        credits_applied=Decimal("0"),
        observations=None,
        actor_user_id=admin_user.id,
    )
    payment = tax_payment_service.create_payment(
        db_session,
        tenant.id,
        locked,
        idempotency_key=uuid.uuid4(),
        amount=amount,
        paid_at=date(2026, 2, 1),
        payment_method="TRANSFER",
        operation_number=None,
        reference=None,
        observations=None,
        overpayment_confirmed=False,
        overpayment_reason=None,
        actor_user_id=admin_user.id,
    )
    return payment
