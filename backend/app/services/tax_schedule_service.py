"""Maquina de estados y CRUD de fuentes/versiones de cronogramas tributarios
(ver ADR-0011). Una version APPROVED es inmutable; las correcciones siempre
crean una version nueva. La superacion (SUPERSEDED) solo afecta a la version
previa con el MISMO scope exacto (tenant, fuente, anio fiscal, tipo de
cronograma, categoria de alcance) -- nunca a otros anios/scopes."""

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock
from app.models.tax_schedule import (
    TaxScheduleApprovalHistory,
    TaxScheduleEntry,
    TaxScheduleSource,
    TaxScheduleVersion,
)
from app.repositories import tax_schedule_repository

MUTABLE_STATUSES = {"DRAFT", "IMPORTED"}

VALID_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"PENDING_REVIEW", "CANCELLED"},
    "IMPORTED": {"PENDING_REVIEW", "CANCELLED"},
    "PENDING_REVIEW": {"APPROVED", "REJECTED", "CANCELLED"},
    "APPROVED": {"SUPERSEDED"},
    "SUPERSEDED": set(),
    "REJECTED": set(),
    "CANCELLED": set(),
}


class TaxScheduleSourceNotFoundError(Exception):
    pass


class TaxScheduleVersionNotFoundError(Exception):
    pass


class InvalidTransitionError(Exception):
    pass


class ImmutableVersionError(Exception):
    pass


class ConcurrentApprovalConflictError(Exception):
    pass


class MissingCommentError(Exception):
    pass


def _validate_transition(from_status: str, to_status: str) -> None:
    allowed = VALID_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise InvalidTransitionError(f"Transición inválida: {from_status} -> {to_status}")


def _record_history(
    db: Session,
    tenant_id: uuid.UUID,
    version_id: uuid.UUID,
    from_status: str | None,
    to_status: str,
    actor_user_id: uuid.UUID | None,
    comment: str | None,
) -> TaxScheduleApprovalHistory:
    entry = TaxScheduleApprovalHistory(
        tenant_id=tenant_id,
        version_id=version_id,
        from_status=from_status,
        to_status=to_status,
        actor_user_id=actor_user_id,
        comment=comment,
        created_at=clock.utcnow(),
    )
    return tax_schedule_repository.create_approval_history_entry(db, entry)


def ensure_mutable(version: TaxScheduleVersion) -> None:
    if version.status not in MUTABLE_STATUSES:
        raise ImmutableVersionError(
            f"La versión '{version.version_label}' está en estado {version.status} y no puede "
            "editarse directamente; las correcciones deben crear una versión nueva."
        )


# --- sources ---


def list_sources(db: Session, tenant_id: uuid.UUID) -> list[TaxScheduleSource]:
    return tax_schedule_repository.list_sources(db, tenant_id)


def get_source(db: Session, tenant_id: uuid.UUID, source_id: uuid.UUID) -> TaxScheduleSource:
    source = tax_schedule_repository.get_source(db, tenant_id, source_id)
    if source is None:
        raise TaxScheduleSourceNotFoundError(f"Fuente {source_id} no encontrada")
    return source


def create_source(
    db: Session,
    tenant_id: uuid.UUID,
    obligation_id: uuid.UUID,
    name: str,
    description: str | None,
    reference_url: str | None,
) -> TaxScheduleSource:
    source = TaxScheduleSource(
        tenant_id=tenant_id,
        obligation_id=obligation_id,
        name=name,
        description=description,
        reference_url=reference_url,
        is_active=True,
    )
    return tax_schedule_repository.create_source(db, source)


# --- versions ---


def list_versions(
    db: Session, tenant_id: uuid.UUID, *, source_id: uuid.UUID | None = None
) -> list[TaxScheduleVersion]:
    return tax_schedule_repository.list_versions(db, tenant_id, source_id=source_id)


def get_version(db: Session, tenant_id: uuid.UUID, version_id: uuid.UUID) -> TaxScheduleVersion:
    version = tax_schedule_repository.get_version(db, tenant_id, version_id)
    if version is None:
        raise TaxScheduleVersionNotFoundError(f"Versión {version_id} no encontrada")
    return version


def submit_for_review(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    version_id: uuid.UUID,
    comment: str | None = None,
) -> TaxScheduleVersion:
    version = get_version(db, tenant_id, version_id)
    _validate_transition(version.status, "PENDING_REVIEW")
    from_status = version.status
    version.status = "PENDING_REVIEW"
    db.flush()
    _record_history(db, tenant_id, version.id, from_status, "PENDING_REVIEW", user_id, comment)
    return version


def approve(
    db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, version_id: uuid.UUID, comment: str
) -> TaxScheduleVersion:
    if not comment or not comment.strip():
        raise MissingCommentError("La aprobación requiere un comentario explícito")

    version = get_version(db, tenant_id, version_id)
    _validate_transition(version.status, "APPROVED")

    previous = tax_schedule_repository.get_approved_version(
        db,
        tenant_id,
        version.source_id,
        version.fiscal_year,
        version.schedule_type,
        version.scope_category_code,
    )

    # Se degrada la version previa ANTES de aprobar la nueva, para nunca
    # tener dos filas APPROVED del mismo scope simultaneamente (evita que el
    # propio flush choque con el indice unico parcial).
    if previous is not None and previous.id != version.id:
        previous.status = "SUPERSEDED"
        previous.superseded_by_version_id = version.id
        db.flush()

    from_status = version.status
    version.status = "APPROVED"
    version.approved_by = user_id
    version.approved_at = clock.utcnow()

    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConcurrentApprovalConflictError(
            "Ya existe otra versión aprobada para el mismo alcance exacto "
            "(fuente, año fiscal, tipo de cronograma y categoría). "
            "Posible conflicto de concurrencia: vuelve a intentarlo."
        ) from exc

    if previous is not None and previous.id != version.id:
        _record_history(
            db, tenant_id, previous.id, "APPROVED", "SUPERSEDED", user_id, "Reemplazada automáticamente"
        )
    _record_history(db, tenant_id, version.id, from_status, "APPROVED", user_id, comment)
    return version


def reject(
    db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, version_id: uuid.UUID, reason: str
) -> TaxScheduleVersion:
    if not reason or not reason.strip():
        raise MissingCommentError("El rechazo requiere un motivo explícito")
    version = get_version(db, tenant_id, version_id)
    _validate_transition(version.status, "REJECTED")
    from_status = version.status
    version.status = "REJECTED"
    db.flush()
    _record_history(db, tenant_id, version.id, from_status, "REJECTED", user_id, reason)
    return version


def cancel(
    db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, version_id: uuid.UUID, reason: str
) -> TaxScheduleVersion:
    if not reason or not reason.strip():
        raise MissingCommentError("La cancelación requiere un motivo explícito")
    version = get_version(db, tenant_id, version_id)
    _validate_transition(version.status, "CANCELLED")
    from_status = version.status
    version.status = "CANCELLED"
    db.flush()
    _record_history(db, tenant_id, version.id, from_status, "CANCELLED", user_id, reason)
    return version


def list_approval_history(
    db: Session, tenant_id: uuid.UUID, version_id: uuid.UUID
) -> list[TaxScheduleApprovalHistory]:
    return tax_schedule_repository.list_approval_history(db, tenant_id, version_id)


def _entry_key(entry: TaxScheduleEntry) -> tuple:
    digits = tuple(entry.applicable_last_digits) if entry.applicable_last_digits else None
    return (entry.tax_period, digits, entry.required_category_code)


def compare_versions(
    db: Session, tenant_id: uuid.UUID, version_a_id: uuid.UUID, version_b_id: uuid.UUID
) -> dict:
    version_a = get_version(db, tenant_id, version_a_id)
    version_b = get_version(db, tenant_id, version_b_id)

    entries_a = {
        _entry_key(e): e for e in tax_schedule_repository.list_entries(db, tenant_id, version_a.id)
    }
    entries_b = {
        _entry_key(e): e for e in tax_schedule_repository.list_entries(db, tenant_id, version_b.id)
    }

    added_keys = entries_b.keys() - entries_a.keys()
    removed_keys = entries_a.keys() - entries_b.keys()
    common_keys = entries_a.keys() & entries_b.keys()

    modified = []
    for key in common_keys:
        entry_a, entry_b = entries_a[key], entries_b[key]
        if (
            entry_a.due_date_effective != entry_b.due_date_effective
            or entry_a.due_date_original != entry_b.due_date_original
            or entry_a.due_date_type != entry_b.due_date_type
        ):
            modified.append(
                {
                    "tax_period": key[0],
                    "applicable_last_digits": list(key[1]) if key[1] else None,
                    "required_category_code": key[2],
                    "from_due_date_effective": entry_a.due_date_effective.isoformat(),
                    "to_due_date_effective": entry_b.due_date_effective.isoformat(),
                }
            )

    return {
        "version_a": version_a.version_label,
        "version_b": version_b.version_label,
        "added": [
            {"tax_period": k[0], "applicable_last_digits": list(k[1]) if k[1] else None}
            for k in added_keys
        ],
        "removed": [
            {"tax_period": k[0], "applicable_last_digits": list(k[1]) if k[1] else None}
            for k in removed_keys
        ],
        "modified": modified,
    }
