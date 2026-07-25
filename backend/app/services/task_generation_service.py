"""Generacion automatica idempotente de tareas operativas (Fase 2, Bloque 1,
Seccion 8 del plan / ADR-0059). Nunca llama `commit()`/`rollback()` --el
llamador (router HTTP o CLI) controla la transaccion, exactamente igual que
todo otro servicio del repositorio (ADR-0037). El CLI y `POST /tasks/sync`
invocan esta MISMA funcion -- cero logica duplicada.

Orden global determinista obligatorio (evita interbloqueos entre
sincronizaciones concurrentes, ver Seccion 8 del plan):
1. crear `task_generation_runs`.
2. evaluar las 3 reglas y construir candidatos SIN insertar.
3-4. unir y deduplicar por `generation_key`.
5-6. insertar en orden `generation_key ASC`, uno por uno, `ON CONFLICT DO
   NOTHING RETURNING`.
7-9. obtener IDs de tareas existentes a mutar, deduplicar, ordenar `id ASC`.
10. bloquear y procesar en ese orden exacto.
11. resumen + auditoria.
12. el commit final lo hace el llamador."""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core import clock
from app.core.timezone import end_of_day_utc, today_lima
from app.models.audit_log import AuditAction
from app.models.client import Client
from app.models.declaration import Declaration
from app.models.declaration_document import (
    ClientDocumentRequirement,
    DeclarationDocumentItem,
    DocumentRequirementCatalog,
)
from app.models.operational_task import OperationalTask, TaskChangeHistory, TaskGenerationRun
from app.models.professional_fee import ProfessionalFeeAgreement, ProfessionalFeeCharge
from app.models.tax_period import TaxPeriod
from app.repositories import operational_task_repository, professional_fee_repository, user_repository
from app.services import audit_service
from app.services.declaration_risk import UPCOMING_THRESHOLD_DAYS
from app.services.professional_fee_risk import compute_fee_collection_risk

_TERMINAL_WORKFLOW = ("CLOSED", "NOT_APPLICABLE", "CANCELLED")


class InvalidTriggerSourceError(Exception):
    pass


def _safe_label(value: str | None, fallback: str) -> str:
    if value is None:
        return fallback
    stripped = value.strip()
    return stripped if stripped else fallback


def _fecha(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _active_responsible(db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID | None) -> uuid.UUID | None:
    if user_id is None:
        return None
    user = user_repository.get_by_id_and_tenant(db, tenant_id, user_id)
    return user_id if (user and user.is_active) else None


# --- Regla 1: DECLARATION_FILING ---


def _discover_declaration_filing(db: Session, tenant_id: uuid.UUID, today: date) -> list[dict]:
    threshold = today + timedelta(days=UPCOMING_THRESHOLD_DAYS)
    rows = db.execute(
        select(Declaration, Client, TaxPeriod)
        .join(Client, Declaration.client_id == Client.id)
        .join(TaxPeriod, Declaration.tax_period_id == TaxPeriod.id)
        .where(
            Declaration.tenant_id == tenant_id,
            Declaration.filing_status.notin_(["FILED", "NOT_REQUIRED"]),
            Declaration.workflow_status.notin_(_TERMINAL_WORKFLOW),
            Declaration.current_effective_due_date <= threshold,
        )
    ).all()

    candidates = []
    for declaration, client, period in rows:
        due_date = declaration.current_effective_due_date
        periodo = period.to_display()
        candidates.append(
            {
                "generation_key": f"DECL_FILING:{declaration.id}",
                "task_type": "DECLARATION_FILING",
                "title": (
                    f"Presentar declaración — "
                    f"{_safe_label(client.business_name, 'Cliente sin nombre registrado')} — {periodo}"
                ),
                "description": (
                    f"Preparar y presentar la declaración del periodo {periodo}. "
                    f"Vencimiento efectivo: {_fecha(due_date)}."
                ),
                "priority": "URGENT" if due_date <= today else "HIGH",
                "due_date": due_date,
                "assigned_to": _active_responsible(db, tenant_id, declaration.assigned_user_id),
                "client_id": client.id,
                "declaration_id": declaration.id,
            }
        )
    return candidates


# --- Regla 2: DOCUMENT_COLLECTION ---


def _discover_document_collection(db: Session, tenant_id: uuid.UUID, today: date) -> list[dict]:
    rows = db.execute(
        select(
            DeclarationDocumentItem,
            ClientDocumentRequirement,
            DocumentRequirementCatalog,
            Declaration,
            Client,
            TaxPeriod,
        )
        .join(
            ClientDocumentRequirement,
            DeclarationDocumentItem.client_document_requirement_id == ClientDocumentRequirement.id,
        )
        .join(
            DocumentRequirementCatalog,
            ClientDocumentRequirement.requirement_catalog_id == DocumentRequirementCatalog.id,
        )
        .join(Declaration, DeclarationDocumentItem.declaration_id == Declaration.id)
        .join(Client, DeclarationDocumentItem.client_id == Client.id)
        .join(TaxPeriod, Declaration.tax_period_id == TaxPeriod.id)
        .where(
            DeclarationDocumentItem.tenant_id == tenant_id,
            DeclarationDocumentItem.status.in_(["REQUESTED", "PARTIAL", "OBSERVED"]),
            Declaration.workflow_status.notin_(_TERMINAL_WORKFLOW),
        )
    ).all()

    candidates = []
    for item, requirement, catalog, declaration, client, period in rows:
        lead_days = (
            requirement.reminder_lead_days
            if requirement.reminder_lead_days is not None
            else UPCOMING_THRESHOLD_DAYS
        )
        due_date = declaration.current_effective_due_date - timedelta(days=lead_days)
        periodo = period.to_display()
        requirement_name = _safe_label(catalog.name, "Requisito sin nombre")
        candidates.append(
            {
                "generation_key": f"DOC_COLLECTION:{item.id}",
                "task_type": "DOCUMENT_COLLECTION",
                "title": (
                    f"Recopilar {requirement_name} — "
                    f"{_safe_label(client.business_name, 'Cliente sin nombre registrado')} — {periodo}"
                ),
                "description": (
                    f"Solicitar y verificar el requisito «{requirement_name}» para la declaración del "
                    f"periodo {periodo}. Fecha objetivo de recolección: {_fecha(due_date)}."
                ),
                "priority": "URGENT" if due_date <= today else "HIGH",
                "due_date": due_date,
                "assigned_to": _active_responsible(db, tenant_id, item.responsible_user_id),
                "client_id": client.id,
                "declaration_id": declaration.id,
                "declaration_document_item_id": item.id,
            }
        )
    return candidates


# --- Regla 3: FEE_COLLECTION ---


def _discover_fee_collection(db: Session, tenant_id: uuid.UUID, today: date) -> list[dict]:
    rows = db.execute(
        select(ProfessionalFeeCharge, ProfessionalFeeAgreement, Client)
        .join(ProfessionalFeeAgreement, ProfessionalFeeCharge.agreement_id == ProfessionalFeeAgreement.id)
        .join(Client, ProfessionalFeeAgreement.client_id == Client.id)
        .where(
            ProfessionalFeeCharge.tenant_id == tenant_id,
            ProfessionalFeeCharge.status.notin_(["PAID", "CANCELLED"]),
        )
    ).all()

    charge_ids = [charge.id for charge, _, _ in rows]
    paid_by_charge = professional_fee_repository.sum_valid_amounts_for_charges(db, tenant_id, charge_ids)

    candidates = []
    for charge, agreement, client in rows:
        balance = charge.amount - paid_by_charge.get(charge.id, Decimal("0"))
        risk = compute_fee_collection_risk(
            due_date=charge.due_date, status=charge.status, balance=balance, today=today
        )
        if risk != "OVERDUE" or charge.due_date is None:
            continue
        concept = _safe_label(agreement.service_type, "Concepto sin especificar")
        candidates.append(
            {
                "generation_key": f"FEE_COLLECTION:{charge.id}",
                "task_type": "FEE_COLLECTION",
                "title": (
                    f"Cobrar honorario — "
                    f"{_safe_label(client.business_name, 'Cliente sin nombre registrado')} — "
                    f"{_fecha(charge.due_date)}"
                ),
                "description": (
                    f"Gestionar la cobranza del concepto «{concept}». "
                    f"Fecha de vencimiento: {_fecha(charge.due_date)}."
                ),
                "priority": "URGENT",
                "due_date": charge.due_date,
                "assigned_to": None,
                "client_id": client.id,
                "professional_fee_charge_id": charge.id,
                "professional_fee_agreement_id": agreement.id,
            }
        )
    return candidates


def _insert_candidates(
    db: Session, tenant_id: uuid.UUID, run: TaskGenerationRun, candidates: list[dict], now: datetime
) -> tuple[int, int]:
    created = 0
    already_exists = 0
    ordered = sorted(candidates, key=lambda c: c["generation_key"])
    for candidate in ordered:
        due_at = end_of_day_utc(candidate["due_date"])
        stmt = (
            pg_insert(OperationalTask)
            .values(
                tenant_id=tenant_id,
                client_id=candidate["client_id"],
                declaration_id=candidate.get("declaration_id"),
                declaration_document_item_id=candidate.get("declaration_document_item_id"),
                professional_fee_charge_id=candidate.get("professional_fee_charge_id"),
                professional_fee_agreement_id=candidate.get("professional_fee_agreement_id"),
                title=candidate["title"],
                description=candidate["description"],
                task_type=candidate["task_type"],
                priority=candidate["priority"],
                status="PENDING",
                assigned_to=candidate.get("assigned_to"),
                due_at=due_at,
                due_has_time=False,
                source_due_at=due_at,
                due_is_overridden=False,
                generation_key=candidate["generation_key"],
                is_system_generated=True,
                generation_run_id=run.id,
            )
            .on_conflict_do_nothing(
                index_elements=["tenant_id", "generation_key"],
                index_where=text("generation_key IS NOT NULL"),
            )
            .returning(OperationalTask.id)
        )
        new_id = db.execute(stmt).scalar_one_or_none()
        db.flush()
        if new_id is None:
            already_exists += 1
            continue
        created += 1
        operational_task_repository.create_history(
            db,
            TaskChangeHistory(
                tenant_id=tenant_id,
                task_id=new_id,
                actor_user_id=None,
                created_at=now,
                origin="SYSTEM",
                event_type="CREATED",
                to_status="PENDING",
                to_due_at=due_at,
                to_priority=candidate["priority"],
            ),
        )
    return created, already_exists


def _declaration_filing_action(declaration: Declaration | None) -> tuple[str, str | None]:
    if declaration is None:
        return "noop", None
    if declaration.filing_status == "FILED":
        return "complete", None
    if declaration.filing_status == "NOT_REQUIRED":
        return "cancel", "La declaración ya no requiere presentación"
    if declaration.workflow_status == "CANCELLED":
        return "cancel", "La declaración fue cancelada"
    if declaration.workflow_status == "NOT_APPLICABLE":
        return "cancel", "La declaración ya no aplica"
    if declaration.workflow_status == "CLOSED":
        return "cancel", "La declaración se cerró sin requerir presentación adicional"
    return "realign", None


def _document_collection_action(
    item: DeclarationDocumentItem | None, declaration: Declaration | None
) -> tuple[str, str | None]:
    if item is None or declaration is None:
        return "noop", None
    if item.status in ("RECEIVED", "ACCEPTED"):
        return "complete", None
    if item.status == "NOT_APPLICABLE":
        return "cancel", "El requisito ya no aplica"
    if declaration.workflow_status == "CANCELLED":
        return "cancel", "La declaración vinculada fue cancelada"
    if declaration.workflow_status == "NOT_APPLICABLE":
        return "cancel", "La declaración vinculada ya no aplica"
    if declaration.workflow_status == "CLOSED":
        return "cancel", "La declaración vinculada fue cerrada sin requerir documentación adicional"
    if item.status in ("REQUESTED", "PARTIAL", "OBSERVED"):
        return "realign", None
    if item.status == "NOT_REQUESTED":
        return "cancel", "El requisito ya no está solicitado"
    return "noop", None


def _fee_collection_action(charge: ProfessionalFeeCharge | None) -> tuple[str, str | None]:
    if charge is None:
        return "noop", None
    if charge.status == "PAID":
        return "complete", None
    if charge.status == "CANCELLED":
        return "cancel", "El cargo fue anulado"
    return "realign", None


def _current_source_due_date(
    db: Session, tenant_id: uuid.UUID, task: OperationalTask
) -> date | None:
    if task.task_type == "DECLARATION_FILING":
        declaration = db.get(Declaration, task.declaration_id)
        return declaration.current_effective_due_date if declaration else None
    if task.task_type == "DOCUMENT_COLLECTION":
        item = db.get(DeclarationDocumentItem, task.declaration_document_item_id)
        if item is None:
            return None
        declaration = db.get(Declaration, item.declaration_id)
        if declaration is None:
            return None
        requirement = db.get(ClientDocumentRequirement, item.client_document_requirement_id)
        lead_days = (
            requirement.reminder_lead_days
            if requirement and requirement.reminder_lead_days is not None
            else UPCOMING_THRESHOLD_DAYS
        )
        return declaration.current_effective_due_date - timedelta(days=lead_days)
    if task.task_type == "FEE_COLLECTION":
        charge = db.get(ProfessionalFeeCharge, task.professional_fee_charge_id)
        return charge.due_date if charge else None
    return None


def _resolve_action_for_task(
    db: Session, tenant_id: uuid.UUID, task: OperationalTask
) -> tuple[str, str | None]:
    if task.task_type == "DECLARATION_FILING":
        return _declaration_filing_action(db.get(Declaration, task.declaration_id))
    if task.task_type == "DOCUMENT_COLLECTION":
        item = db.get(DeclarationDocumentItem, task.declaration_document_item_id)
        declaration = db.get(Declaration, item.declaration_id) if item else None
        return _document_collection_action(item, declaration)
    if task.task_type == "FEE_COLLECTION":
        return _fee_collection_action(db.get(ProfessionalFeeCharge, task.professional_fee_charge_id))
    return "noop", None


def _process_existing_tasks(
    db: Session, tenant_id: uuid.UUID, now: datetime
) -> tuple[int, int, int, int, bool]:
    non_terminal = operational_task_repository.list_non_terminal_generated_tasks(db, tenant_id)
    candidate_ids = sorted({t.id for t in non_terminal})

    source_updated = 0
    source_mismatch = 0
    source_completed = 0
    source_cancelled = 0
    any_mutation = False

    for task_id in candidate_ids:
        task = operational_task_repository.lock_exact_task(db, tenant_id, task_id)
        if task is None or task.status in ("COMPLETED", "CANCELLED"):
            continue

        action, reason = _resolve_action_for_task(db, tenant_id, task)

        if action == "complete":
            from_status = task.status
            task.status = "COMPLETED"
            task.completed_at = now
            task.completed_by = None
            task.resolution_origin = "SYSTEM"
            task.version += 1
            db.flush()
            operational_task_repository.create_history(
                db,
                TaskChangeHistory(
                    tenant_id=tenant_id,
                    task_id=task.id,
                    actor_user_id=None,
                    created_at=now,
                    origin="SYSTEM",
                    event_type="SOURCE_COMPLETED",
                    from_status=from_status,
                    to_status=task.status,
                ),
            )
            source_completed += 1
            any_mutation = True
            continue

        if action == "cancel":
            from_status = task.status
            task.status = "CANCELLED"
            task.cancelled_at = now
            task.cancelled_by = None
            task.cancellation_reason = reason
            task.resolution_origin = "SYSTEM"
            task.version += 1
            db.flush()
            operational_task_repository.create_history(
                db,
                TaskChangeHistory(
                    tenant_id=tenant_id,
                    task_id=task.id,
                    actor_user_id=None,
                    created_at=now,
                    origin="SYSTEM",
                    event_type="SOURCE_CANCELLED",
                    from_status=from_status,
                    to_status=task.status,
                    reason=reason,
                ),
            )
            source_cancelled += 1
            any_mutation = True
            continue

        if action == "realign":
            new_source_due_date = _current_source_due_date(db, tenant_id, task)
            if new_source_due_date is None:
                continue
            new_source_due_at = end_of_day_utc(new_source_due_date)
            if new_source_due_at == task.source_due_at:
                continue

            from_source_due_at = task.source_due_at
            from_due_at = task.due_at
            task.source_due_at = new_source_due_at
            if not task.due_is_overridden:
                task.due_at = new_source_due_at
            task.version += 1
            db.flush()
            operational_task_repository.create_history(
                db,
                TaskChangeHistory(
                    tenant_id=tenant_id,
                    task_id=task.id,
                    actor_user_id=None,
                    created_at=now,
                    origin="SYSTEM",
                    event_type="SOURCE_DUE_UPDATED",
                    from_source_due_at=from_source_due_at,
                    to_source_due_at=task.source_due_at,
                    from_due_at=from_due_at if not task.due_is_overridden else None,
                    to_due_at=task.due_at if not task.due_is_overridden else None,
                ),
            )
            any_mutation = True
            if task.due_is_overridden:
                source_mismatch += 1
            else:
                source_updated += 1

    return source_updated, source_mismatch, source_completed, source_cancelled, any_mutation


def sync_operational_tasks(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    trigger_source: str,
    triggered_by: uuid.UUID | None,
) -> TaskGenerationRun:
    now = clock.utcnow()
    today = today_lima(now)

    run = TaskGenerationRun(
        tenant_id=tenant_id,
        triggered_by=triggered_by,
        trigger_source=trigger_source,
        started_at=now,
        summary={},
    )
    operational_task_repository.create_generation_run(db, run)

    candidates: list[dict] = []
    candidates += _discover_declaration_filing(db, tenant_id, today)
    candidates += _discover_document_collection(db, tenant_id, today)
    candidates += _discover_fee_collection(db, tenant_id, today)

    dedup: dict[str, dict] = {}
    for candidate in candidates:
        dedup[candidate["generation_key"]] = candidate

    created, already_exists = _insert_candidates(db, tenant_id, run, list(dedup.values()), now)
    source_updated, source_mismatch, source_completed, source_cancelled, any_mutation = (
        _process_existing_tasks(db, tenant_id, now)
    )

    run.completed_at = clock.utcnow()
    run.summary = {
        "rules_evaluated": 3,
        "created": created,
        "already_exists": already_exists,
        "source_updated": source_updated,
        "source_mismatch": source_mismatch,
        "source_completed": source_completed,
        "source_cancelled": source_cancelled,
        "no_change": 0,
    }
    db.flush()

    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=triggered_by,
        action=AuditAction.TASK_GENERATE,
        entity_type="task_generation_run",
        entity_id=run.id,
        metadata=run.summary,
    )
    if any_mutation:
        audit_service.record(
            db,
            tenant_id=tenant_id,
            user_id=triggered_by,
            action=AuditAction.TASK_SOURCE_UPDATE,
            entity_type="task_generation_run",
            entity_id=run.id,
            metadata=run.summary,
        )

    return run
