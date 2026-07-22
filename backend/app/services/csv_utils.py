"""Utilidades compartidas para CSV: sanitizacion contra CSV formula injection
en exportaciones (ver correccion de seguridad del Bloque 2)."""

_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_csv_cell(value: str) -> str:
    """Antepone un apostrofe a celdas que Excel/Sheets interpretarian como
    formula, neutralizando su ejecucion al abrir el archivo."""
    if value and value[0] in _DANGEROUS_PREFIXES:
        return "'" + value
    return value
