"""`computed_risk_status`: funcion pura vs. expresion SQL equivalente deben
coincidir siempre (ver ADR-0018, corregido en la segunda/tercera ronda para
usar filing_status y distinguir CANCELLED de NOT_APPLICABLE)."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.declaration import Declaration
from app.services.declaration_risk import (
    CRITICAL_THRESHOLD_DAYS,
    UPCOMING_THRESHOLD_DAYS,
    compute_risk_status,
    risk_status_sql_expression,
)
from tests.integration._declaration_helpers import (
    make_client,
    make_client_obligation,
    make_entry,
    make_monthly_period,
    make_source,
    make_version,
)

TODAY = date(2026, 6, 15)


def _make_declaration_row(
    db_session,
    tenant_id,
    client_id,
    co_id,
    period_id,
    source_id,
    version_id,
    entry_id,
    *,
    due,
    workflow,
    filing,
    document_status="NOT_CONFIGURED",
    tax_payment_status="NOT_DETERMINED",
    closed_with_pending=False,
) -> Declaration:
    declaration = Declaration(
        tenant_id=tenant_id,
        client_id=client_id,
        client_obligation_id=co_id,
        tax_period_id=period_id,
        workflow_status=workflow,
        filing_status=filing,
        document_status=document_status,
        tax_payment_status=tax_payment_status,
        closed_with_pending=closed_with_pending,
        initial_effective_due_date=due,
        current_original_due_date=due,
        current_effective_due_date=due,
        schedule_source_id=source_id,
        schedule_version_id=version_id,
        schedule_rule_type="ENTRY",
        schedule_entry_id=entry_id,
        schedule_exception_id=None,
        schedule_snapshot={},
    )
    db_session.add(declaration)
    db_session.flush()
    return declaration


@pytest.fixture()
def scenario_setup(db_session, tenant, obligation_catalog):
    client = make_client(db_session, tenant.id)
    obligation = obligation_catalog["621"]
    co = make_client_obligation(db_session, tenant.id, client.id, obligation.id)
    source = make_source(db_session, tenant.id, obligation.id)
    version = make_version(db_session, tenant.id, source.id, status="APPROVED")
    entry = make_entry(db_session, tenant.id, version.id, tax_period="2026-01", digits=list(range(10)))
    period = make_monthly_period(db_session, tenant.id, fiscal_year=2026, month=1)
    return {
        "client": client,
        "co": co,
        "source": source,
        "version": version,
        "entry": entry,
        "period": period,
    }


# Valores por defecto de una declaracion recien creada (nunca determinada,
# sin control documental configurado) -- abreviados para acortar CASES.
_NC = "NOT_CONFIGURED"
_ND = "NOT_DETERMINED"

CASES = [
    # (workflow, filing, due, document_status, tax_payment_status, closed_with_pending, expected)
    ("CANCELLED", "NOT_FILED", TODAY, _NC, _ND, False, "CANCELLED"),
    ("NOT_APPLICABLE", "NOT_REQUIRED", TODAY, _NC, _ND, False, "NOT_APPLICABLE"),
    ("CLOSED", "FILED", TODAY, "COMPLETE", "PAID", False, "CLOSED"),
    ("CLOSED", "FILED", TODAY, "PENDING", "PAYMENT_PENDING", True, "CLOSED_WITH_PENDING"),
    # FILED pero la determinacion tributaria sigue pendiente (ver Dominio E):
    # ya no es "ON_TRACK" a ciegas como en 3.1, sino PENDING_TAX.
    ("READY_TO_FILE", "FILED", TODAY - timedelta(days=30), _NC, _ND, False, "PENDING_TAX"),
    ("READY_TO_FILE", "NOT_REQUIRED", TODAY - timedelta(days=30), _NC, _ND, False, "PENDING_TAX"),
    # FILED + impuesto ya determinado y pagado, pero documentos incompletos.
    ("READY_TO_FILE", "FILED", TODAY - timedelta(days=30), "PARTIAL", "PAID", False, "PENDING_DOCUMENTS"),
    # FILED + impuesto y documentos ambos en un estado elegible -> ON_TRACK real.
    ("READY_TO_FILE", "FILED", TODAY - timedelta(days=30), "COMPLETE", "PAID", False, "ON_TRACK"),
    (
        "PENDING", "NOT_FILED", TODAY - timedelta(days=CRITICAL_THRESHOLD_DAYS + 1),
        _NC, _ND, False, "CRITICAL",
    ),
    ("PENDING", "NOT_FILED", TODAY - timedelta(days=1), _NC, _ND, False, "OVERDUE"),
    ("PENDING", "NOT_FILED", TODAY, _NC, _ND, False, "DUE_TODAY"),
    (
        "PENDING", "NOT_FILED", TODAY + timedelta(days=UPCOMING_THRESHOLD_DAYS),
        _NC, _ND, False, "UPCOMING",
    ),
    (
        "PENDING", "NOT_FILED", TODAY + timedelta(days=UPCOMING_THRESHOLD_DAYS + 10),
        _NC, _ND, False, "ON_TRACK",
    ),
]


@pytest.mark.parametrize(
    "workflow,filing,due,document_status,tax_payment_status,closed_with_pending,expected", CASES
)
def test_python_matches_expected(
    workflow, filing, due, document_status, tax_payment_status, closed_with_pending, expected
) -> None:
    assert (
        compute_risk_status(
            due,
            workflow,
            filing,
            TODAY,
            document_status=document_status,
            tax_payment_status=tax_payment_status,
            closed_with_pending=closed_with_pending,
        )
        == expected
    )


@pytest.mark.parametrize(
    "workflow,filing,due,document_status,tax_payment_status,closed_with_pending,expected", CASES
)
def test_declaration_risk_python_matches_sql(
    db_session,
    scenario_setup,
    workflow,
    filing,
    due,
    document_status,
    tax_payment_status,
    closed_with_pending,
    expected,
) -> None:
    declaration = _make_declaration_row(
        db_session,
        scenario_setup["client"].tenant_id,
        scenario_setup["client"].id,
        scenario_setup["co"].id,
        scenario_setup["period"].id,
        scenario_setup["source"].id,
        scenario_setup["version"].id,
        scenario_setup["entry"].id,
        due=due,
        workflow=workflow,
        filing=filing,
        document_status=document_status,
        tax_payment_status=tax_payment_status,
        closed_with_pending=closed_with_pending,
    )

    python_result = compute_risk_status(
        due,
        workflow,
        filing,
        TODAY,
        document_status=document_status,
        tax_payment_status=tax_payment_status,
        closed_with_pending=closed_with_pending,
    )
    assert python_result == expected

    sql_result = db_session.execute(
        select(risk_status_sql_expression(TODAY)).where(Declaration.id == declaration.id)
    ).scalar_one()
    assert sql_result == python_result
