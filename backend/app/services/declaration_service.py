"""Ciclo de vida de una `Declaration` (ver ADR-0017/0018/0022/0028): creacion
con snapshot versionado e historial inicial completo (5 tablas), maquinas de
estado con regresiones auditadas, recalculo con snapshot par completo,
cierre restringido durante 3.1, reapertura y cancelacion."""

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.models.declaration import (
    Declaration,
    DeclarationAssignmentHistory,
    DeclarationDueDateHistory,
    DeclarationReopenHistory,
    DeclarationStatusHistory,
    FilingHistory,
)
from app.repositories import declaration_repository, obligation_repository, tax_schedule_repository
from app.schemas.declaration import RecalculatePreviewOut
from app.services import client_service, declaration_snapshot_service, tax_period_service
from app.services.tax_schedule_resolver import ScheduleResolution, resolve_due_date

settings = get_settings()

WORKFLOW_TRANSITIONS: dict[str, set[str]] = {
    "PENDING": {"DOCUMENTS_REQUESTED", "NOT_APPLICABLE", "CANCELLED"},
    "DOCUMENTS_REQUESTED": {"INFORMATION_INCOMPLETE", "DOCUMENTS_COMPLETE", "NOT_APPLICABLE", "CANCELLED"},
    "INFORMATION_INCOMPLETE": {"DOCUMENTS_COMPLETE", "NOT_APPLICABLE", "CANCELLED"},
    "DOCUMENTS_COMPLETE": {"IN_PROGRESS", "INFORMATION_INCOMPLETE", "NOT_APPLICABLE", "CANCELLED"},
    "IN_PROGRESS": {"UNDER_REVIEW", "NOT_APPLICABLE", "CANCELLED"},
    "UNDER_REVIEW": {"READY_TO_FILE", "IN_PROGRESS", "NOT_APPLICABLE", "CANCELLED"},
    "READY_TO_FILE": {"UNDER_REVIEW", "NOT_APPLICABLE", "CANCELLED"},
    "CLOSED": set(),
    "NOT_APPLICABLE": set(),
    "CANCELLED": set(),
}

# Regresiones que exigen `reason` no vacio (ver "Maquinas de estado").
WORKFLOW_REGRESSIONS: set[tuple[str, str]] = {
    ("DOCUMENTS_COMPLETE", "INFORMATION_INCOMPLETE"),
    ("UNDER_REVIEW", "IN_PROGRESS"),
    ("READY_TO_FILE", "UNDER_REVIEW"),
}

FILING_TRANSITIONS: dict[str, set[str]] = {
    "NOT_FILED": {"READY", "NOT_REQUIRED"},
    "READY": {"FILED", "FAILED"},
    "FAILED": {"READY"},
    "FILED": set(),
    "NOT_REQUIRED": set(),
}

CLOSE_ELIGIBLE_FILING = {"FILED", "NOT_REQUIRED"}
CLOSE_ELIGIBLE_TAX_PAYMENT = {"PAID", "NO_TAX_DUE", "CREDIT_BALANCE", "NOT_APPLICABLE"}
CLOSE_ELIGIBLE_DOCUMENT = {"COMPLETE", "NOT_APPLICABLE"}


class DeclarationNotFoundError(Exception):
    pass


class DuplicateDeclarationError(Exception):
    pass


class InvalidWorkflowTransitionError(Exception):
    pass


class MissingWorkflowRegressionReasonError(Exception):
    pass


class InvalidFilingTransitionError(Exception):
    pass


class ReadyToFileRequiresDocumentsOrJustificationError(Exception):
    pass


class FilingRequiresPresentationDateError(Exception):
    pass


class CloseRequiresPendingOverrideError(Exception):
    pass


class CloseOverrideDisabledError(Exception):
    pass


class MissingCloseOverrideReasonError(Exception):
    pass


class DeclarationClosedError(Exception):
    pass


class DeclarationCancelledError(Exception):
    pass


class MissingCancellationReasonError(Exception):
    pass


class MissingReopenReasonError(Exception):
    pass


class InvalidReopenTargetError(Exception):
    pass


class MissingRecalculationReasonError(Exception):
    pass


class StaleRowVersionError(Exception):
    pass


class ScheduleNotResolvableForPeriodError(Exception):
    pass


def get_declaration(db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID) -> Declaration:
    declaration = declaration_repository.get(db, tenant_id, declaration_id)
    if declaration is None:
        raise DeclarationNotFoundError(f"Declaracion {declaration_id} no encontrada")
    return declaration


def _check_row_version(declaration: Declaration, expected: int) -> None:
    if declaration.row_version != expected:
        raise StaleRowVersionError(
            f"row_version desactualizado: esperado {expected}, actual {declaration.row_version}"
        )


def _resolve_schedule(
    db,
    tenant_id: uuid.UUID,
    *,
    source_id: uuid.UUID,
    client_id: uuid.UUID,
    last_digit: int,
    schedule_tax_period_code: str | None,
) -> ScheduleResolution:
    if not schedule_tax_period_code:
        raise ScheduleNotResolvableForPeriodError(
            "El periodo no tiene un schedule_tax_period_code valido para resolver el cronograma"
        )
    return resolve_due_date(
        db,
        tenant_id,
        source_id=source_id,
        client_id=client_id,
        last_digit=last_digit,
        tax_period=schedule_tax_period_code,
    )


def _record_initial_history(
    db: Session,
    tenant_id: uuid.UUID,
    declaration: Declaration,
    *,
    actor_user_id: uuid.UUID | None,
    origin: str,
    correlation_id: uuid.UUID,
    new_snapshot: dict,
    new_snapshot_schema_version: int,
) -> None:
    """Escribe, en la misma transaccion que crea la declaracion, historial
    inicial en las 5 tablas (ver "Historial inicial completo de la
    declaracion") -- no solo el vencimiento."""
    now = clock.utcnow()

    declaration_repository.create_due_date_history_entry(
        db,
        DeclarationDueDateHistory(
            tenant_id=tenant_id,
            declaration_id=declaration.id,
            actor_user_id=actor_user_id,
            created_at=now,
            origin=origin,
            audit_correlation_id=correlation_id,
            from_original_due_date=None,
            to_original_due_date=declaration.current_original_due_date,
            from_effective_due_date=None,
            to_effective_due_date=declaration.current_effective_due_date,
            from_source_id=None,
            to_source_id=declaration.schedule_source_id,
            from_version_id=None,
            to_version_id=declaration.schedule_version_id,
            from_rule_type=None,
            to_rule_type=declaration.schedule_rule_type,
            from_entry_id=None,
            to_entry_id=declaration.schedule_entry_id,
            from_exception_id=None,
            to_exception_id=declaration.schedule_exception_id,
            previous_snapshot_schema_version=None,
            new_snapshot_schema_version=new_snapshot_schema_version,
            previous_schedule_snapshot=None,
            new_schedule_snapshot=new_snapshot,
        ),
    )

    for field, to_value in (
        ("WORKFLOW", declaration.workflow_status),
        ("FILING", declaration.filing_status),
        ("TAX_PAYMENT", declaration.tax_payment_status),
        ("DOCUMENT", declaration.document_status),
    ):
        declaration_repository.create_status_history_entry(
            db,
            DeclarationStatusHistory(
                tenant_id=tenant_id,
                declaration_id=declaration.id,
                actor_user_id=actor_user_id,
                created_at=now,
                origin=origin,
                audit_correlation_id=correlation_id,
                status_field=field,
                from_value=None,
                to_value=to_value,
            ),
        )

    if declaration.assigned_user_id is not None:
        declaration_repository.create_assignment_history_entry(
            db,
            DeclarationAssignmentHistory(
                tenant_id=tenant_id,
                declaration_id=declaration.id,
                actor_user_id=actor_user_id,
                created_at=now,
                origin=origin,
                audit_correlation_id=correlation_id,
                from_user_id=None,
                to_user_id=declaration.assigned_user_id,
            ),
        )


def create_declaration(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    *,
    client_id: uuid.UUID,
    client_obligation_id: uuid.UUID,
    tax_period_id: uuid.UUID,
    source_id: uuid.UUID | None = None,
    assigned_user_id: uuid.UUID | None = None,
    origin: str = "MANUAL",
    generation_run_id: uuid.UUID | None = None,
) -> Declaration:
    tax_period = tax_period_service.get_period(db, tenant_id, tax_period_id)
    tax_period_service.ensure_period_open(tax_period)

    client_obligation = obligation_repository.get_client_obligation_by_id(
        db, tenant_id, client_obligation_id
    )
    if client_obligation is None or client_obligation.client_id != client_id:
        raise DeclarationNotFoundError("La obligacion asignada no existe para este cliente")

    client = client_service.get_client(db, tenant_id, client_id)

    resolved_source_id = source_id
    if resolved_source_id is None:
        source = tax_schedule_repository.get_active_source_for_obligation(
            db, tenant_id, client_obligation.obligation_id
        )
        if source is None:
            raise ScheduleNotResolvableForPeriodError(
                "No hay una fuente de cronograma activa para esta obligacion"
            )
        resolved_source_id = source.id

    resolution = _resolve_schedule(
        db,
        tenant_id,
        source_id=resolved_source_id,
        client_id=client_id,
        last_digit=client.ruc_last_digit,
        schedule_tax_period_code=tax_period.schedule_tax_period_code,
    )

    resolution_v1 = declaration_snapshot_service.build_resolution(
        resolution, source_id=resolved_source_id
    )
    now = clock.utcnow()
    snapshot_v1 = declaration_snapshot_service.build_snapshot(
        resolution_v1,
        generated_at=now,
        generated_by_user_id=user_id or uuid.UUID(int=0),
        generation_run_id=generation_run_id,
    )
    snapshot_dict = declaration_snapshot_service.validate_and_dump(snapshot_v1)

    original_due_date = resolution.due_date_original or resolution.due_date_effective
    rule_type = "ENTRY" if resolution.rule_type == "entry" else "EXCEPTION"

    declaration = Declaration(
        tenant_id=tenant_id,
        client_id=client_id,
        client_obligation_id=client_obligation_id,
        tax_period_id=tax_period_id,
        assigned_user_id=assigned_user_id,
        workflow_status="PENDING",
        filing_status="NOT_FILED",
        tax_payment_status="NOT_DETERMINED",
        document_status="NOT_CONFIGURED",
        initial_effective_due_date=resolution.due_date_effective,
        current_original_due_date=original_due_date,
        current_effective_due_date=resolution.due_date_effective,
        schedule_source_id=resolved_source_id,
        schedule_version_id=resolution.version_id,
        schedule_rule_type=rule_type,
        schedule_entry_id=resolution.rule_id if rule_type == "ENTRY" else None,
        schedule_exception_id=resolution.rule_id if rule_type == "EXCEPTION" else None,
        schedule_snapshot=snapshot_dict,
        schedule_snapshot_schema_version=1,
        row_version=1,
    )

    try:
        declaration_repository.create(db, declaration)
    except IntegrityError as exc:
        # No se hace rollback aqui: quien llama decide el alcance correcto
        # (un endpoint aislado hace rollback() completo; la confirmacion
        # masiva -- ver declaration_generation_service.confirm -- corre esto
        # dentro de un SAVEPOINT propio por item y solo revierte ese
        # SAVEPOINT, para no invalidar el resto de la transaccion).
        raise DuplicateDeclarationError(
            "Ya existe una declaracion para este cliente, obligacion y periodo"
        ) from exc

    correlation_id = uuid.uuid4()
    _record_initial_history(
        db,
        tenant_id,
        declaration,
        actor_user_id=user_id,
        origin=origin,
        correlation_id=correlation_id,
        new_snapshot=snapshot_dict,
        new_snapshot_schema_version=1,
    )
    return declaration


def change_workflow_status(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    declaration_id: uuid.UUID,
    *,
    target: str,
    reason: str | None,
    row_version: int,
) -> Declaration:
    declaration = get_declaration(db, tenant_id, declaration_id)
    _check_row_version(declaration, row_version)
    _ensure_not_terminal(declaration)

    current = declaration.workflow_status
    allowed = WORKFLOW_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidWorkflowTransitionError(f"Transicion invalida: {current} -> {target}")

    if (current, target) in WORKFLOW_REGRESSIONS and (not reason or not reason.strip()):
        raise MissingWorkflowRegressionReasonError(
            f"La regresion {current} -> {target} requiere un motivo explicito"
        )

    if target == "READY_TO_FILE":
        if declaration.document_status != "COMPLETE" and not (reason and reason.strip()):
            raise ReadyToFileRequiresDocumentsOrJustificationError(
                "READY_TO_FILE requiere documentos completos o una justificacion explicita"
            )
        if declaration.document_status != "COMPLETE":
            declaration.ready_to_file_override_reason = reason

    declaration.workflow_status = target
    declaration.row_version += 1
    db.flush()

    declaration_repository.create_status_history_entry(
        db,
        DeclarationStatusHistory(
            tenant_id=tenant_id,
            declaration_id=declaration.id,
            actor_user_id=user_id,
            created_at=clock.utcnow(),
            reason=reason,
            origin="MANUAL",
            status_field="WORKFLOW",
            from_value=current,
            to_value=target,
        ),
    )
    return declaration


def _ensure_not_terminal(declaration: Declaration) -> None:
    if declaration.workflow_status == "CLOSED":
        raise DeclarationClosedError("La declaracion esta cerrada")
    if declaration.workflow_status == "CANCELLED":
        raise DeclarationCancelledError("La declaracion esta cancelada")


def assign(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    declaration_id: uuid.UUID,
    *,
    target_user_id: uuid.UUID | None,
    reason: str | None,
    row_version: int,
) -> Declaration:
    declaration = get_declaration(db, tenant_id, declaration_id)
    _check_row_version(declaration, row_version)
    _ensure_not_terminal(declaration)

    from_user_id = declaration.assigned_user_id
    declaration.assigned_user_id = target_user_id
    declaration.row_version += 1
    db.flush()

    declaration_repository.create_assignment_history_entry(
        db,
        DeclarationAssignmentHistory(
            tenant_id=tenant_id,
            declaration_id=declaration.id,
            actor_user_id=user_id,
            created_at=clock.utcnow(),
            reason=reason,
            origin="MANUAL",
            from_user_id=from_user_id,
            to_user_id=target_user_id,
        ),
    )
    return declaration


def file_declaration(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    declaration_id: uuid.UUID,
    *,
    target: str,
    presentation_date,
    order_number: str | None,
    reason: str | None,
    row_version: int,
) -> Declaration:
    declaration = get_declaration(db, tenant_id, declaration_id)
    _check_row_version(declaration, row_version)
    _ensure_not_terminal(declaration)

    current = declaration.filing_status
    allowed = FILING_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidFilingTransitionError(f"Transicion de presentacion invalida: {current} -> {target}")

    if target == "FILED" and presentation_date is None:
        raise FilingRequiresPresentationDateError("FILED requiere presentation_date")

    declaration.filing_status = target
    if target == "FILED":
        declaration.presentation_date = presentation_date
        declaration.order_number = order_number
    declaration.row_version += 1
    db.flush()

    declaration_repository.create_filing_history_entry(
        db,
        FilingHistory(
            tenant_id=tenant_id,
            declaration_id=declaration.id,
            actor_user_id=user_id,
            created_at=clock.utcnow(),
            reason=reason,
            origin="MANUAL",
            from_filing_status=current,
            to_filing_status=target,
            presentation_date=presentation_date if target == "FILED" else None,
            order_number=order_number if target == "FILED" else None,
        ),
    )
    return declaration


def correct_filing(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    declaration_id: uuid.UUID,
    *,
    target: str,
    reason: str,
) -> Declaration:
    if not reason or not reason.strip():
        raise MissingRecalculationReasonError("La correccion de presentacion requiere un motivo")
    declaration = get_declaration(db, tenant_id, declaration_id)
    _ensure_not_terminal(declaration)

    current = declaration.filing_status
    declaration.filing_status = target
    if target != "FILED":
        declaration.presentation_date = None
        declaration.order_number = None
    declaration.row_version += 1
    db.flush()

    declaration_repository.create_filing_history_entry(
        db,
        FilingHistory(
            tenant_id=tenant_id,
            declaration_id=declaration.id,
            actor_user_id=user_id,
            created_at=clock.utcnow(),
            reason=reason,
            origin="MANUAL",
            from_filing_status=current,
            to_filing_status=target,
            presentation_date=declaration.presentation_date,
            order_number=declaration.order_number,
        ),
    )
    return declaration


def recalculate_preview(
    db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID
) -> RecalculatePreviewOut:
    declaration = get_declaration(db, tenant_id, declaration_id)
    client = client_service.get_client(db, tenant_id, declaration.client_id)
    tax_period = tax_period_service.get_period(db, tenant_id, declaration.tax_period_id)

    resolution = _resolve_schedule(
        db,
        tenant_id,
        source_id=declaration.schedule_source_id,
        client_id=declaration.client_id,
        last_digit=client.ruc_last_digit,
        schedule_tax_period_code=tax_period.schedule_tax_period_code,
    )
    to_rule_type = "ENTRY" if resolution.rule_type == "entry" else "EXCEPTION"
    to_original = resolution.due_date_original or resolution.due_date_effective

    changed = (
        to_original != declaration.current_original_due_date
        or resolution.due_date_effective != declaration.current_effective_due_date
        or resolution.version_id != declaration.schedule_version_id
        or to_rule_type != declaration.schedule_rule_type
    )

    return RecalculatePreviewOut(
        from_original_due_date=declaration.current_original_due_date,
        from_effective_due_date=declaration.current_effective_due_date,
        to_original_due_date=to_original,
        to_effective_due_date=resolution.due_date_effective,
        from_version_label="",
        to_version_label=resolution.version_label,
        from_rule_type=declaration.schedule_rule_type,
        to_rule_type=to_rule_type,
        changed=changed,
    )


def recalculate(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    declaration_id: uuid.UUID,
    *,
    reason: str,
    row_version: int,
) -> Declaration:
    if not reason or not reason.strip():
        raise MissingRecalculationReasonError("El recalculo requiere un motivo explicito")
    declaration = get_declaration(db, tenant_id, declaration_id)
    _check_row_version(declaration, row_version)
    _ensure_not_terminal(declaration)

    tax_period = tax_period_service.get_period(db, tenant_id, declaration.tax_period_id)
    tax_period_service.ensure_period_open(tax_period)
    client = client_service.get_client(db, tenant_id, declaration.client_id)

    resolution = _resolve_schedule(
        db,
        tenant_id,
        source_id=declaration.schedule_source_id,
        client_id=declaration.client_id,
        last_digit=client.ruc_last_digit,
        schedule_tax_period_code=tax_period.schedule_tax_period_code,
    )
    resolution_v1 = declaration_snapshot_service.build_resolution(
        resolution, source_id=declaration.schedule_source_id
    )
    now = clock.utcnow()
    snapshot_v1 = declaration_snapshot_service.build_snapshot(
        resolution_v1,
        generated_at=now,
        generated_by_user_id=user_id or uuid.UUID(int=0),
        generation_run_id=None,
    )
    new_snapshot_dict = declaration_snapshot_service.validate_and_dump(snapshot_v1)

    from_original = declaration.current_original_due_date
    from_effective = declaration.current_effective_due_date
    from_source_id = declaration.schedule_source_id
    from_version_id = declaration.schedule_version_id
    from_rule_type = declaration.schedule_rule_type
    from_entry_id = declaration.schedule_entry_id
    from_exception_id = declaration.schedule_exception_id
    previous_snapshot = declaration.schedule_snapshot
    previous_snapshot_schema_version = declaration.schedule_snapshot_schema_version

    to_rule_type = "ENTRY" if resolution.rule_type == "entry" else "EXCEPTION"
    to_original = resolution.due_date_original or resolution.due_date_effective

    declaration.current_original_due_date = to_original
    declaration.current_effective_due_date = resolution.due_date_effective
    declaration.schedule_version_id = resolution.version_id
    declaration.schedule_rule_type = to_rule_type
    declaration.schedule_entry_id = resolution.rule_id if to_rule_type == "ENTRY" else None
    declaration.schedule_exception_id = resolution.rule_id if to_rule_type == "EXCEPTION" else None
    declaration.schedule_snapshot = new_snapshot_dict
    declaration.schedule_snapshot_schema_version = 1
    declaration.row_version += 1
    db.flush()

    declaration_repository.create_due_date_history_entry(
        db,
        DeclarationDueDateHistory(
            tenant_id=tenant_id,
            declaration_id=declaration.id,
            actor_user_id=user_id,
            created_at=now,
            reason=reason,
            origin="RECALCULATION",
            from_original_due_date=from_original,
            to_original_due_date=to_original,
            from_effective_due_date=from_effective,
            to_effective_due_date=resolution.due_date_effective,
            from_source_id=from_source_id,
            to_source_id=declaration.schedule_source_id,
            from_version_id=from_version_id,
            to_version_id=resolution.version_id,
            from_rule_type=from_rule_type,
            to_rule_type=to_rule_type,
            from_entry_id=from_entry_id,
            to_entry_id=declaration.schedule_entry_id,
            from_exception_id=from_exception_id,
            to_exception_id=declaration.schedule_exception_id,
            previous_snapshot_schema_version=previous_snapshot_schema_version,
            new_snapshot_schema_version=1,
            previous_schedule_snapshot=previous_snapshot,
            new_schedule_snapshot=new_snapshot_dict,
        ),
    )
    return declaration


def reopen(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    declaration_id: uuid.UUID,
    *,
    reason: str,
    target_workflow_status: str,
    row_version: int,
) -> Declaration:
    if not reason or not reason.strip():
        raise MissingReopenReasonError("La reapertura requiere un motivo explicito")
    declaration = get_declaration(db, tenant_id, declaration_id)
    _check_row_version(declaration, row_version)
    if declaration.workflow_status != "CLOSED":
        raise InvalidReopenTargetError("Solo una declaracion CLOSED puede reabrirse")
    if target_workflow_status in ("NOT_APPLICABLE", "CANCELLED", "CLOSED"):
        raise InvalidReopenTargetError(
            f"'{target_workflow_status}' no es un estado valido de reapertura"
        )
    if target_workflow_status not in WORKFLOW_TRANSITIONS:
        raise InvalidReopenTargetError(f"Estado desconocido: {target_workflow_status}")

    from_workflow = declaration.workflow_status
    declaration.workflow_status = target_workflow_status
    declaration.closed_at = None
    declaration.closed_by = None
    declaration.closed_with_pending = False
    declaration.close_override_reason = None
    declaration.row_version += 1
    db.flush()

    now = clock.utcnow()
    db.add(
        DeclarationReopenHistory(
            tenant_id=tenant_id,
            declaration_id=declaration.id,
            actor_user_id=user_id,
            created_at=now,
            reason=reason,
            origin="MANUAL",
            from_workflow_status=from_workflow,
            to_workflow_status=target_workflow_status,
        )
    )
    declaration_repository.create_status_history_entry(
        db,
        DeclarationStatusHistory(
            tenant_id=tenant_id,
            declaration_id=declaration.id,
            actor_user_id=user_id,
            created_at=now,
            reason=reason,
            origin="MANUAL",
            status_field="WORKFLOW",
            from_value=from_workflow,
            to_value=target_workflow_status,
        ),
    )
    db.flush()
    return declaration


def close(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    declaration_id: uuid.UUID,
    *,
    force_close_with_pending: bool,
    close_override_reason: str | None,
) -> Declaration:
    declaration = get_declaration(db, tenant_id, declaration_id)
    _ensure_not_terminal(declaration)
    if declaration.workflow_status == "NOT_APPLICABLE":
        raise InvalidWorkflowTransitionError("NOT_APPLICABLE es terminal y no admite cierre")

    preconditions_met = (
        declaration.filing_status in CLOSE_ELIGIBLE_FILING
        and declaration.tax_payment_status in CLOSE_ELIGIBLE_TAX_PAYMENT
        and declaration.document_status in CLOSE_ELIGIBLE_DOCUMENT
    )

    if not preconditions_met:
        if not force_close_with_pending:
            raise CloseRequiresPendingOverrideError(
                "El cierre normal requiere filing/impuesto/documentos en un estado terminal; "
                "usa force_close_with_pending si corresponde"
            )
        if not settings.allow_close_with_pending_tax:
            raise CloseOverrideDisabledError(
                "El cierre con impuesto/documentos pendientes esta deshabilitado en esta fase "
                "(disponible desde la subentrega 3.2)"
            )
        if not close_override_reason or not close_override_reason.strip():
            raise MissingCloseOverrideReasonError(
                "El cierre con pendientes requiere close_override_reason explicito"
            )

    from_workflow = declaration.workflow_status
    declaration.workflow_status = "CLOSED"
    declaration.closed_at = clock.utcnow()
    declaration.closed_by = user_id
    declaration.closed_with_pending = not preconditions_met
    declaration.close_override_reason = close_override_reason if not preconditions_met else None
    declaration.row_version += 1
    db.flush()

    declaration_repository.create_status_history_entry(
        db,
        DeclarationStatusHistory(
            tenant_id=tenant_id,
            declaration_id=declaration.id,
            actor_user_id=user_id,
            created_at=clock.utcnow(),
            reason=close_override_reason,
            origin="MANUAL",
            status_field="WORKFLOW",
            from_value=from_workflow,
            to_value="CLOSED",
        ),
    )
    return declaration


def cancel(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    declaration_id: uuid.UUID,
    *,
    reason: str,
) -> Declaration:
    if not reason or not reason.strip():
        raise MissingCancellationReasonError("La cancelacion requiere un motivo explicito")
    declaration = get_declaration(db, tenant_id, declaration_id)
    _ensure_not_terminal(declaration)
    if declaration.workflow_status == "NOT_APPLICABLE":
        raise InvalidWorkflowTransitionError("NOT_APPLICABLE es terminal y no admite cancelacion")

    from_workflow = declaration.workflow_status
    declaration.workflow_status = "CANCELLED"
    declaration.cancelled_at = clock.utcnow()
    declaration.cancellation_reason = reason
    declaration.row_version += 1
    db.flush()

    declaration_repository.create_status_history_entry(
        db,
        DeclarationStatusHistory(
            tenant_id=tenant_id,
            declaration_id=declaration.id,
            actor_user_id=user_id,
            created_at=clock.utcnow(),
            reason=reason,
            origin="MANUAL",
            status_field="WORKFLOW",
            from_value=from_workflow,
            to_value="CANCELLED",
        ),
    )
    return declaration


def get_history(db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID) -> dict:
    get_declaration(db, tenant_id, declaration_id)
    return {
        "status_history": declaration_repository.list_status_history(db, tenant_id, declaration_id),
        "due_date_history": declaration_repository.list_due_date_history(
            db, tenant_id, declaration_id
        ),
        "assignment_history": declaration_repository.list_assignment_history(
            db, tenant_id, declaration_id
        ),
        "filing_history": declaration_repository.list_filing_history(db, tenant_id, declaration_id),
        "reopen_history": declaration_repository.list_reopen_history(db, tenant_id, declaration_id),
    }
