"""Reporte de integridad BD-disco para archivos documentales (Bloque 5,
Dominio F, ver Seccion 12 del plan -- ADR-0055). Nunca expone `storage_key`
ni ninguna ruta fisica -- las entradas sin fila en BD usan
`blob_fingerprint = SHA256(clave_relativa)` en vez de la clave real."""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.file_storage import FileStorageBackend
from app.models.declaration_document_file import DeclarationDocumentFile
from app.repositories import declaration_document_file_repository as file_repository
from app.services.document_file_naming import sanitize_original_filename

_HASH_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class IntegrityEntry:
    category: str
    file_id: str | None = None
    logical_file_id: str | None = None
    declaration_document_item_id: str | None = None
    original_filename: str | None = None
    status: str | None = None
    expected_hash: str | None = None
    actual_hash: str | None = None
    blob_fingerprint: str | None = None
    basename: str | None = None
    size_bytes: int | None = None
    modified_at: datetime | None = None


@dataclass(frozen=True)
class IntegrityReport:
    items: list[IntegrityEntry]
    total: int
    counts: dict[str, int]


def _recompute_hash(storage: FileStorageBackend, key: str) -> str:
    hasher = hashlib.sha256()
    with storage.open(key) as fh:
        while True:
            chunk = fh.read(_HASH_CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _row_entry(
    category: str, file: DeclarationDocumentFile, *, actual_hash: str | None = None
) -> IntegrityEntry:
    return IntegrityEntry(
        category=category,
        file_id=str(file.id),
        logical_file_id=str(file.logical_file_id),
        declaration_document_item_id=str(file.declaration_document_item_id),
        original_filename=file.original_filename,
        status=file.status,
        expected_hash=file.sha256_hash,
        actual_hash=actual_hash,
    )


def _blob_only_entry(storage: FileStorageBackend, key: str, *, category: str) -> IntegrityEntry:
    stat = storage.stat(key)
    return IntegrityEntry(
        category=category,
        blob_fingerprint=hashlib.sha256(key.encode("utf-8")).hexdigest(),
        basename=sanitize_original_filename(key.rsplit("/", 1)[-1]),
        size_bytes=stat.size_bytes,
        modified_at=datetime.fromtimestamp(stat.modified_at, tz=UTC),
    )


def build_integrity_report(
    db: Session,
    storage: FileStorageBackend,
    tenant_id: uuid.UUID,
    *,
    category: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> IntegrityReport:
    """Recorre todas las filas del tenant y el volumen fisico bajo su
    prefijo (`iter_keys` aisla por tenant), clasificando cada hallazgo en
    una de las 5 categorias exactas del plan. `retained_deleted_blob` no es
    una anomalia -- es la eliminacion logica funcionando como se diseno
    (Seccion 3 punto 6): el borrado fisico real queda para un futuro proceso
    de purga, fuera de alcance de este bloque."""
    non_deleted = file_repository.iter_all_non_deleted_for_tenant(db, tenant_id)
    deleted = file_repository.iter_deleted_for_tenant(db, tenant_id)

    known_keys: dict[str, DeclarationDocumentFile] = {}
    entries: list[IntegrityEntry] = []

    for file in non_deleted:
        known_keys[file.storage_key] = file
        if not storage.exists(file.storage_key):
            entries.append(_row_entry("missing_blob", file))
            continue
        actual_hash = _recompute_hash(storage, file.storage_key)
        if actual_hash != file.sha256_hash:
            entries.append(_row_entry("hash_mismatch", file, actual_hash=actual_hash))

    for file in deleted:
        known_keys[file.storage_key] = file
        if storage.exists(file.storage_key):
            entries.append(_row_entry("retained_deleted_blob", file))

    for key in storage.iter_keys(str(tenant_id)):
        if key.endswith(".part"):
            entries.append(_blob_only_entry(storage, key, category="temporary_part"))
            continue
        if key not in known_keys:
            entries.append(_blob_only_entry(storage, key, category="orphan_blob"))

    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.category] = counts.get(entry.category, 0) + 1

    filtered = entries if category is None else [e for e in entries if e.category == category]
    filtered.sort(key=lambda e: (e.category, e.file_id or e.blob_fingerprint or ""))
    total = len(filtered)
    page = filtered[offset : offset + limit]

    return IntegrityReport(items=page, total=total, counts=counts)
