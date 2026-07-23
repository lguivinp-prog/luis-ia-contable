"""Generacion masiva idempotente de declaraciones (ver ADR-0021/0027/0028):
preview -> clasificacion persistida por item -> deteccion de preview
obsoleto (STALE) -> confirmacion transaccional con SAVEPOINT por item.

La idempotencia real la garantiza `UNIQUE(tenant_id, client_id,
client_obligation_id, tax_period_id)` en `declarations` + `UNIQUE(run_id,
client_obligation_id)` en los items; este servicio es la capa de
preview/auditoria sobre esa garantia, no la garantia misma."""

import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.models.client import Client
from app.models.declaration_generation import DeclarationGenerationRun, DeclarationGenerationRunItem
from app.models.obligation import ClientObligation
from app.models.tax_schedule import TaxScheduleSource
from app.repositories import (
    declaration_generation_repository,
    declaration_repository,
    obligation_repository,
)
from app.services import declaration_service, declaration_snapshot_service, tax_period_service
from app.services.tax_schedule_resolver import (
    AmbiguousScheduleRuleError,
    NoApplicableScheduleRuleError,
    NoApprovedVersionError,
    resolve_due_date,
)

CLASSIFICATIONS_NON_APTO_TERMINAL = {
    "YA_EXISTENTE",
    "CLIENTE_INACTIVO",
    "OBLIGACION_INACTIVA",
    "SIN_CRONOGRAMA",
    "AMBIGUO",
    "SIN_REGLA_APLICABLE",
    "ERROR_INESPERADO",
}


class GenerationRunNotFoundError(Exception):
    pass


class GenerationRunNotConfirmableError(Exception):
    pass


class GenerationPreviewStaleError(Exception):
    def __init__(self, message: str, stale_diff: list[dict]):
        super().__init__(message)
        self.stale_diff = stale_diff


class GenerationItemNotFoundError(Exception):
    pass


def _classify_one(
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    tax_period,
    client_obligation: ClientObligation,
    client: Client | None,
    source: TaxScheduleSource | None,
    db: Session,
    existing_declaration_id: uuid.UUID | None,
) -> DeclarationGenerationRunItem:
    item = DeclarationGenerationRunItem(
        tenant_id=tenant_id,
        run_id=run_id,
        client_id=client_obligation.client_id,
        client_obligation_id=client_obligation.id,
        created_at=clock.utcnow(),
        excluded_by_user=False,
    )

    if client is None or client.service_status != "activo":
        item.classification = "CLIENTE_INACTIVO"
        return item
    if not client_obligation.is_active:
        item.classification = "OBLIGACION_INACTIVA"
        return item
    if existing_declaration_id is not None:
        item.classification = "YA_EXISTENTE"
        item.declaration_id = existing_declaration_id
        return item
    if source is None or not tax_period.schedule_tax_period_code:
        item.classification = "SIN_CRONOGRAMA"
        return item

    try:
        resolution = resolve_due_date(
            db,
            tenant_id,
            source_id=source.id,
            client_id=client.id,
            last_digit=client.ruc_last_digit,
            tax_period=tax_period.schedule_tax_period_code,
        )
    except NoApprovedVersionError:
        item.classification = "SIN_CRONOGRAMA"
        return item
    except AmbiguousScheduleRuleError:
        item.classification = "AMBIGUO"
        return item
    except NoApplicableScheduleRuleError:
        item.classification = "SIN_REGLA_APLICABLE"
        return item
    except Exception as exc:  # noqa: BLE001 -- clasificacion, nunca debe tumbar el preview completo
        item.classification = "ERROR_INESPERADO"
        item.error_detail = str(exc)
        return item

    resolution_v1 = declaration_snapshot_service.build_resolution(resolution, source_id=source.id)
    rule_type = resolution_v1.rule_type
    item.classification = "APTO"
    item.preview_schedule_source_id = source.id
    item.preview_schedule_version_id = resolution.version_id
    item.preview_schedule_rule_type = rule_type
    item.preview_schedule_entry_id = resolution_v1.entry_id
    item.preview_schedule_exception_id = resolution_v1.exception_id
    item.preview_original_due_date = resolution_v1.due_date_original or resolution_v1.due_date_effective
    item.preview_effective_due_date = resolution_v1.due_date_effective
    item.preview_snapshot_schema_version = 1
    item.preview_resolution_snapshot = resolution_v1.model_dump(mode="json")
    item.preview_resolution_hash = resolution_v1.content_hash()
    return item


def _classify_all(
    db: Session, tenant_id: uuid.UUID, tax_period, run_id: uuid.UUID
) -> list[DeclarationGenerationRunItem]:
    """Itera las asignaciones REALES de client_obligations (ver "Generacion
    desde asignaciones reales"), nunca un producto cartesiano cliente x
    catalogo global. Dos consultas masivas (clientes, fuentes activas) evitan
    N+1 sin importar cuantas asignaciones haya."""
    obligations = obligation_repository.list_all_client_obligations_for_tenant(db, tenant_id)
    if not obligations:
        return []

    client_ids = {o.client_id for o in obligations}
    clients_by_id = {
        c.id: c
        for c in db.execute(
            select(Client).where(Client.tenant_id == tenant_id, Client.id.in_(client_ids))
        )
        .scalars()
        .all()
    }

    obligation_ids = {o.obligation_id for o in obligations}
    sources_by_obligation: dict[uuid.UUID, TaxScheduleSource] = {}
    for source in (
        db.execute(
            select(TaxScheduleSource)
            .where(
                TaxScheduleSource.tenant_id == tenant_id,
                TaxScheduleSource.obligation_id.in_(obligation_ids),
                TaxScheduleSource.is_active.is_(True),
            )
            .order_by(TaxScheduleSource.name)
        )
        .scalars()
        .all()
    ):
        sources_by_obligation.setdefault(source.obligation_id, source)

    items: list[DeclarationGenerationRunItem] = []
    for co in obligations:
        existing = declaration_repository.get_by_natural_key(
            db, tenant_id, co.client_id, co.id, tax_period.id
        )
        items.append(
            _classify_one(
                tenant_id,
                run_id,
                tax_period,
                co,
                clients_by_id.get(co.client_id),
                sources_by_obligation.get(co.obligation_id),
                db,
                existing.id if existing is not None else None,
            )
        )
    return items


def _compute_checksum(items: list[DeclarationGenerationRunItem]) -> str:
    rows = []
    for item in sorted(items, key=lambda i: str(i.client_obligation_id)):
        row: dict = {
            "client_id": str(item.client_id),
            "client_obligation_id": str(item.client_obligation_id),
            "classification": item.classification,
            "excluded_by_user": item.excluded_by_user,
        }
        if item.classification == "APTO":
            row["preview_resolution_hash"] = item.preview_resolution_hash
        rows.append(row)
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_run(db: Session, tenant_id: uuid.UUID, run_id: uuid.UUID) -> DeclarationGenerationRun:
    run = declaration_generation_repository.get_run(db, tenant_id, run_id)
    if run is None:
        raise GenerationRunNotFoundError(f"Run de generacion {run_id} no encontrado")
    return run


def list_items(
    db: Session, tenant_id: uuid.UUID, run_id: uuid.UUID
) -> list[DeclarationGenerationRunItem]:
    get_run(db, tenant_id, run_id)
    return declaration_generation_repository.list_items(db, tenant_id, run_id)


def preview(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    tax_period_id: uuid.UUID,
    filters: dict | None,
) -> DeclarationGenerationRun:
    tax_period = tax_period_service.get_period(db, tenant_id, tax_period_id)
    tax_period_service.ensure_period_open(tax_period)

    run = DeclarationGenerationRun(
        tenant_id=tenant_id,
        tax_period_id=tax_period_id,
        requested_by=user_id,
        requested_at=clock.utcnow(),
        filters=filters,
        status="PENDING",
    )
    declaration_generation_repository.create_run(db, run)

    items = _classify_all(db, tenant_id, tax_period, run.id)
    if items:
        declaration_generation_repository.create_items(db, items)

    run.preview_checksum = _compute_checksum(items)
    run.total_analyzed = len(items)
    run.total_skipped = sum(1 for i in items if i.classification != "APTO")
    run.status = "PREVIEWED"
    db.flush()
    return run


def set_item_excluded(
    db: Session, tenant_id: uuid.UUID, run_id: uuid.UUID, item_id: uuid.UUID, excluded: bool
) -> DeclarationGenerationRunItem:
    run = get_run(db, tenant_id, run_id)
    if run.status != "PREVIEWED":
        raise GenerationRunNotConfirmableError(
            "Solo se pueden excluir items de un run en estado PREVIEWED"
        )
    item = declaration_generation_repository.get_item(db, tenant_id, run_id, item_id)
    if item is None:
        raise GenerationItemNotFoundError(f"Item {item_id} no encontrado")
    item.excluded_by_user = excluded
    db.flush()

    items = declaration_generation_repository.list_items(db, tenant_id, run_id)
    run.preview_checksum = _compute_checksum(items)
    db.flush()
    return item


def cancel_run(db: Session, tenant_id: uuid.UUID, run_id: uuid.UUID) -> DeclarationGenerationRun:
    run = get_run(db, tenant_id, run_id)
    if run.status != "PREVIEWED":
        raise GenerationRunNotConfirmableError("Solo un run PREVIEWED puede cancelarse")
    run.status = "CANCELLED"
    db.flush()
    return run


def refresh_preview(db: Session, tenant_id: uuid.UUID, run_id: uuid.UUID) -> DeclarationGenerationRun:
    run = get_run(db, tenant_id, run_id)
    if run.status != "STALE":
        raise GenerationRunNotConfirmableError("Solo un run STALE admite refresh-preview")

    tax_period = tax_period_service.get_period(db, tenant_id, run.tax_period_id)
    declaration_generation_repository.delete_items_for_run(db, tenant_id, run.id)
    items = _classify_all(db, tenant_id, tax_period, run.id)
    if items:
        declaration_generation_repository.create_items(db, items)

    run.preview_checksum = _compute_checksum(items)
    run.stale_diff = None
    run.total_analyzed = len(items)
    run.total_skipped = sum(1 for i in items if i.classification != "APTO")
    run.status = "PREVIEWED"
    db.flush()
    return run


def _prevalidate_not_stale(
    db: Session, tenant_id: uuid.UUID, run: DeclarationGenerationRun
) -> None:
    """Re-resuelve todos los items APTO no excluidos ANTES de abrir cualquier
    SAVEPOINT de insercion (ver "Deteccion de preview obsoleto"). Si algo
    cambio, el run pasa a STALE y no se crea ninguna declaracion."""
    tax_period = tax_period_service.get_period(db, tenant_id, run.tax_period_id)
    items = declaration_generation_repository.list_items(db, tenant_id, run.id)

    diffs = []
    for item in items:
        if item.classification != "APTO" or item.excluded_by_user:
            continue
        client_obligation = obligation_repository.get_client_obligation_by_id(
            db, tenant_id, item.client_obligation_id
        )
        if client_obligation is None:
            diffs.append({"item_id": str(item.id), "reason": "obligacion ya no existe"})
            continue
        client = db.get(Client, item.client_id)
        existing_decl = declaration_repository.get_by_natural_key(
            db, tenant_id, item.client_id, item.client_obligation_id, tax_period.id
        )
        existing = existing_decl.id if existing_decl is not None else None

        fresh = _classify_one(
            tenant_id,
            run.id,
            tax_period,
            client_obligation,
            client,
            _active_source_for_item(db, tenant_id, item),
            db,
            existing,
        )
        if fresh.classification != item.classification or (
            fresh.classification == "APTO" and fresh.preview_resolution_hash != item.preview_resolution_hash
        ):
            diffs.append(
                {
                    "item_id": str(item.id),
                    "client_obligation_id": str(item.client_obligation_id),
                    "previous_classification": item.classification,
                    "new_classification": fresh.classification,
                    "previous_resolution_hash": item.preview_resolution_hash,
                    "new_resolution_hash": fresh.preview_resolution_hash,
                }
            )

    if diffs:
        run.status = "STALE"
        run.stale_diff = diffs
        db.flush()
        raise GenerationPreviewStaleError(
            "El preview quedo desactualizado respecto al estado actual; se requiere refresh-preview",
            diffs,
        )


def _active_source_for_item(
    db: Session, tenant_id: uuid.UUID, item: DeclarationGenerationRunItem
) -> TaxScheduleSource | None:
    if item.preview_schedule_source_id is None:
        return None
    return db.get(TaxScheduleSource, item.preview_schedule_source_id)


def confirm(
    db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, run_id: uuid.UUID
) -> DeclarationGenerationRun:
    run = get_run(db, tenant_id, run_id)

    if run.status in ("COMPLETED", "COMPLETED_WITH_ERRORS"):
        return run  # idempotente: retorna el resultado ya existente
    if run.status in ("STALE", "CANCELLED", "PENDING"):
        raise GenerationRunNotConfirmableError(
            f"Un run en estado {run.status} no puede confirmarse directamente"
        )
    if run.status not in ("PREVIEWED", "FAILED"):
        raise GenerationRunNotConfirmableError(f"Estado de run no confirmable: {run.status}")

    # Fase 1: prevalidacion global -- ANTES de cualquier SAVEPOINT de insercion.
    _prevalidate_not_stale(db, tenant_id, run)

    run.started_at = clock.utcnow()
    db.flush()

    created = 0
    skipped = 0
    failed = 0
    errors: list[dict] = []

    try:
        items = declaration_generation_repository.list_items(db, tenant_id, run.id)
        for item in items:
            if item.classification != "APTO" or item.excluded_by_user:
                skipped += 1
                continue

            savepoint = db.begin_nested()
            try:
                declaration = declaration_service.create_declaration(
                    db,
                    tenant_id,
                    user_id,
                    client_id=item.client_id,
                    client_obligation_id=item.client_obligation_id,
                    tax_period_id=run.tax_period_id,
                    source_id=item.preview_schedule_source_id,
                    origin="GENERATION",
                    generation_run_id=run.id,
                )
            except declaration_service.DuplicateDeclarationError:
                savepoint.rollback()
                item.classification = "YA_EXISTENTE"
                skipped += 1
                continue
            except Exception as exc:  # noqa: BLE001 -- error recuperable, contenido por el SAVEPOINT
                savepoint.rollback()
                item.classification = "ERROR_INESPERADO"
                item.error_detail = str(exc)
                failed += 1
                errors.append({"item_id": str(item.id), "error": str(exc)})
                continue

            savepoint.commit()
            item.declaration_id = declaration.id
            created += 1

        db.flush()
    except Exception:
        db.rollback()
        _mark_run_failed(db, tenant_id, run.id)
        raise

    run.total_created = created
    run.total_skipped = skipped
    run.total_failed = failed
    run.errors = errors or None
    run.completed_at = clock.utcnow()
    run.status = "COMPLETED_WITH_ERRORS" if failed else "COMPLETED"
    db.flush()
    return run


def _mark_run_failed(db: Session, tenant_id: uuid.UUID, run_id: uuid.UUID) -> None:
    """Registra FAILED en una transaccion independiente de la que se revirtio
    (ver "Transaccion de confirmacion masiva"): nunca queda un run FAILED con
    declaraciones parcialmente confirmadas."""
    run = declaration_generation_repository.get_run(db, tenant_id, run_id)
    if run is None:
        return
    run.status = "FAILED"
    run.completed_at = clock.utcnow()
    db.flush()
