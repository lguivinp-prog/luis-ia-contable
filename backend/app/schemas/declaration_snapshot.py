"""Schemas versionados del snapshot de resolucion de cronograma (ver
ADR-0027). `DeclarationScheduleResolutionV1` es el contenido DETERMINISTA
(hasheable) de una resolucion: nunca incluye metadatos de ejecucion (fecha,
usuario, run). `DeclarationScheduleSnapshotV1` envuelve esa resolucion con
la procedencia (quien/cuando/desde que run) y es lo que finalmente se
guarda en `declarations.schedule_snapshot`. El hash/checksum del preview de
generacion masiva se calcula EXCLUSIVAMENTE sobre la resolucion, nunca sobre
el snapshot completo."""

import hashlib
import json
import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class DeclarationScheduleResolutionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: uuid.UUID
    version_id: uuid.UUID
    version_label: str
    rule_type: str  # "ENTRY" | "EXCEPTION"
    entry_id: uuid.UUID | None = None
    exception_id: uuid.UUID | None = None
    due_date_original: date | None
    due_date_effective: date
    client_category_applied: str | None
    ruc_last_digit_used: int
    normative_reference: str | None
    resolver_warnings: list[str]

    def canonical_json(self) -> str:
        """JSON canonico: claves ordenadas, sin espacios variables, fechas
        normalizadas ISO-8601 (via mode='json'), orden de listas tal como
        vienen (ya deterministas desde el resolver)."""
        data = self.model_dump(mode="json")
        return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class DeclarationScheduleSnapshotV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: DeclarationScheduleResolutionV1
    generated_at: datetime
    generated_by_user_id: uuid.UUID
    generation_run_id: uuid.UUID | None = None


SNAPSHOT_SCHEMA_REGISTRY: dict[int, type[DeclarationScheduleSnapshotV1]] = {
    1: DeclarationScheduleSnapshotV1,
}

CURRENT_SNAPSHOT_SCHEMA_VERSION = 1
