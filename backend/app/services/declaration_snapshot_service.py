"""Construccion y validacion del snapshot de resolucion de cronograma (ver
ADR-0017/ADR-0027). Ensambla el `ScheduleResolution` del motor de
cronogramas en los dos schemas versionados y valida contra el registro de
schemas ANTES de persistir -- si la validacion falla no se guarda nada."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from app.schemas.declaration_snapshot import (
    CURRENT_SNAPSHOT_SCHEMA_VERSION,
    SNAPSHOT_SCHEMA_REGISTRY,
    DeclarationScheduleResolutionV1,
    DeclarationScheduleSnapshotV1,
)
from app.services.tax_schedule_resolver import ScheduleResolution


class SnapshotSchemaValidationError(Exception):
    pass


def build_resolution(
    resolution: ScheduleResolution,
    *,
    source_id: uuid.UUID,
) -> DeclarationScheduleResolutionV1:
    rule_type = "ENTRY" if resolution.rule_type == "entry" else "EXCEPTION"
    entry_id = resolution.rule_id if rule_type == "ENTRY" else None
    exception_id = resolution.rule_id if rule_type == "EXCEPTION" else None
    return DeclarationScheduleResolutionV1(
        source_id=source_id,
        version_id=resolution.version_id,
        version_label=resolution.version_label,
        rule_type=rule_type,
        entry_id=entry_id,
        exception_id=exception_id,
        due_date_original=resolution.due_date_original,
        due_date_effective=resolution.due_date_effective,
        client_category_applied=resolution.matched_category_code,
        ruc_last_digit_used=resolution.last_digit,
        normative_reference=None,
        resolver_warnings=list(resolution.warnings),
    )


def build_snapshot(
    resolution: DeclarationScheduleResolutionV1,
    *,
    generated_at: datetime,
    generated_by_user_id: uuid.UUID,
    generation_run_id: uuid.UUID | None,
) -> DeclarationScheduleSnapshotV1:
    return DeclarationScheduleSnapshotV1(
        resolution=resolution,
        generated_at=generated_at,
        generated_by_user_id=generated_by_user_id,
        generation_run_id=generation_run_id,
    )


def validate_and_dump(
    snapshot: DeclarationScheduleSnapshotV1, schema_version: int = CURRENT_SNAPSHOT_SCHEMA_VERSION
) -> dict[str, Any]:
    schema_cls = SNAPSHOT_SCHEMA_REGISTRY.get(schema_version)
    if schema_cls is None:
        raise SnapshotSchemaValidationError(
            f"Version de schema de snapshot desconocida: {schema_version}"
        )
    try:
        validated = schema_cls.model_validate(snapshot.model_dump(mode="json"))
    except ValidationError as exc:
        raise SnapshotSchemaValidationError(
            f"El snapshot no cumple el schema version {schema_version}: {exc}"
        ) from exc
    return validated.model_dump(mode="json")
