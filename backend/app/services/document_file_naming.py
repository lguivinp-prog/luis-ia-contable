"""Saneamiento del nombre original de un archivo documental (Bloque 5,
ADR-0056). El valor devuelto es puramente de presentacion -- nunca se usa
para construir `storage_key` (ver Seccion 9 del plan)."""

import re
import urllib.parse

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_MAX_ORIGINAL_FILENAME_LENGTH = 255
_DEFAULT_FILENAME = "documento"


def sanitize_original_filename(raw: str) -> str:
    """Quita cualquier componente de ruta (toma solo el nombre base),
    rechaza caracteres de control y CR/LF, y trunca a 255 caracteres."""
    basename = raw.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _CONTROL_CHARS_RE.sub("", basename).strip()
    if not cleaned:
        cleaned = _DEFAULT_FILENAME
    return cleaned[:_MAX_ORIGINAL_FILENAME_LENGTH]


def build_content_disposition(filename: str) -> str:
    """Construye un `Content-Disposition: attachment` seguro (RFC 6266):
    `filename` ASCII de respaldo + `filename*` UTF-8 codificado por
    porcentaje -- nunca interpolacion directa sin codificar (defensa en
    profundidad ademas del saneamiento en la ingesta, ver ADR-0056)."""
    ascii_fallback = filename.encode("ascii", errors="replace").decode("ascii").replace('"', "_")
    quoted_utf8 = urllib.parse.quote(filename, safe="")
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{quoted_utf8}'
