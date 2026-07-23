"""Control documental por declaracion (ver "Control documental" y
"Sincronizacion documental" del plan / ADR-0035/0036). Ningun metodo de
este servicio ejecuta `commit()`/`rollback()`: usa `flush()` cuando necesita
un ID o quiere que una violacion de restriccion se manifieste de inmediato;
el router es quien controla el limite transaccional (ver ADR-0037)."""

import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.core import clock
from app.models.declaration import Declaration
from app.models.declaration_document import (
    ClientDocumentRequirement,
    DeclarationDocumentItem,
    DocumentItemStatusHistory,
)
from app.repositories import declaration_document_repository
from app.schemas.declaration_document import DocumentFileMetadataV1
from app.services import document_requirement_catalog_service, tax_period_service
from app.services.tax_schedule_resolver import resolve_reference_date

_NOT_APPLICABLE = "NOT_APPLICABLE"
_OBSERVED = "OBSERVED"
_ACCEPTED = "ACCEPTED"
_PENDING_STATUSES = {"NOT_REQUESTED", "REQUESTED"}
_RECEIVED_LIKE_STATUSES = {"RECEIVED", "PARTIAL", _ACCEPTED}


class ClientDocumentRequirementNotFoundError(Exception):
    pass


class DeclarationDocumentItemNotFoundError(Exception):
    pass


class InvalidFileMetadataError(Exception):
    pass


def compute_document_status(item_statuses: list[str]) -> str:
    """Funcion pura y determinista (ver seccion 6/9 del plan). Nunca acepta
    un valor desde payload; se recalcula transaccionalmente tras cada cambio
    de item."""
    if not item_statuses:
        return "NOT_CONFIGURED"

    statuses = set(item_statuses)

    if statuses <= {_NOT_APPLICABLE}:
        return _NOT_APPLICABLE
    if _OBSERVED in statuses:
        return _OBSERVED
    if statuses <= {_ACCEPTED, _NOT_APPLICABLE} and _ACCEPTED in statuses:
        return "COMPLETE"
    if statuses <= _PENDING_STATUSES:
        return "PENDING"
    if (statuses & _RECEIVED_LIKE_STATUSES) and (statuses & _PENDING_STATUSES):
        return "PARTIAL"
    return "PARTIAL"


def _recalculate_document_status(
    db: Session, tenant_id: uuid.UUID, declaration: Declaration
) -> None:
    items = declaration_document_repository.list_items_for_declaration(
        db, tenant_id, declaration.id
    )
    declaration.document_status = compute_document_status([i.status for i in items])
    db.flush()


def _validate_file_metadata(file_metadata: dict | None) -> dict | None:
    if file_metadata is None:
        return None
    try:
        validated = DocumentFileMetadataV1.model_validate(file_metadata)
    except Exception as exc:  # noqa: BLE001 -- se traduce a una excepcion de dominio propia
        raise InvalidFileMetadataError(
            "file_metadata solo admite original_filename/mime_type/size_bytes/notes "
            "-- nunca una ruta o URL real"
        ) from exc
    return validated.model_dump(mode="json", exclude_none=True)


# --- asignaciones (client_document_requirements) ---


def assign_requirement(
    db: Session,
    tenant_id: uuid.UUID,
    client_id: uuid.UUID,
    *,
    requirement_catalog_id: uuid.UUID,
    client_obligation_id: uuid.UUID | None,
    is_mandatory: bool,
    periodicity: str,
    valid_from: date,
    valid_until: date | None,
    description: str | None,
    responsible_user_id: uuid.UUID | None,
    reminder_lead_days: int,
) -> ClientDocumentRequirement:
    catalog_entry = document_requirement_catalog_service.get_catalog_entry(
        db, tenant_id, requirement_catalog_id
    )
    document_requirement_catalog_service.validate_catalog_scope(catalog_entry, tenant_id)

    requirement = ClientDocumentRequirement(
        tenant_id=tenant_id,
        client_id=client_id,
        requirement_catalog_id=requirement_catalog_id,
        client_obligation_id=client_obligation_id,
        is_mandatory=is_mandatory,
        periodicity=periodicity,
        valid_from=valid_from,
        valid_until=valid_until,
        description=description,
        responsible_user_id=responsible_user_id,
        reminder_lead_days=reminder_lead_days,
        is_active=True,
    )
    return declaration_document_repository.create_client_requirement(db, requirement)


def list_client_requirements(
    db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID
) -> list[ClientDocumentRequirement]:
    return declaration_document_repository.list_client_requirements(db, tenant_id, client_id)


# --- sincronizacion documental ---


def _reference_date_for_period(tax_period) -> date:
    if tax_period.schedule_tax_period_code:
        return resolve_reference_date(tax_period.schedule_tax_period_code)
    return tax_period.end_date


def sync_requirements_for_declaration(
    db: Session,
    tenant_id: uuid.UUID,
    declaration: Declaration,
    *,
    actor_user_id: uuid.UUID | None,
    origin: str = "MANUAL",
) -> dict:
    """Materializa `declaration_document_items` faltantes para una
    declaracion, de forma idempotente (ver "Sincronizacion documental").
    Nunca elimina/desactiva items existentes, aunque una asignacion cambie
    despues. Se puede invocar cuantas veces se quiera."""
    tax_period = tax_period_service.get_period(db, tenant_id, declaration.tax_period_id)
    tax_period_service.ensure_period_open(tax_period)

    reference_date = _reference_date_for_period(tax_period)
    requirements = declaration_document_repository.list_active_requirements_for_sync(
        db, tenant_id, declaration.client_id, declaration.client_obligation_id, reference_date
    )

    created: list[uuid.UUID] = []
    existing: list[uuid.UUID] = []
    errors: list[str] = []

    for requirement in requirements:
        existing_item = declaration_document_repository.get_item_by_requirement(
            db, tenant_id, declaration.id, requirement.id
        )
        if existing_item is not None:
            existing.append(existing_item.id)
            continue

        item = DeclarationDocumentItem(
            tenant_id=tenant_id,
            client_id=declaration.client_id,
            declaration_id=declaration.id,
            client_document_requirement_id=requirement.id,
            status="NOT_REQUESTED",
        )
        declaration_document_repository.create_item(db, item)
        created.append(item.id)

    _recalculate_document_status(db, tenant_id, declaration)

    return {"created": created, "existing": existing, "skipped": [], "errors": errors}


# --- items ---


def get_item(
    db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID, item_id: uuid.UUID
) -> DeclarationDocumentItem:
    item = declaration_document_repository.get_item(db, tenant_id, declaration_id, item_id)
    if item is None:
        raise DeclarationDocumentItemNotFoundError(f"Item documental {item_id} no encontrado")
    return item


def list_items(
    db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID
) -> list[DeclarationDocumentItem]:
    return declaration_document_repository.list_items_for_declaration(db, tenant_id, declaration_id)


def change_item_status(
    db: Session,
    tenant_id: uuid.UUID,
    declaration: Declaration,
    item_id: uuid.UUID,
    *,
    target: str,
    reason: str | None,
    file_metadata: dict | None,
    actor_user_id: uuid.UUID | None,
) -> DeclarationDocumentItem:
    item = get_item(db, tenant_id, declaration.id, item_id)
    from_status = item.status
    item.status = target

    now = clock.utcnow()
    if target == "REQUESTED" and item.requested_at is None:
        item.requested_at = now
    if target in ("RECEIVED", "PARTIAL") and item.received_at is None:
        item.received_at = now
    if target in ("ACCEPTED", "OBSERVED"):
        item.reviewed_at = now
    if file_metadata is not None:
        item.file_metadata = _validate_file_metadata(file_metadata)

    db.flush()

    declaration_document_repository.create_item_status_history_entry(
        db,
        DocumentItemStatusHistory(
            tenant_id=tenant_id,
            declaration_document_item_id=item.id,
            actor_user_id=actor_user_id,
            created_at=now,
            reason=reason,
            origin="MANUAL",
            from_status=from_status,
            to_status=target,
        ),
    )

    _recalculate_document_status(db, tenant_id, declaration)
    return item
