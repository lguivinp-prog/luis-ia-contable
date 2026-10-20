"""M4 -- almacenamiento ACOTADO de archivos del control SIRE (CSV, XLSX, PDF, imagenes). Reutiliza `FileStorageBackend` (staging -> clasificacion por CONTENIDO real
-> promocion) y la clasificacion existente de PDF/JPEG/PNG/XLSX (`document_file_content_validation`), sin debilitarla: el CSV tiene una via propia y estrictamente
acotada (texto sin bytes nulos, delimitador consistente) y los XLSX con macros, vinculos externos u objetos incrustados se rechazan. Nunca se ejecuta ni se
interpreta un archivo almacenado salvo CSV/XLSX con mapeo explicito del contador; ningun metodo hace `commit()`."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import BinaryIO

from app.core.file_storage import EmptyBlobError, FileStorageBackend, StagedBlob, StorageLimitExceededError
from app.services.document_file_content_validation import UnsupportedFileContentError, classify_file_content
from app.services.sire_import_parser import ImportRejected, assert_safe_ooxml, detect_csv

TABULAR_KINDS = ("csv", "xlsx")


class SireFileRejected(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


@dataclass(frozen=True)
class SireClassified:
    mime_type: str
    extension: str
    kind: str  # csv | xlsx | pdf | image


def classify_sire_file(stream: BinaryIO) -> SireClassified:
    """Clasifica por CONTENIDO (nunca por el nombre ni el Content-Type declarados)."""
    stream.seek(0)
    try:
        general = classify_file_content(stream)
    except UnsupportedFileContentError:
        general = None
    stream.seek(0)
    if general is not None:
        if general.extension == ".docx":
            raise SireFileRejected("FILE_TYPE_NOT_ALLOWED", "Formatos permitidos: CSV, XLSX, PDF, JPEG o PNG.")
        if general.extension == ".xlsx":
            try:
                assert_safe_ooxml(stream)
            except ImportRejected as exc:
                raise SireFileRejected(exc.code, exc.message) from exc
            return SireClassified(general.mime_type, ".xlsx", "xlsx")
        return SireClassified(general.mime_type, general.extension, "pdf" if general.extension == ".pdf" else "image")
    sample = stream.read(64 * 1024)
    stream.seek(0)
    try:
        detect_csv(sample)
    except ImportRejected as exc:
        raise SireFileRejected("FILE_TYPE_NOT_ALLOWED", "Formatos permitidos: CSV, XLSX, PDF, JPEG o PNG.") from exc
    return SireClassified("text/csv", ".csv", "csv")


@dataclass(frozen=True)
class StoredBlob:
    temporary_key: str
    final_key: str
    size_bytes: int
    sha256_hash: str
    classified: SireClassified


def stage_upload(storage: FileStorageBackend, *, tenant_id: uuid.UUID, control_id: uuid.UUID, stream: BinaryIO, max_bytes: int) -> StoredBlob:
    """Escribe en streaming a una clave temporal y clasifica; devuelve la clave final CALCULADA (aun sin promover). El temporal se elimina ante cualquier rechazo."""
    file_id = uuid.uuid4()
    base = f"sire/{tenant_id}/{control_id}/{file_id}"
    temporary_key = f"{base}.part"
    try:
        staged: StagedBlob = storage.write_staged(temporary_key, stream, max_bytes=max_bytes)
    except StorageLimitExceededError as exc:
        raise SireFileRejected("FILE_TOO_LARGE", "El archivo supera el tamaño máximo permitido.") from exc
    except EmptyBlobError as exc:
        raise SireFileRejected("FILE_EMPTY", "El archivo está vacío.") from exc
    try:
        with storage.open(staged.temporary_key) as fh:
            classified = classify_sire_file(fh)
    except (SireFileRejected, Exception):
        storage.delete(staged.temporary_key)
        raise
    return StoredBlob(staged.temporary_key, f"{base}{classified.extension}", staged.size_bytes, staged.sha256_hash, classified)


def sha256_of(storage: FileStorageBackend, key: str) -> str:
    digest = hashlib.sha256()
    with storage.open(key) as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
