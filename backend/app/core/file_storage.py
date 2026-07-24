"""Backend de almacenamiento de archivos desacoplado (Bloque 5, ADR-0053).
Ninguna capa fuera de este modulo recibe una ruta absoluta -- los llamadores
solo conocen claves logicas (`temporary_key`/`final_key`/`storage_key`).
`LocalFileStorageBackend` es la unica implementacion de este bloque; una
futura migracion a Drive/S3 implementaria el mismo protocolo `FileStorageBackend`
sin tocar el servicio ni el modelo de datos."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

_CHUNK_SIZE = 1024 * 1024  # 1 MiB, nunca se carga el archivo completo en memoria


class StorageLimitExceededError(Exception):
    pass


class EmptyBlobError(Exception):
    pass


@dataclass(frozen=True)
class StagedBlob:
    temporary_key: str
    sha256_hash: str
    size_bytes: int


@dataclass(frozen=True)
class BlobStat:
    size_bytes: int
    modified_at: float  # epoch seconds


class FileStorageBackend(Protocol):
    def write_staged(self, temporary_key: str, stream: BinaryIO, *, max_bytes: int) -> StagedBlob: ...

    def promote(self, temporary_key: str, final_key: str) -> None: ...

    def delete(self, key: str) -> None: ...

    def open(self, key: str) -> BinaryIO: ...

    def exists(self, key: str) -> bool: ...

    def iter_keys(self, prefix: str) -> Iterable[str]: ...

    def stat(self, key: str) -> BlobStat: ...


class LocalFileStorageBackend:
    """Sistema de archivos local bajo `root` (ver ADR-0053). `root` debe ser
    un volumen Docker nombrado montado fuera de `/app`, nunca un bind mount
    dentro de `./backend` -- de lo contrario los archivos reales aparecerian
    en el arbol de git."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        candidate = (self._root / key).resolve()
        if not candidate.is_relative_to(self._root):
            raise ValueError(f"Clave fuera de la raiz de almacenamiento: {key!r}")
        return candidate

    def write_staged(self, temporary_key: str, stream: BinaryIO, *, max_bytes: int) -> StagedBlob:
        """Escribe en streaming, acumulando tamano/hash por bloque -- el
        limite se aplica mientras se lee, nunca confiando solo en
        `Content-Length` (ver Seccion 8/10 del plan)."""
        path = self._resolve(temporary_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        hasher = hashlib.sha256()
        total = 0
        try:
            with path.open("wb") as fh:
                while True:
                    chunk = stream.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise StorageLimitExceededError(
                            f"El archivo excede el tamano maximo permitido ({max_bytes} bytes)"
                        )
                    hasher.update(chunk)
                    fh.write(chunk)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        if total == 0:
            path.unlink(missing_ok=True)
            raise EmptyBlobError("El archivo esta vacio")
        return StagedBlob(temporary_key=temporary_key, sha256_hash=hasher.hexdigest(), size_bytes=total)

    def promote(self, temporary_key: str, final_key: str) -> None:
        source = self._resolve(temporary_key)
        destination = self._resolve(final_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)

    def delete(self, key: str) -> None:
        self._resolve(key).unlink(missing_ok=True)

    def open(self, key: str) -> BinaryIO:
        return self._resolve(key).open("rb")

    def exists(self, key: str) -> bool:
        return self._resolve(key).exists()

    def iter_keys(self, prefix: str) -> Iterable[str]:
        base = self._resolve(prefix)
        if not base.exists():
            return
        for path in base.rglob("*"):
            if path.is_file():
                yield str(path.relative_to(self._root)).replace(os.sep, "/")

    def stat(self, key: str) -> BlobStat:
        st = self._resolve(key).stat()
        return BlobStat(size_bytes=st.st_size, modified_at=st.st_mtime)


def compensate_physical_write(storage: FileStorageBackend, *, key: str) -> None:
    """Compensacion best-effort ante un fallo posterior a la escritura fisica
    de un blob (ver Seccion 5/10 del plan de Bloque 5). `key` puede ser la
    clave temporal (fallo antes de `promote()`) o la clave definitiva (fallo
    despues de `promote()` y antes del `commit()`) -- el llamador decide cual
    esta vigente en el momento del fallo. Nunca lanza: si la propia limpieza
    falla, el blob queda para que el reporte de integridad lo detecte como
    huerfano (segunda barrera, nunca el mecanismo ordinario de compensacion).
    El error original de la base de datos siempre se re-lanza por separado,
    nunca lo oculta esta funcion."""
    try:
        storage.delete(key)
    except Exception:  # noqa: BLE001 -- best-effort; el reporte de integridad es la segunda barrera
        pass
