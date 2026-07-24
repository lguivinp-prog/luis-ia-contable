"""Esquemas del reporte de integridad de archivos documentales (Bloque 5,
Dominio F, ver Seccion 12/17 del plan -- ADR-0055). Nunca expone
`storage_key` ni ninguna ruta fisica: las entradas sin fila en BD usan
`blob_fingerprint` (hash de la clave relativa) en vez de la clave real."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

IntegrityCategory = Literal[
    "missing_blob",
    "orphan_blob",
    "hash_mismatch",
    "retained_deleted_blob",
    "temporary_part",
]


class IntegrityReportEntryOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    category: IntegrityCategory
    # Presentes solo para entradas con fila en BD (missing_blob, hash_mismatch,
    # retained_deleted_blob):
    file_id: str | None = None
    logical_file_id: str | None = None
    declaration_document_item_id: str | None = None
    original_filename: str | None = None
    status: str | None = None
    expected_hash: str | None = None
    actual_hash: str | None = None
    # Presentes solo para entradas sin fila en BD (orphan_blob, temporary_part):
    blob_fingerprint: str | None = None
    basename: str | None = None
    size_bytes: int | None = None
    modified_at: datetime | None = None


class IntegrityReportOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[IntegrityReportEntryOut]
    total: int
    limit: int
    offset: int
    counts: dict[str, int]
