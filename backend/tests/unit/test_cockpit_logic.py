"""Cockpit Multi-RUC (M1, Domain B): logica pura de derivacion."""

from datetime import date

import pytest

from app.services import cockpit_logic as logic


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 10, 1), "APERTURA"),
        (date(2026, 10, 4), "APERTURA"),
        (date(2026, 10, 5), "OPERACION"),
        (date(2026, 10, 14), "OPERACION"),
        (date(2026, 10, 15), "CIERRE"),
        (date(2026, 10, 31), "CIERRE"),
        (date(2026, 11, 3), "CIERRE"),  # pasado el mes de trabajo: sigue CIERRE (el riesgo lo da el vencimiento)
        (date(2026, 9, 30), "PRE_APERTURA"),
    ],
)
def test_monthly_phase_for_september_period(today, expected):
    period_end = date(2026, 9, 30)
    assert logic.monthly_phase(period_end, today) == expected


def test_monthly_phase_december_period_rolls_to_january():
    assert logic.monthly_phase(date(2026, 12, 31), date(2027, 1, 2)) == "APERTURA"
    assert logic.monthly_phase(date(2026, 12, 31), date(2026, 12, 20)) == "PRE_APERTURA"


@pytest.mark.parametrize(
    ("workflow", "filing", "expected"),
    [
        ("PENDING", "NOT_FILED", "NOT_STARTED"),
        ("DOCUMENTS_REQUESTED", "NOT_FILED", "IN_PROGRESS"),
        ("INFORMATION_INCOMPLETE", "NOT_FILED", "IN_PROGRESS"),
        ("DOCUMENTS_COMPLETE", "NOT_FILED", "IN_PROGRESS"),
        ("IN_PROGRESS", "NOT_FILED", "IN_PROGRESS"),
        ("UNDER_REVIEW", "NOT_FILED", "IN_PROGRESS"),
        ("READY_TO_FILE", "NOT_FILED", "READY_FOR_SUNAT"),
        ("IN_PROGRESS", "READY", "READY_FOR_SUNAT"),
        ("READY_TO_FILE", "FILED", "PRESENTED"),
        ("CLOSED", "FILED", "PRESENTED"),
        ("IN_PROGRESS", "FAILED", "IN_PROGRESS"),
        ("NOT_APPLICABLE", "NOT_REQUIRED", "NOT_APPLICABLE"),
        ("PENDING", "NOT_REQUIRED", "NOT_APPLICABLE"),
    ],
)
def test_sire_component_mapping_is_conservative(workflow, filing, expected):
    assert logic.sire_component_status(workflow, filing) == expected


@pytest.mark.parametrize(
    ("components", "expected"),
    [
        ([], "NOT_CONFIGURED"),
        (["PRESENTED"], "PRESENTED"),
        (["PRESENTED", "NOT_STARTED"], "NOT_STARTED"),
        (["PRESENTED", "READY_FOR_SUNAT"], "READY_FOR_SUNAT"),
        (["IN_PROGRESS", "READY_FOR_SUNAT"], "IN_PROGRESS"),
        (["NOT_APPLICABLE", "PRESENTED"], "PRESENTED"),
        (["NOT_APPLICABLE", "NOT_APPLICABLE"], "NOT_APPLICABLE"),
    ],
)
def test_sire_combination_takes_the_most_pending_applicable_component(components, expected):
    assert logic.combine_sire(components) == expected


def test_documents_one_incomplete_declaration_prevents_complete():
    assert logic.aggregate_document_status(["COMPLETE", "PARTIAL"]) == "PARTIAL"
    assert logic.aggregate_document_status(["COMPLETE", "OBSERVED", "PENDING"]) == "OBSERVED"
    assert logic.aggregate_document_status(["COMPLETE", "NOT_APPLICABLE"]) == "COMPLETE"
    assert logic.aggregate_document_status(["COMPLETE", "NOT_CONFIGURED"]) == "NOT_CONFIGURED"
    assert logic.aggregate_document_status(["NOT_APPLICABLE"]) == "NOT_APPLICABLE"
    assert logic.aggregate_document_status([]) is None


def test_tax_payment_status_aggregation_picks_the_worst():
    assert logic.aggregate_tax_payment_status(["PAID", "PAYMENT_PENDING"]) == "PAYMENT_PENDING"
    assert logic.aggregate_tax_payment_status(["PAID", "PARTIALLY_PAID"]) == "PARTIALLY_PAID"
    assert logic.aggregate_tax_payment_status(["PAID", "NO_TAX_DUE"]) == "PAID"
    assert logic.aggregate_tax_payment_status([]) is None


def test_overall_risk_takes_the_worst_declaration_and_fees_never_lower_it():
    kwargs = dict(period_generated=True, document_status="COMPLETE", tax_payment_status="PAID")
    assert logic.overall_risk(["ON_TRACK", "CRITICAL"], fee_collection_risk="ON_TRACK", **kwargs) == "CRITICAL"
    # una cobranza vencida NO oculta ni degrada una obligacion legal vencida
    assert logic.overall_risk(["OVERDUE"], fee_collection_risk="OVERDUE", **kwargs) == "OVERDUE"
    # sin riesgo legal, la cobranza vencida si se refleja
    assert logic.overall_risk(["ON_TRACK"], fee_collection_risk="OVERDUE", **kwargs) == "PENDING_FEES"
    assert logic.overall_risk(["UPCOMING"], fee_collection_risk="OVERDUE", **kwargs) == "PENDING_FEES"
    assert logic.overall_risk(["ON_TRACK"], fee_collection_risk="ON_TRACK", **kwargs) == "ON_TRACK"


def test_overall_risk_includes_documents_and_tax_payment_components():
    base = dict(period_generated=True, fee_collection_risk=None)
    assert logic.overall_risk(["ON_TRACK"], document_status="PARTIAL", tax_payment_status="NOT_DETERMINED", **base) == "PENDING_DOCUMENTS"
    assert logic.overall_risk(["ON_TRACK"], document_status="COMPLETE", tax_payment_status="PARTIALLY_PAID", **base) == "PENDING_TAX"
    assert logic.overall_risk(["ON_TRACK"], document_status="NOT_CONFIGURED", tax_payment_status="NOT_DETERMINED", **base) == "ON_TRACK"


def test_overall_risk_not_generated_and_all_cancelled():
    assert logic.overall_risk([], period_generated=False, document_status=None, tax_payment_status=None, fee_collection_risk="OVERDUE") == "PERIOD_NOT_GENERATED"
    assert logic.overall_risk(["CANCELLED", "NOT_APPLICABLE"], period_generated=True, document_status=None, tax_payment_status=None, fee_collection_risk=None) == "NOT_APPLICABLE"


def test_risk_order_puts_legal_risks_above_fee_collection():
    order = logic.OVERALL_RISK_ORDER
    for legal in ("CRITICAL", "OVERDUE", "DUE_TODAY", "PENDING_TAX", "PENDING_DOCUMENTS"):
        assert order.index(legal) < order.index("PENDING_FEES")


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (dict(period_generated=False, applicable=0, pending=0, filed=0), "NOT_GENERATED"),
        (dict(period_generated=True, applicable=0, pending=0, filed=0), "NOT_APPLICABLE"),
        (dict(period_generated=True, applicable=3, pending=3, filed=0), "NONE_FILED"),
        (dict(period_generated=True, applicable=3, pending=1, filed=2), "PARTIAL"),
        (dict(period_generated=True, applicable=3, pending=0, filed=3), "ALL_FILED"),
    ],
)
def test_filing_progress(kwargs, expected):
    assert logic.filing_progress(**kwargs) == expected


def test_not_determined_declarations_do_not_hide_a_paid_status():
    # una SIRE/PLAME sin liquidacion (NOT_DETERMINED) no debe tapar el PAID de la 621
    assert logic.aggregate_tax_payment_status(["PAID", "NOT_DETERMINED", "NOT_DETERMINED"]) == "PAID"
    assert logic.aggregate_tax_payment_status(["PAYMENT_PENDING", "NOT_DETERMINED"]) == "PAYMENT_PENDING"
    assert logic.aggregate_tax_payment_status(["NOT_DETERMINED", "NOT_DETERMINED"]) == "NOT_DETERMINED"
    assert logic.aggregate_tax_payment_status(["NOT_DETERMINED", "NOT_APPLICABLE"]) == "NOT_APPLICABLE"
