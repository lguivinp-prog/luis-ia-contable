"""M4: motor de comparacion SIRE (puro, determinista, con Decimal)."""

from datetime import date
from decimal import Decimal

import pytest

from app.services import sire_control_logic as L

D = Decimal


def snap(origin, *, rc=None, base=None, igv=None, total=None, currency="PEN", sid="s", version=1, ch="h"):
    return L.SourceSnapshot(
        id=f"{sid}-{origin}", origin=origin, version=version, entry_mode="MANUAL", currency=currency, source_label="fuente", source_date=date(2026, 10, 1), content_hash=ch,
        record_count=rc, base_amount=None if base is None else D(base), igv_amount=None if igv is None else D(igv), total_amount=None if total is None else D(total),
    )


def test_amount_parsing_is_strict_decimal_and_never_rounds():
    assert L.parse_amount("1234.50") == D("1234.50") and L.parse_amount(10) == D("10.00") and L.parse_amount(None) is None and L.parse_amount("  ") is None
    for bad in ("1.234", "abc", "NaN", "Infinity", 1.5, True, "1e30", "9999999999999999.99"):
        with pytest.raises(L.AmountError):
            L.parse_amount(bad)
    assert L.parse_amount("-12.30") == D("-12.30")


def test_no_sources_and_single_source_are_never_a_match():
    assert L.compare(None, None).status == L.NO_SOURCES
    only_sunat = L.compare(snap("SUNAT", rc=3, total="100.00"), None)
    assert only_sunat.status == L.PARTIAL and only_sunat.single_source == "SUNAT" and only_sunat.concepts == ()
    assert L.compare(None, snap("DARWIN", rc=3)).single_source == "DARWIN"


def test_exact_match_requires_equal_concepts_known_on_both_sides():
    result = L.compare(snap("SUNAT", rc=10, base="1000.00", igv="180.00", total="1180.00"), snap("DARWIN", rc=10, base="1000.00", igv="180.00", total="1180.00"))
    assert result.status == L.MATCH and all(c.exact and c.matches for c in result.concepts) and not result.tolerance_applied
    assert [c.concept for c in result.concepts] == ["record_count", "base_amount", "igv_amount", "total_amount"]


def test_differences_are_reported_original_and_signed_darwin_minus_sunat():
    result = L.compare(snap("SUNAT", rc=10, base="1000.00", total="1180.00"), snap("DARWIN", rc=9, base="1000.00", total="1175.50"))
    assert result.status == L.DIFFERENCES
    by = {c.concept: c for c in result.concepts}
    assert by["record_count"].difference == "-1" and by["total_amount"].difference == "-4.50" and by["base_amount"].exact is True


def test_unknown_values_are_never_zero_and_one_sided_concepts_make_it_partial():
    # IGV solo en SUNAT: no se asume 0 en DARWIN. Lo comparable cuadra, pero la informacion es parcial.
    result = L.compare(snap("SUNAT", rc=5, igv="18.00", total="118.00"), snap("DARWIN", rc=5, total="118.00"))
    assert result.status == L.PARTIAL and result.one_sided_concepts == ("igv_amount",)
    igv = next(c for c in result.concepts if c.concept == "igv_amount")
    assert igv.comparable is False and igv.difference is None and igv.darwin is None
    # una diferencia real sigue siendo DIFFERENCES aunque haya conceptos de un solo lado
    assert L.compare(snap("SUNAT", rc=5, igv="18.00", total="118.00"), snap("DARWIN", rc=5, total="119.00")).status == L.DIFFERENCES


def test_no_common_concept_or_currency_mismatch_is_not_comparable():
    assert L.compare(snap("SUNAT", rc=5), snap("DARWIN", total="100.00")).status == L.NOT_COMPARABLE
    mismatch = L.compare(snap("SUNAT", total="100.00"), snap("DARWIN", total="100.00", currency="USD"))
    assert mismatch.status == L.NOT_COMPARABLE and mismatch.currency_mismatch and mismatch.concepts == ()


def test_tolerance_is_explicit_only_for_amounts_and_never_hides_the_original_difference():
    sunat, darwin = snap("SUNAT", rc=10, total="100.00"), snap("DARWIN", rc=10, total="100.40")
    exact = L.compare(sunat, darwin)
    assert exact.status == L.DIFFERENCES  # por defecto: comparacion EXACTA
    tolerant = L.compare(sunat, darwin, D("0.50"))
    total = next(c for c in tolerant.concepts if c.concept == "total_amount")
    assert tolerant.status == L.MATCH and tolerant.tolerance_applied and total.difference == "0.40" and total.exact is False and total.within_tolerance is True
    assert "MATCH_WITH_TOLERANCE" in tolerant.messages
    # la tolerancia NO aplica a cantidades de registros
    assert L.compare(snap("SUNAT", rc=10), snap("DARWIN", rc=11), D("5.00")).status == L.DIFFERENCES
    assert L.compare(sunat, snap("DARWIN", rc=10, total="101.00"), D("0.50")).status == L.DIFFERENCES  # fuera de tolerancia


def test_comparison_hash_changes_with_any_input_and_is_stable():
    a, b = snap("SUNAT", rc=1, total="1.00", ch="x"), snap("DARWIN", rc=1, total="1.00", ch="y")
    base = L.comparison_hash(a, b, None)
    assert base == L.comparison_hash(a, b, None) and len(base) == 64
    assert base != L.comparison_hash(snap("SUNAT", rc=1, total="1.00", ch="z"), b, None)  # fuente nueva
    assert base != L.comparison_hash(a, snap("DARWIN", rc=1, total="1.00", ch="y", version=2), None)  # nueva version
    assert base != L.comparison_hash(a, b, D("0.10"))  # tolerancia
    assert base != L.comparison_hash(a, None, None)


def test_review_rules():
    L.validate_review(L.DECISION_APPROVED, L.MATCH, None)
    for decision, status, notes, code in (
        (L.DECISION_APPROVED, L.DIFFERENCES, None, "REVIEW_APPROVAL_REQUIRES_MATCH"),
        (L.DECISION_APPROVED, L.PARTIAL, "ok", "REVIEW_APPROVAL_REQUIRES_MATCH"),
        (L.DECISION_OBSERVED, L.DIFFERENCES, "  ", "REVIEW_NOTES_REQUIRED"),
        (L.DECISION_REGULARIZE, L.DIFFERENCES, None, "REVIEW_NOTES_REQUIRED"),
        (L.DECISION_OBSERVED, L.NO_SOURCES, "x", "REVIEW_NOTHING_TO_REVIEW"),
        ("OTHER", L.MATCH, "x", "REVIEW_DECISION_INVALID"),
    ):
        with pytest.raises(L.ReviewRuleError) as raised:
            L.validate_review(decision, status, notes)
        assert raised.value.code == code
    L.validate_review(L.DECISION_OBSERVED, L.DIFFERENCES, "falta el comprobante 12")


def test_summary_code_for_the_cockpit():
    assert L.summary_code(exists=False, status=L.NO_SOURCES, review_decision=None, review_stale=False) == "NO_CONTROL"
    assert L.summary_code(exists=True, status=L.PARTIAL, review_decision=None, review_stale=False) == "PARTIAL"
    assert L.summary_code(exists=True, status=L.MATCH, review_decision=None, review_stale=False) == "MATCH"
    assert L.summary_code(exists=True, status=L.MATCH, review_decision="APPROVED", review_stale=False) == "MATCH_REVIEWED"
    assert L.summary_code(exists=True, status=L.DIFFERENCES, review_decision="OBSERVED", review_stale=False) == "DIFFERENCES_REVIEWED"
    assert L.summary_code(exists=True, status=L.MATCH, review_decision="APPROVED", review_stale=True) == "NEEDS_REVIEW"


def test_the_engine_never_uses_float_and_disclaims_official_filing():
    import inspect

    code = inspect.getsource(L)
    assert "float(" not in code
    assert "NO constituye constancia oficial" in L.DISCLAIMER
