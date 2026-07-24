"""Almacenamiento real de archivos documentales (Bloque 5, ver ADR-0053 a
ADR-0056). Ningun metodo de este servicio ejecuta `commit()`/`rollback()`:
usa `flush()` unicamente; el router controla el limite transaccional (mismo
principio que el resto del sistema, ADR-0037). La carga NUNCA cambia
`declaration_document_items.status` automaticamente -- eso sigue siendo una
accion manual separada via `change_item_status`."""

import uuid
from typing import BinaryIO

from sqlalchemy.orm import Session

from app.core import clock
from app.core.file_storage import (
    EmptyBlobError,
    FileStorageBackend,
    StorageLimitExceededError,
    compensate_physical_write,
)
from app.models.declaration_document_file import DeclarationDocumentFile
from app.repositories import declaration_document_file_repository as file_repository
from app.services.document_file_content_validation import (
    UnsupportedFileContentError,
    classify_file_content,
)
from app.services.document_file_naming import sanitize_original_filename


class DeclarationDocumentFileNotFoundError(Exception):
    pass


class FileTooLargeError(Exception):
    pass


class EmptyFileError(Exception):
    pass


# Re-exportado para conveniencia del router (evita un segundo import desde
# document_file_content_validation).
UnsupportedFileTypeError = UnsupportedFileContentError


class FileNotActiveError(Exception):
    """Se intento reemplazar/eliminar una version que ya no esta ACTIVE
    (fue superada o eliminada por otra operacion, posiblemente una carrera
    concurrente perdida) -- el router lo traduce a 409."""


def _new_storage_key(
    tenant_id: uuid.UUID, item_id: uuid.UUID, logical_file_id: uuid.UUID, version: int, extension: str
) -> str:
    return f"{tenant_id}/{item_id}/{logical_file_id}/v{version}-{uuid.uuid4().hex}{extension}"


def _stage_and_classify(
    storage: FileStorageBackend,
    stream: BinaryIO,
    *,
    tenant_id: uuid.UUID,
    item_id: uuid.UUID,
    logical_file_id: uuid.UUID,
    version: int,
    max_bytes: int,
):
    """Escribe el archivo en streaming a una clave temporal (sin ningun
    bloqueo de base de datos todavia, ver Seccion 2/11 del plan) y clasifica
    su contenido real. Devuelve (staged_blob, classified_content). Limpia el
    temporal y relanza como excepcion de dominio ante cualquier fallo de
    validacion -- nunca deja un `.part` huerfano en el caso de rechazo."""
    temporary_key = f"{tenant_id}/{item_id}/{logical_file_id}/v{version}.part"
    try:
        staged = storage.write_staged(temporary_key, stream, max_bytes=max_bytes)
    except StorageLimitExceededError as exc:
        raise FileTooLargeError(str(exc)) from exc
    except EmptyBlobError as exc:
        # write_staged ya limpio el .part en este caso (archivo vacio).
        raise EmptyFileError(str(exc)) from exc

    try:
        with storage.open(staged.temporary_key) as fh:
            classified = classify_file_content(fh)
    except UnsupportedFileContentError:
        storage.delete(staged.temporary_key)
        raise

    return staged, classified


def upload_file(
    db: Session,
    storage: FileStorageBackend,
    tenant_id: uuid.UUID,
    client_id: uuid.UUID,
    declaration_id: uuid.UUID,
    item_id: uuid.UUID,
    *,
    stream: BinaryIO,
    original_filename: str,
    description: str | None,
    max_bytes: int,
    actor_user_id: uuid.UUID,
) -> DeclarationDocumentFile:
    """Carga inicial: abre una cadena logica nueva (`version=1`,
    `replaces_file_id=NULL`). Nunca cambia el estado del item documental."""
    logical_file_id = uuid.uuid4()
    staged, classified = _stage_and_classify(
        storage,
        stream,
        tenant_id=tenant_id,
        item_id=item_id,
        logical_file_id=logical_file_id,
        version=1,
        max_bytes=max_bytes,
    )

    final_key = _new_storage_key(tenant_id, item_id, logical_file_id, 1, classified.extension)
    storage.promote(staged.temporary_key, final_key)

    now = clock.utcnow()
    file = DeclarationDocumentFile(
        tenant_id=tenant_id,
        client_id=client_id,
        declaration_id=declaration_id,
        declaration_document_item_id=item_id,
        logical_file_id=logical_file_id,
        version=1,
        original_filename=sanitize_original_filename(original_filename),
        description=description,
        storage_key=final_key,
        mime_type=classified.mime_type,
        extension=classified.extension,
        size_bytes=staged.size_bytes,
        sha256_hash=staged.sha256_hash,
        status="ACTIVE",
        replaces_file_id=None,
        uploaded_by=actor_user_id,
        uploaded_at=now,
    )
    try:
        file_repository.create(db, file)
    except Exception:
        compensate_physical_write(storage, key=final_key)
        raise
    return file


def get_file(
    db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID, item_id: uuid.UUID, file_id: uuid.UUID
) -> DeclarationDocumentFile:
    file = file_repository.get(db, tenant_id, declaration_id, item_id, file_id)
    if file is None:
        raise DeclarationDocumentFileNotFoundError(f"Archivo {file_id} no encontrado")
    return file


def list_files(
    db: Session, tenant_id: uuid.UUID, declaration_id: uuid.UUID, item_id: uuid.UUID
) -> list[DeclarationDocumentFile]:
    return file_repository.list_for_item(db, tenant_id, declaration_id, item_id)


_DOWNLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MiB


def iter_download_chunks(storage: FileStorageBackend, file: DeclarationDocumentFile):
    """Generador de bloques para streaming de descarga -- nunca carga el
    archivo completo en memoria."""
    stream = storage.open(file.storage_key)
    try:
        while True:
            chunk = stream.read(_DOWNLOAD_CHUNK_SIZE)
            if not chunk:
                break
            yield chunk
    finally:
        stream.close()


def replace_file(
    db: Session,
    storage: FileStorageBackend,
    tenant_id: uuid.UUID,
    client_id: uuid.UUID,
    declaration_id: uuid.UUID,
    item_id: uuid.UUID,
    file_id: uuid.UUID,
    *,
    stream: BinaryIO,
    original_filename: str,
    max_bytes: int,
    reason: str,
    actor_user_id: uuid.UUID,
) -> DeclarationDocumentFile:
    """Reemplazo concurrente-seguro (ver Seccion 2/3/11 del plan de Bloque 5):

    1-4. El archivo nuevo se escribe y valida en streaming ANTES de tomar
         ningun bloqueo de base de datos (staging).
    5+.  Solo entonces se bloquea el `file_id` EXACTO solicitado (no
         "cualquier ACTIVE de la cadena"), se confirma bajo el bloqueo que
         sigue ACTIVE, y se promueve/inserta con dos `flush()` explicitos
         (el indice unico parcial WHERE status='ACTIVE' no es diferible)."""
    # Staging: sin bloqueo de BD todavia.
    logical_file_id_probe = file_repository.get(db, tenant_id, declaration_id, item_id, file_id)
    if logical_file_id_probe is None:
        raise DeclarationDocumentFileNotFoundError(f"Archivo {file_id} no encontrado")
    logical_file_id = logical_file_id_probe.logical_file_id
    next_version = logical_file_id_probe.version + 1

    staged, classified = _stage_and_classify(
        storage,
        stream,
        tenant_id=tenant_id,
        item_id=item_id,
        logical_file_id=logical_file_id,
        version=next_version,
        max_bytes=max_bytes,
    )

    # Bloqueo del file_id EXACTO solicitado -- garantiza que dos peticiones
    # sobre la misma version original nunca se encadenen silenciosamente.
    target = file_repository.lock_exact_active_file(db, tenant_id, declaration_id, item_id, file_id)
    if target is None:
        storage.delete(staged.temporary_key)
        raise DeclarationDocumentFileNotFoundError(f"Archivo {file_id} no encontrado")
    if target.status != "ACTIVE":
        # Carrera perdida: otra transaccion ya reemplazo/elimino este file_id
        # exacto. Nunca se promueve el temporal, nunca se inserta la fila.
        storage.delete(staged.temporary_key)
        raise FileNotActiveError(
            f"El archivo {file_id} ya no esta activo (estado actual: {target.status})"
        )

    final_key = _new_storage_key(tenant_id, item_id, logical_file_id, next_version, classified.extension)
    storage.promote(staged.temporary_key, final_key)

    now = clock.utcnow()
    try:
        target.status = "SUPERSEDED"
        target.superseded_at = now
        db.flush()  # necesario: el indice unico parcial no es diferible (ver Seccion 3/11)

        new_file = DeclarationDocumentFile(
            tenant_id=tenant_id,
            client_id=client_id,
            declaration_id=declaration_id,
            declaration_document_item_id=item_id,
            logical_file_id=logical_file_id,
            version=next_version,
            original_filename=sanitize_original_filename(original_filename),
            description=target.description,
            storage_key=final_key,
            mime_type=classified.mime_type,
            extension=classified.extension,
            size_bytes=staged.size_bytes,
            sha256_hash=staged.sha256_hash,
            status="ACTIVE",
            replaces_file_id=target.id,
            uploaded_by=actor_user_id,
            uploaded_at=now,
        )
        file_repository.create(db, new_file)
    except Exception:
        # El rollback() exterior (router) revierte el UPDATE de `target` --
        # vuelve a ACTIVE por el propio rollback, sin logica manual.
        compensate_physical_write(storage, key=final_key)
        raise
    return new_file


def delete_file(
    db: Session,
    tenant_id: uuid.UUID,
    declaration_id: uuid.UUID,
    item_id: uuid.UUID,
    file_id: uuid.UUID,
    *,
    reason: str,
    actor_user_id: uuid.UUID,
) -> DeclarationDocumentFile:
    """Eliminacion logica -- nunca toca el archivo fisico dentro de la
    peticion HTTP (Sección 10 del plan). Transicion directa ACTIVE->DELETED,
    nunca pasa por SUPERSEDED."""
    target = file_repository.lock_exact_active_file(db, tenant_id, declaration_id, item_id, file_id)
    if target is None:
        raise DeclarationDocumentFileNotFoundError(f"Archivo {file_id} no encontrado")
    if target.status != "ACTIVE":
        raise FileNotActiveError(
            f"El archivo {file_id} ya no esta activo (estado actual: {target.status})"
        )

    now = clock.utcnow()
    target.status = "DELETED"
    target.deleted_at = now
    target.deleted_by = actor_user_id
    target.deletion_reason = reason
    db.flush()
    return target
