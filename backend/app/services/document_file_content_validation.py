"""Validacion de contenido real para archivos documentales (Bloque 5,
ADR-0056). La clase declarada por el cliente (extension/Content-Type) nunca
decide el resultado -- todo se deriva del contenido real del archivo ya
escrito (`.part` completo). Opera siempre sobre un `BinaryIO` obtenido de
`FileStorageBackend.open()` -- nunca recibe una ruta absoluta (ninguna capa
fuera del backend de almacenamiento debe conocer rutas fisicas, ver
Seccion 1/2 del plan)."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import filetype

# mime -> extension para los formatos de firma simple (deteccion por
# contenido via `filetype`, sin dependencia de sistema tipo libmagic). DOCX/
# XLSX se resuelven aparte en `_classify_ooxml`, nunca por deteccion generica
# de ZIP.
_SIGNATURE_ALLOWLIST: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}

_MAX_ZIP_ENTRIES = 2000
_MAX_ZIP_TOTAL_UNCOMPRESSED = 200 * 1024 * 1024  # 200 MB
_MAX_ZIP_ENTRY_UNCOMPRESSED = 100 * 1024 * 1024  # 100 MB por entrada
_MAX_COMPRESSION_RATIO = 100
_MAX_CONTENT_TYPES_SIZE = 1024 * 1024  # 1 MB, generoso para un XML pequeno


class UnsupportedFileContentError(Exception):
    pass


@dataclass(frozen=True)
class ClassifiedContent:
    mime_type: str
    extension: str


def classify_file_content(stream: BinaryIO) -> ClassifiedContent:
    """Determina mime_type/extension reales a partir del contenido del
    archivo ya escrito -- nunca del nombre/extension declarados por el
    cliente. Los ZIP (DOCX/XLSX) requieren que el archivo este completo,
    porque el directorio central de un ZIP vive al final del archivo.
    `stream` debe ser buscable (seekable), como el devuelto por
    `FileStorageBackend.open()`."""
    stream.seek(0)
    header = stream.read(261)  # suficiente para la deteccion de firma de filetype
    stream.seek(0)

    if zipfile.is_zipfile(stream):
        stream.seek(0)
        return _classify_ooxml(stream)

    kind = filetype.guess(header)
    if kind is not None and kind.mime in _SIGNATURE_ALLOWLIST:
        return ClassifiedContent(mime_type=kind.mime, extension=_SIGNATURE_ALLOWLIST[kind.mime])

    raise UnsupportedFileContentError(
        "El contenido del archivo no corresponde a ningun formato permitido "
        "(PDF, JPEG, PNG, DOCX, XLSX)"
    )


def _validate_zip_bounds(zf: zipfile.ZipFile) -> None:
    """Defensa contra zip-bombs: limites sobre el directorio central antes
    de leer ningun contenido (ver Seccion 6/9 del plan)."""
    infos = zf.infolist()
    if len(infos) > _MAX_ZIP_ENTRIES:
        raise UnsupportedFileContentError("El archivo tiene demasiadas entradas internas")

    total_uncompressed = 0
    for info in infos:
        name = info.filename
        if name.startswith("/") or name.startswith("\\") or ".." in Path(name).parts:
            raise UnsupportedFileContentError(
                "El archivo contiene nombres de entrada no permitidos (posible traversal)"
            )
        if info.file_size > _MAX_ZIP_ENTRY_UNCOMPRESSED:
            raise UnsupportedFileContentError("Una entrada interna excede el tamano permitido")
        compress_size = max(info.compress_size, 1)
        if info.file_size / compress_size > _MAX_COMPRESSION_RATIO:
            raise UnsupportedFileContentError(
                "El archivo tiene una razon de compresion sospechosa (posible zip-bomb)"
            )
        total_uncompressed += info.file_size

    if total_uncompressed > _MAX_ZIP_TOTAL_UNCOMPRESSED:
        raise UnsupportedFileContentError("El contenido descomprimido total excede el limite permitido")


def _classify_ooxml(stream: BinaryIO) -> ClassifiedContent:
    """Verificacion estructural real de OOXML -- nunca se extrae ningun
    archivo al sistema de ficheros; solo se inspeccionan metadatos del
    directorio central y el contenido en memoria de `[Content_Types].xml`
    (ver Seccion 6/9 del plan)."""
    try:
        with zipfile.ZipFile(stream) as zf:
            _validate_zip_bounds(zf)
            names = zf.namelist()

            if "[Content_Types].xml" not in names:
                raise UnsupportedFileContentError(
                    "Estructura OOXML invalida: falta [Content_Types].xml"
                )

            content_types_info = zf.getinfo("[Content_Types].xml")
            if content_types_info.file_size > _MAX_CONTENT_TYPES_SIZE:
                raise UnsupportedFileContentError("[Content_Types].xml excede el tamano esperado")
            content_types_xml = zf.read("[Content_Types].xml").decode("utf-8", errors="replace")

            has_word = any(n.startswith("word/") for n in names)
            has_excel = any(n.startswith("xl/") for n in names)

            if has_word and has_excel:
                raise UnsupportedFileContentError(
                    "Estructura OOXML ambigua: contiene word/ y xl/ a la vez"
                )
            if not has_word and not has_excel:
                raise UnsupportedFileContentError(
                    "El archivo no tiene una estructura OOXML reconocible (falta word/ o xl/)"
                )

            if any(n.endswith("vbaProject.bin") for n in names) or "macroEnabled" in content_types_xml:
                raise UnsupportedFileContentError(
                    "Los formatos habilitados para macros no estan permitidos"
                )

            if has_word:
                return ClassifiedContent(
                    mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    extension=".docx",
                )
            return ClassifiedContent(
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                extension=".xlsx",
            )
    except zipfile.BadZipFile as exc:
        raise UnsupportedFileContentError("El archivo no es un contenedor ZIP valido") from exc
