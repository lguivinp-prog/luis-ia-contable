"""Maquinas de estado de `Declaration` (ver ADR-0018/0022): transiciones no
lineales con regresiones auditadas, `row_version` optimista, y cierre
explicitamente restringido durante 3.1 (tax_assessments/declaration_document_items
no existen todavia)."""

import pytest

from app.core.config import get_settings
from app.services import declaration_service
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
def declaration_setup(db_session, tenant, admin_user, obligation_catalog):
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
    )
    return declaration


def test_initial_history_has_5_table_birth_rows(db_session, tenant, declaration_setup) -> None:
    history = declaration_service.get_history(db_session, tenant.id, declaration_setup.id)
    assert len(history["due_date_history"]) == 1
    assert history["due_date_history"][0].previous_schedule_snapshot is None
    assert len(history["status_history"]) == 4
    fields = {h.status_field for h in history["status_history"]}
    assert fields == {"WORKFLOW", "FILING", "TAX_PAYMENT", "DOCUMENT"}
    assert all(h.from_value is None for h in history["status_history"])
    assert len(history["assignment_history"]) == 0  # no habia asignacion inicial

    correlation_ids = {h.audit_correlation_id for h in history["status_history"]}
    correlation_ids |= {h.audit_correlation_id for h in history["due_date_history"]}
    assert len(correlation_ids) == 1  # todas las filas de nacimiento comparten audit_correlation_id


def test_workflow_regression_requires_reason(db_session, tenant, admin_user, declaration_setup) -> None:
    d = declaration_setup
    d = declaration_service.change_workflow_status(
        db_session, tenant.id, admin_user.id, d.id,
        target="DOCUMENTS_REQUESTED", reason=None, row_version=d.row_version,
    )
    d = declaration_service.change_workflow_status(
        db_session, tenant.id, admin_user.id, d.id,
        target="DOCUMENTS_COMPLETE", reason=None, row_version=d.row_version,
    )
    with pytest.raises(declaration_service.MissingWorkflowRegressionReasonError):
        declaration_service.change_workflow_status(
            db_session, tenant.id, admin_user.id, d.id,
            target="INFORMATION_INCOMPLETE", reason=None, row_version=d.row_version,
        )
    # con motivo si funciona
    d = declaration_service.change_workflow_status(
        db_session, tenant.id, admin_user.id, d.id,
        target="INFORMATION_INCOMPLETE", reason="Falta un documento", row_version=d.row_version,
    )
    assert d.workflow_status == "INFORMATION_INCOMPLETE"


def test_invalid_workflow_transition_rejected(db_session, tenant, admin_user, declaration_setup) -> None:
    d = declaration_setup
    with pytest.raises(declaration_service.InvalidWorkflowTransitionError):
        declaration_service.change_workflow_status(
            db_session, tenant.id, admin_user.id, d.id,
            target="UNDER_REVIEW", reason=None, row_version=d.row_version,
        )


def test_row_version_conflict_returns_stale_error(db_session, tenant, admin_user, declaration_setup) -> None:
    d = declaration_setup
    with pytest.raises(declaration_service.StaleRowVersionError):
        declaration_service.change_workflow_status(
            db_session, tenant.id, admin_user.id, d.id,
            target="DOCUMENTS_REQUESTED", reason=None, row_version=d.row_version + 999,
        )


def test_filing_requires_presentation_date(db_session, tenant, admin_user, declaration_setup) -> None:
    d = declaration_setup
    d = declaration_service.change_workflow_status(
        db_session, tenant.id, admin_user.id, d.id,
        target="DOCUMENTS_REQUESTED", reason=None, row_version=d.row_version,
    )
    d = declaration_service.file_declaration(
        db_session, tenant.id, admin_user.id, d.id,
        target="READY", presentation_date=None, order_number=None, reason=None, row_version=d.row_version,
    )
    with pytest.raises(declaration_service.FilingRequiresPresentationDateError):
        declaration_service.file_declaration(
            db_session, tenant.id, admin_user.id, d.id,
            target="FILED", presentation_date=None, order_number=None, reason=None, row_version=d.row_version,
        )


def test_filing_reference_persisted_on_filed_and_in_history(
    db_session, tenant, admin_user, declaration_setup
) -> None:
    d = declaration_setup
    d = declaration_service.file_declaration(
        db_session, tenant.id, admin_user.id, d.id,
        target="READY", presentation_date=None, order_number=None, reason=None, row_version=d.row_version,
    )
    d = declaration_service.file_declaration(
        db_session, tenant.id, admin_user.id, d.id,
        target="FILED", presentation_date="2026-02-10", order_number="ORD-1",
        filing_reference="REF-001", reason=None, row_version=d.row_version,
    )
    assert d.filing_reference == "REF-001"

    history = declaration_service.get_history(db_session, tenant.id, d.id)
    filed_entry = next(h for h in history["filing_history"] if h.to_filing_status == "FILED")
    assert filed_entry.filing_reference == "REF-001"


def test_correct_filing_requires_reason(db_session, tenant, admin_user, declaration_setup) -> None:
    d = declaration_setup
    with pytest.raises(declaration_service.MissingRecalculationReasonError):
        declaration_service.correct_filing(
            db_session, tenant.id, admin_user.id, d.id, target="NOT_REQUIRED", reason="",
        )


def test_correct_filing_from_filed_clears_filing_fields(
    db_session, tenant, admin_user, declaration_setup
) -> None:
    d = declaration_setup
    d = declaration_service.file_declaration(
        db_session, tenant.id, admin_user.id, d.id,
        target="READY", presentation_date=None, order_number=None, reason=None, row_version=d.row_version,
    )
    d = declaration_service.file_declaration(
        db_session, tenant.id, admin_user.id, d.id,
        target="FILED", presentation_date="2026-02-10", order_number="ORD-1",
        filing_reference="REF-001", reason=None, row_version=d.row_version,
    )
    assert d.filing_status == "FILED"

    corrected = declaration_service.correct_filing(
        db_session, tenant.id, admin_user.id, d.id,
        target="NOT_FILED", reason="Se presento por error, se anula administrativamente",
    )
    assert corrected.filing_status == "NOT_FILED"
    assert corrected.presentation_date is None
    assert corrected.order_number is None
    assert corrected.filing_reference is None

    history = declaration_service.get_history(db_session, tenant.id, d.id)
    correction_entry = history["filing_history"][-1]
    assert correction_entry.from_filing_status == "FILED"
    assert correction_entry.to_filing_status == "NOT_FILED"
    assert correction_entry.reason == "Se presento por error, se anula administrativamente"


def test_close_normal_blocked_for_applicable_declaration_in_3_1(
    db_session, tenant, admin_user, declaration_setup
) -> None:
    d = declaration_setup
    with pytest.raises(declaration_service.CloseRequiresPendingOverrideError):
        declaration_service.close(
            db_session, tenant.id, admin_user.id, d.id,
            force_close_with_pending=False, close_override_reason=None,
        )


def test_force_close_with_pending_disabled_in_3_1(db_session, tenant, admin_user, declaration_setup) -> None:
    d = declaration_setup
    assert settings.allow_close_with_pending_tax is False
    with pytest.raises(declaration_service.CloseOverrideDisabledError):
        declaration_service.close(
            db_session, tenant.id, admin_user.id, d.id,
            force_close_with_pending=True, close_override_reason="motivo forzado",
        )


def test_not_applicable_is_terminal_and_reachable(db_session, tenant, admin_user, declaration_setup) -> None:
    d = declaration_setup
    d = declaration_service.change_workflow_status(
        db_session, tenant.id, admin_user.id, d.id,
        target="NOT_APPLICABLE", reason=None, row_version=d.row_version,
    )
    assert d.workflow_status == "NOT_APPLICABLE"
    with pytest.raises(declaration_service.InvalidWorkflowTransitionError):
        declaration_service.close(
            db_session, tenant.id, admin_user.id, d.id,
            force_close_with_pending=False, close_override_reason=None,
        )


def test_cancel_requires_reason_and_is_terminal(db_session, tenant, admin_user, declaration_setup) -> None:
    d = declaration_setup
    with pytest.raises(declaration_service.MissingCancellationReasonError):
        declaration_service.cancel(db_session, tenant.id, admin_user.id, d.id, reason="")

    d = declaration_service.cancel(db_session, tenant.id, admin_user.id, d.id, reason="Ya no aplica")
    assert d.workflow_status == "CANCELLED"
    with pytest.raises(declaration_service.DeclarationCancelledError):
        declaration_service.change_workflow_status(
            db_session, tenant.id, admin_user.id, d.id,
            target="DOCUMENTS_REQUESTED", reason=None, row_version=d.row_version,
        )


def test_recalculate_requires_reason_and_conserves_previous_snapshot(
    db_session, tenant, admin_user, declaration_setup
) -> None:
    d = declaration_setup
    with pytest.raises(declaration_service.MissingRecalculationReasonError):
        declaration_service.recalculate(
            db_session, tenant.id, admin_user.id, d.id, reason="", row_version=d.row_version
        )

    original_row_version = d.row_version
    original_initial_due_date = d.initial_effective_due_date
    updated = declaration_service.recalculate(
        db_session, tenant.id, admin_user.id, d.id, reason="Verificacion periodica", row_version=d.row_version
    )
    assert updated.row_version == original_row_version + 1
    assert updated.initial_effective_due_date == original_initial_due_date  # nunca cambia

    history = declaration_service.get_history(db_session, tenant.id, d.id)
    assert len(history["due_date_history"]) == 2
    last = history["due_date_history"][-1]
    assert last.previous_snapshot_schema_version == 1
    assert last.new_snapshot_schema_version == 1


def test_reopen_requires_closed_state_and_valid_target(
    db_session, tenant, admin_user, declaration_setup
) -> None:
    d = declaration_setup
    with pytest.raises(declaration_service.InvalidReopenTargetError):
        declaration_service.reopen(
            db_session, tenant.id, admin_user.id, d.id,
            reason="motivo", target_workflow_status="IN_PROGRESS", row_version=d.row_version,
        )
