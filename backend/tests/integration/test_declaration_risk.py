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
) -> Declaration:
    declaration = Declaration(
        tenant_id=tenant_id,
        client_id=client_id,
        client_obligation_id=co_id,
        tax_period_id=period_id,
        workflow_status=workflow,
        filing_status=filing,
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


CASES = [
    ("CANCELLED", "NOT_FILED", TODAY, "CANCELLED"),
    ("NOT_APPLICABLE", "NOT_REQUIRED", TODAY, "NOT_APPLICABLE"),
    ("CLOSED", "FILED", TODAY, "CLOSED"),
    ("READY_TO_FILE", "FILED", TODAY - timedelta(days=30), "ON_TRACK"),  # FILED tras vencido -> ON_TRACK
    ("READY_TO_FILE", "NOT_REQUIRED", TODAY - timedelta(days=30), "ON_TRACK"),
    ("PENDING", "NOT_FILED", TODAY - timedelta(days=CRITICAL_THRESHOLD_DAYS + 1), "CRITICAL"),
    ("PENDING", "NOT_FILED", TODAY - timedelta(days=1), "OVERDUE"),
    ("PENDING", "NOT_FILED", TODAY, "DUE_TODAY"),
    ("PENDING", "NOT_FILED", TODAY + timedelta(days=UPCOMING_THRESHOLD_DAYS), "UPCOMING"),
    ("PENDING", "NOT_FILED", TODAY + timedelta(days=UPCOMING_THRESHOLD_DAYS + 10), "ON_TRACK"),
]


@pytest.mark.parametrize("workflow,filing,due,expected", CASES)
def test_python_matches_expected(workflow, filing, due, expected) -> None:
    assert compute_risk_status(due, workflow, filing, TODAY) == expected


@pytest.mark.parametrize("workflow,filing,due,expected", CASES)
def test_declaration_risk_python_matches_sql(
    db_session, scenario_setup, workflow, filing, due, expected
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
    )

    python_result = compute_risk_status(due, workflow, filing, TODAY)
    assert python_result == expected

    sql_result = db_session.execute(
        select(risk_status_sql_expression(TODAY)).where(Declaration.id == declaration.id)
    ).scalar_one()
    assert sql_result == python_result
