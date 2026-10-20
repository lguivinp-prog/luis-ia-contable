"""M4 -- lectura CONTROLADA de CSV/XLSX para el control SIRE. Este modulo NO conoce ningun formato oficial de SUNAT ni de DARWIN: el contador asigna cada campo
a una columna del archivo (mapeo explicito, validado), de modo que fuentes con encabezados distintos pueden compararse sin presumir nombres.

Garantias: sin ejecucion de contenido (CSV como texto; XLSX con `openpyxl` en modo solo lectura, SIN macros, SIN formulas, SIN vinculos externos), limites de bytes /
filas / columnas, deteccion acotada de codificacion y delimitador, encabezados ambiguos rechazados, filas de totales no comparables rechazadas (o excluidas SOLO
por decision explicita), importes con `Decimal` (jamas float), mas de 2 decimales rechazado, importes vacios rechazados (nunca cero implicito)."""

from __future__ import annotations

import csv
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import BinaryIO, Iterator

from app.services.sire_control_logic import CENT, MAX_ABS_AMOUNT

MAX_PREVIEW_ROWS = 20
MAX_COLUMNS = 60
MAX_ERRORS_REPORTED = 50
SNIFF_BYTES = 64 * 1024
DELIMITERS = (",", ";", "\t", "|")
ENCODINGS = ("utf-8-sig", "cp1252")  # cp1252 cubre latin-1 sin huecos peligrosos; cualquier otro => se pide re-exportar
FIELDS = ("record_count", "base_amount", "igv_amount", "total_amount", "period", "record_type")
AMOUNT_FIELDS = ("base_amount", "igv_amount", "total_amount")
DECIMAL_STYLES = ("AUTO", "DOT", "COMMA")
MODES = ("DETAIL", "SUMMARY")

_FORBIDDEN_OOXML_MARKERS = (
    "xl/vbaproject.bin", "xl/externallinks/", "xl/activex/", "xl/embeddings/", "xl/ctrlprops/", "xl/macrosheets/", "xl/dialogsheets/",
    "customui/", "xl/connections.xml",
)
_TOTAL_ROW = re.compile(r"^\s*(sub\s*)?total(es)?\b|^\s*suma(s)?\b|^\s*gran\s+total\b|^\s*total\s+general\b", re.IGNORECASE)


class ImportRejected(Exception):
    """Archivo o mapeo rechazado: `code` estable (lista cerrada) y detalles saneados (sin contenido de celdas sensibles)."""

    def __init__(self, code: str, message: str = "", errors: list[dict] | None = None) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.errors = errors or []


@dataclass
class TableView:
    kind: str  # csv | xlsx
    headers: list[str]
    normalized_headers: list[str]
    rows: list[list[object]]  # filas de datos (sin la cabecera)
    row_numbers: list[int]  # numero de fila original (1 = cabecera)
    encoding: str | None = None
    delimiter: str | None = None
    sheet: str | None = None
    sheets: list[str] = field(default_factory=list)
    truncated: bool = False


def normalize_header(text: object) -> str:
    value = unicodedata.normalize("NFKD", str(text if text is not None else "")).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", value).strip().casefold()


def _check_headers(headers: list[object]) -> tuple[list[str], list[str]]:
    shown = [str(h).strip() if h is not None else "" for h in headers]
    while shown and shown[-1] == "":
        shown.pop()
    if not shown:
        raise ImportRejected("IMPORT_NO_HEADERS", "La primera fila no contiene encabezados.")
    if len(shown) > MAX_COLUMNS:
        raise ImportRejected("IMPORT_TOO_MANY_COLUMNS", f"El archivo tiene más de {MAX_COLUMNS} columnas.")
    normalized = [normalize_header(h) for h in shown]
    if any(n == "" for n in normalized):
        raise ImportRejected("IMPORT_BLANK_HEADER", "Hay columnas con encabezado vacío entre columnas con datos: complétalo o elimina la columna.")
    seen: dict[str, int] = {}
    for n in normalized:
        seen[n] = seen.get(n, 0) + 1
    duplicated = sorted({shown[i] for i, n in enumerate(normalized) if seen[n] > 1})
    if duplicated:
        raise ImportRejected("IMPORT_AMBIGUOUS_HEADERS", "Encabezados repetidos o ambiguos: " + ", ".join(duplicated[:8]) + ".")
    return shown, normalized


# --- CSV ----------------------------------------------------------------------------------------------------------------


def detect_csv(raw: bytes) -> tuple[str, str]:
    """Codificacion y delimitador de un CSV. Binario (NUL) o ilegible => rechazo."""
    sample = raw[:SNIFF_BYTES]
    if b"\x00" in sample:
        raise ImportRejected("IMPORT_BINARY_CONTENT", "El archivo contiene bytes nulos: no es un CSV de texto.")
    text, used = None, None
    for encoding in ENCODINGS:
        try:
            text = raw[:SNIFF_BYTES].decode(encoding, errors="strict") if encoding != "utf-8-sig" else _decode_prefix_utf8(raw[:SNIFF_BYTES])
            used = encoding
            break
        except UnicodeDecodeError:
            continue
    if text is None or used is None:
        raise ImportRejected("IMPORT_ENCODING_UNSUPPORTED", "No se pudo leer la codificación del CSV (usa UTF-8 o Windows-1252).")
    first_lines = [ln for ln in text.splitlines() if ln.strip()][:10]
    if not first_lines:
        raise ImportRejected("IMPORT_EMPTY", "El archivo está vacío.")
    counts = {d: [ln.count(d) for ln in first_lines] for d in DELIMITERS}
    best = max(DELIMITERS, key=lambda d: (min(counts[d]) > 0, min(counts[d]), sum(counts[d])))
    if min(counts[best]) == 0:
        raise ImportRejected("IMPORT_DELIMITER_UNDETECTED", "No se detectó un delimitador consistente (coma, punto y coma, tabulador o barra vertical).")
    return used, best


def _decode_prefix_utf8(prefix: bytes) -> str:
    """Decodifica UTF-8 tolerando un caracter multibyte cortado al final del bloque de muestreo."""
    try:
        return prefix.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        if exc.start >= len(prefix) - 3:
            return prefix[: exc.start].decode("utf-8-sig")
        raise


def read_csv(stream: BinaryIO, *, max_rows: int, encoding: str | None = None, delimiter: str | None = None, preview: bool = False) -> TableView:
    stream.seek(0)
    raw_prefix = stream.read(SNIFF_BYTES)
    stream.seek(0)
    det_encoding, det_delimiter = detect_csv(raw_prefix)
    encoding = encoding if encoding in ENCODINGS else det_encoding
    delimiter = delimiter if delimiter in DELIMITERS else det_delimiter
    wrapper = io.TextIOWrapper(stream, encoding=encoding, errors="strict", newline="")
    reader = csv.reader(wrapper, delimiter=delimiter, quotechar='"', strict=False)
    rows: list[list[object]] = []
    numbers: list[int] = []
    headers: list[str] = []
    normalized: list[str] = []
    truncated = False
    limit = MAX_PREVIEW_ROWS if preview else max_rows
    try:
        for index, row in enumerate(reader, start=1):
            if index == 1:
                headers, normalized = _check_headers(list(row))
                continue
            if len(rows) >= limit:
                truncated = True
                if preview:
                    break
                raise ImportRejected("IMPORT_TOO_MANY_ROWS", f"El archivo supera el máximo de {max_rows} filas de datos.")
            rows.append(list(row[: len(headers)]) + [""] * max(0, len(headers) - len(row)))
            numbers.append(index)
    except UnicodeDecodeError as exc:
        raise ImportRejected("IMPORT_ENCODING_UNSUPPORTED", "El CSV contiene caracteres que no corresponden a su codificación.") from exc
    except csv.Error as exc:
        raise ImportRejected("IMPORT_CSV_MALFORMED", "El CSV está mal formado.") from exc
    finally:
        wrapper.detach()  # no cierra el flujo subyacente del almacenamiento
    if not headers:
        raise ImportRejected("IMPORT_EMPTY", "El archivo está vacío.")
    return TableView("csv", headers, normalized, rows, numbers, encoding=encoding, delimiter=delimiter, truncated=truncated)


# --- XLSX ---------------------------------------------------------------------------------------------------------------


def assert_safe_ooxml(stream: BinaryIO) -> None:
    """Rechaza libros con macros, vinculos externos, controles ActiveX u objetos incrustados ANTES de abrirlos con openpyxl."""
    stream.seek(0)
    try:
        with zipfile.ZipFile(stream) as zf:
            names = [i.filename.lower() for i in zf.infolist()]
    except zipfile.BadZipFile as exc:
        raise ImportRejected("IMPORT_XLSX_CORRUPT", "El archivo XLSX está dañado.") from exc
    for name in names:
        if any(name.startswith(marker) or name == marker for marker in _FORBIDDEN_OOXML_MARKERS):
            raise ImportRejected("IMPORT_XLSX_ACTIVE_CONTENT", "El libro contiene macros, vínculos externos u objetos incrustados: expórtalo como un XLSX simple o CSV.")
    stream.seek(0)


def list_sheets(stream: BinaryIO) -> list[str]:
    from openpyxl import load_workbook

    assert_safe_ooxml(stream)
    try:
        wb = load_workbook(stream, read_only=True, data_only=False, keep_links=False)
        try:
            return [ws.title for ws in wb.worksheets if ws.sheet_state == "visible"]
        finally:
            wb.close()
    except ImportRejected:
        raise
    except Exception as exc:  # noqa: BLE001 -- openpyxl lanza tipos variados ante libros invalidos
        raise ImportRejected("IMPORT_XLSX_CORRUPT", "No se pudo abrir el libro XLSX.") from exc


def read_xlsx(stream: BinaryIO, *, max_rows: int, sheet: str | None = None, preview: bool = False) -> TableView:
    from openpyxl import load_workbook

    assert_safe_ooxml(stream)
    try:
        wb = load_workbook(stream, read_only=True, data_only=False, keep_links=False)
    except Exception as exc:  # noqa: BLE001
        raise ImportRejected("IMPORT_XLSX_CORRUPT", "No se pudo abrir el libro XLSX.") from exc
    try:
        visible = [ws.title for ws in wb.worksheets if ws.sheet_state == "visible"]
        if not visible:
            raise ImportRejected("IMPORT_XLSX_NO_SHEETS", "El libro no tiene hojas visibles.")
        if sheet is None:
            if len(visible) > 1 and not preview:
                raise ImportRejected("IMPORT_SHEET_REQUIRED", "El libro tiene varias hojas: elige la hoja a importar.")
            sheet = visible[0]
        if sheet not in visible:
            raise ImportRejected("IMPORT_SHEET_NOT_FOUND", "La hoja elegida no existe o está oculta.")
        ws = wb[sheet]
        rows: list[list[object]] = []
        numbers: list[int] = []
        headers: list[str] = []
        normalized: list[str] = []
        truncated = False
        limit = MAX_PREVIEW_ROWS if preview else max_rows
        for index, row in enumerate(ws.iter_rows(values_only=False), start=1):
            values = [_cell_value(cell, index) for cell in row[: MAX_COLUMNS + 1]]
            if index == 1:
                headers, normalized = _check_headers(values)
                continue
            if all(v is None or (isinstance(v, str) and v.strip() == "") for v in values):
                continue
            if len(rows) >= limit:
                truncated = True
                if preview:
                    break
                raise ImportRejected("IMPORT_TOO_MANY_ROWS", f"La hoja supera el máximo de {max_rows} filas de datos.")
            padded = list(values[: len(headers)]) + [None] * max(0, len(headers) - len(values))
            rows.append(padded)
            numbers.append(index)
        if not headers:
            raise ImportRejected("IMPORT_EMPTY", "La hoja está vacía.")
        return TableView("xlsx", headers, normalized, rows, numbers, sheet=sheet, sheets=visible, truncated=truncated)
    finally:
        wb.close()


def _cell_value(cell, row_index: int):
    """Valor de una celda. Una FORMULA (tipo 'f' o texto que empieza por '=' en una celda formula) se rechaza: se importan valores, no calculos."""
    if getattr(cell, "data_type", None) == "f":
        raise ImportRejected("IMPORT_XLSX_FORMULA", f"La fila {row_index} contiene fórmulas: pega los valores (no las fórmulas) y vuelve a exportar.")
    return cell.value


# --- numeros y fechas -----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedNumber:
    value: Decimal | None
    ambiguous: bool = False


_CURRENCY_PREFIX = re.compile(r"(?i)^(?:s/\.?|usd|pen|\$)\s*")
_CURRENCY_SUFFIX = re.compile(r"(?i)\s*(?:usd|pen)$")
_NUMERIC_SHAPE = re.compile(r"(\()?\s*([+-])?\s*(\d[\d.,' ]*)\s*(\))?\s*(-)?")


def normalize_numeric_text(text: str) -> tuple[bool, str]:
    """(negativo, digitos con separadores) de un importe en texto. ESTRICTO: solo se admiten signo, parentesis contables, prefijo/sufijo de moneda (S/, $, USD,
    PEN) y separadores; cualquier otro caracter (p. ej. una formula `=1+1`) es un error, jamas se descarta en silencio."""
    t = text.replace(" ", " ").strip()
    t = _CURRENCY_SUFFIX.sub("", _CURRENCY_PREFIX.sub("", t))
    m = _NUMERIC_SHAPE.fullmatch(t)
    if not m or bool(m.group(1)) != bool(m.group(4)):
        raise ValueError("numero invalido")
    negative = bool(m.group(1)) or m.group(2) == "-" or bool(m.group(5))
    return negative, m.group(3).replace(" ", "").replace("'", "")


def parse_decimal_text(text: str, style: str) -> ParsedNumber:
    """Interpreta un importe en texto SIN depender del formato visual. `style`: DOT (1,234.56) | COMMA (1.234,56) | AUTO (ambiguo => ambiguous=True)."""
    raw = text.strip()
    if raw == "":
        return ParsedNumber(None)
    negative, cleaned = normalize_numeric_text(raw)
    if style == "DOT":
        number = cleaned.replace(",", "")
        if number.count(".") > 1:
            raise ValueError("numero invalido")
    elif style == "COMMA":
        number = cleaned.replace(".", "").replace(",", ".")
        if number.count(".") > 1:
            raise ValueError("numero invalido")
    else:
        number, ambiguous = _auto_number(cleaned)
        if ambiguous:
            return ParsedNumber(None, ambiguous=True)
    if number in {"", "."}:
        raise ValueError("numero invalido")
    amount = Decimal(number)
    return ParsedNumber(-amount if negative else amount)


def _auto_number(cleaned: str) -> tuple[str, bool]:
    has_dot, has_comma = "." in cleaned, "," in cleaned
    if has_dot and has_comma:
        decimal_sep = "." if cleaned.rfind(".") > cleaned.rfind(",") else ","
        thousands = "," if decimal_sep == "." else "."
        return cleaned.replace(thousands, "").replace(decimal_sep, "."), False
    if not has_dot and not has_comma:
        return cleaned, False
    sep = "." if has_dot else ","
    if cleaned.count(sep) > 1:
        return cleaned.replace(sep, ""), False  # separador repetido = miles
    head, tail = cleaned.split(sep)
    if len(tail) == 3 and head not in {"", "0"}:
        return "", True  # '1,234' puede ser 1.234 o 1234: no se adivina
    return head + "." + tail, False


def cell_to_amount(value: object, style: str) -> ParsedNumber:
    if value is None:
        return ParsedNumber(None)
    if isinstance(value, bool):
        raise ValueError("numero invalido")
    if isinstance(value, int):
        return ParsedNumber(Decimal(value))
    if isinstance(value, Decimal):
        return ParsedNumber(value)
    if isinstance(value, float):
        rounded = round(value, 2)
        if abs(value - rounded) > 1e-9:
            raise ValueError("mas de 2 decimales")
        return ParsedNumber(Decimal(repr(rounded)))
    if isinstance(value, (date, datetime)):
        raise ValueError("fecha en columna de importe")
    return parse_decimal_text(str(value), style)


_PERIOD_PATTERNS = (
    re.compile(r"^(?P<y>\d{4})[-/]?(?P<m>\d{2})$"),  # 202609, 2026-09, 2026/09
    re.compile(r"^(?P<m>\d{1,2})[-/](?P<y>\d{4})$"),  # 09/2026
    re.compile(r"^\d{4}[-/](?P<m>\d{2})[-/]\d{2}$"),
    re.compile(r"^\d{1,2}[-/](?P<m>\d{1,2})[-/](?P<y>\d{4})$"),  # 15/09/2026
)


def period_of(value: object) -> tuple[int, int] | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, (date, datetime)):
        return value.year, value.month
    text = str(value).strip()
    iso = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if iso:
        return int(iso.group(1)), int(iso.group(2))
    for rx in (_PERIOD_PATTERNS[0], _PERIOD_PATTERNS[1], _PERIOD_PATTERNS[3]):
        m = rx.match(text)
        if m:
            year, month = int(m.group("y")), int(m.group("m"))
            if 1 <= month <= 12:
                return year, month
    return None


def record_type_of(value: object) -> str | None:
    text = normalize_header(value)
    if not text:
        return None
    if "rvie" in text or "venta" in text or "ingreso" in text:
        return "RVIE"
    if re.search(r"\brce\b|compra", text):
        return "RCE"
    return None


# --- agregacion -------------------------------------------------------------------------------------------------------------


@dataclass
class ImportResult:
    record_count: int | None
    base_amount: Decimal | None
    igv_amount: Decimal | None
    total_amount: Decimal | None
    rows_read: int
    rows_used: int
    ignored_total_rows: int
    decimal_style: str
    errors: list[dict]


def aggregate(
    table: TableView, mapping: dict[str, str | None], *, mode: str, decimal_style: str, component: str, period_year: int, period_month: int,
    ignore_total_rows: bool = False,
) -> ImportResult:
    """Valida el mapeo contra los encabezados y agrega el archivo. Cualquier error de FILA bloquea la importacion (se informan hasta 50)."""
    if mode not in MODES:
        raise ImportRejected("IMPORT_MODE_INVALID", "Modo de importación inválido.")
    if decimal_style not in DECIMAL_STYLES:
        raise ImportRejected("IMPORT_DECIMAL_STYLE_INVALID", "Estilo decimal inválido.")
    index_of = {n: i for i, n in enumerate(table.normalized_headers)}
    cols: dict[str, int] = {}
    for field_name, header in mapping.items():
        if field_name not in FIELDS:
            raise ImportRejected("IMPORT_MAPPING_FIELD_UNKNOWN", f"Campo no soportado: {field_name}.")
        if header in (None, ""):
            continue
        key = normalize_header(header)
        if key not in index_of:
            raise ImportRejected("IMPORT_MAPPING_COLUMN_NOT_FOUND", f"La columna asignada a «{field_name}» no existe en el archivo.")
        cols[field_name] = index_of[key]
    if len(set(cols.values())) != len(cols):
        raise ImportRejected("IMPORT_MAPPING_DUPLICATE_COLUMN", "Una misma columna no puede asignarse a dos campos distintos.")
    if not any(f in cols for f in ("record_count", *AMOUNT_FIELDS)) and mode == "SUMMARY":
        raise ImportRejected("IMPORT_MAPPING_EMPTY", "Asigna al menos un campo a comparar.")
    if not any(f in cols for f in AMOUNT_FIELDS) and mode == "DETAIL" and "record_count" not in cols and not table.rows:
        raise ImportRejected("IMPORT_EMPTY", "El archivo no tiene filas de datos.")
    if mode == "DETAIL" and "record_count" in cols:
        raise ImportRejected("IMPORT_MAPPING_COUNT_IN_DETAIL", "En modo detalle los registros se cuentan por filas: usa el modo resumen para una columna de cantidad.")

    errors: list[dict] = []
    ambiguous_decimal = False

    def err(row: int, code: str, message: str) -> None:
        if len(errors) < MAX_ERRORS_REPORTED:
            errors.append({"row": row, "code": code, "message": message})

    used_rows: list[tuple[int, list[object]]] = []
    ignored = 0
    for number, row in zip(table.row_numbers, table.rows):
        cells = [c for c in row]
        if all(c is None or (isinstance(c, str) and not c.strip()) for c in cells):
            continue
        label_cells = [c for i, c in enumerate(cells) if i not in cols.values() and isinstance(c, str) and c.strip()]
        first_text = next((c for c in cells if isinstance(c, str) and c.strip()), "")
        if _TOTAL_ROW.match(first_text or "") or any(_TOTAL_ROW.match(c) for c in label_cells[:2]):
            if ignore_total_rows:
                ignored += 1
                continue
            err(number, "IMPORT_TOTAL_ROW", "Fila de totales no comparable: excluye los totales del archivo o confirma ignorarlos explícitamente.")
            continue
        used_rows.append((number, cells))

    total_rows = len(used_rows)
    if mode == "SUMMARY" and total_rows != 1 and not errors:
        err(used_rows[1][0] if total_rows > 1 else 0, "IMPORT_SUMMARY_ROWS", "El modo resumen requiere exactamente una fila de datos.")

    sums: dict[str, Decimal] = {}
    count: int | None = None
    for number, cells in used_rows:
        if "period" in cols:
            period = period_of(cells[cols["period"]])
            if period is None:
                err(number, "IMPORT_PERIOD_INVALID", "El período de la fila no se pudo interpretar.")
            elif period != (period_year, period_month):
                err(number, "IMPORT_PERIOD_MISMATCH", f"La fila corresponde a otro período ({period[0]}-{period[1]:02d}).")
        if "record_type" in cols:
            kind = record_type_of(cells[cols["record_type"]])
            if kind is None:
                err(number, "IMPORT_TYPE_UNRECOGNIZED", "El tipo de registro de la fila no es RVIE ni RCE reconocible.")
            elif kind != component:
                err(number, "IMPORT_TYPE_MISMATCH", f"La fila es de tipo {kind} y el expediente es {component}.")
        for name in ("record_count", *AMOUNT_FIELDS):
            if name not in cols:
                continue
            raw = cells[cols[name]]
            try:
                if name == "record_count":
                    parsed = cell_to_amount(raw, decimal_style)
                    if parsed.ambiguous:
                        ambiguous_decimal = True
                        continue
                    if parsed.value is None or parsed.value != parsed.value.to_integral_value() or parsed.value < 0:
                        raise ValueError("cantidad invalida")
                    count = int(parsed.value)
                    continue
                parsed = cell_to_amount(raw, decimal_style)
            except (ValueError, InvalidOperation):
                err(number, "IMPORT_NUMBER_INVALID", f"Valor no numérico o con más de 2 decimales en «{name}».")
                continue
            if parsed.ambiguous:
                ambiguous_decimal = True
                continue
            if parsed.value is None:
                err(number, "IMPORT_AMOUNT_EMPTY", f"Importe vacío en «{name}»: un dato desconocido no se importa como cero.")
                continue
            if parsed.value.quantize(CENT) != parsed.value or abs(parsed.value) > MAX_ABS_AMOUNT:
                err(number, "IMPORT_NUMBER_INVALID", f"Importe fuera de rango o con más de 2 decimales en «{name}».")
                continue
            sums[name] = sums.get(name, Decimal("0")) + parsed.value

    if ambiguous_decimal:
        raise ImportRejected("IMPORT_DECIMAL_AMBIGUOUS", "Hay importes con separadores ambiguos (p. ej. 1,234): elige explícitamente el formato decimal (punto o coma).")
    if errors:
        raise ImportRejected("IMPORT_ROW_ERRORS", "El archivo contiene filas inválidas; no se importó nada.", errors)
    if mode == "DETAIL":
        count = total_rows
        if count == 0:
            raise ImportRejected("IMPORT_EMPTY", "El archivo no tiene filas de datos para comparar.")
    return ImportResult(
        record_count=count, base_amount=sums.get("base_amount"), igv_amount=sums.get("igv_amount"), total_amount=sums.get("total_amount"),
        rows_read=len(table.rows), rows_used=total_rows, ignored_total_rows=ignored, decimal_style=decimal_style, errors=[],
    )


def iter_sample(table: TableView) -> Iterator[list[str]]:
    for row in table.rows[:MAX_PREVIEW_ROWS]:
        yield ["" if v is None else (v.isoformat() if isinstance(v, (date, datetime)) else str(v)) for v in row]


def detect_decimal_style(table: TableView, amount_columns: list[int]) -> tuple[str | None, bool]:
    """(estilo detectado | None, ambiguo). Solo texto: las celdas numericas de XLSX ya son numeros."""
    styles: set[str] = set()
    ambiguous = False
    for row in table.rows:
        for i in amount_columns:
            value = row[i] if i < len(row) else None
            if not isinstance(value, str) or not value.strip():
                continue
            try:
                _neg, cleaned = normalize_numeric_text(value)
            except ValueError:
                continue
            number, amb = _auto_number(cleaned)
            if amb:
                ambiguous = True
            elif "," in cleaned and "." in cleaned:
                styles.add("DOT" if cleaned.rfind(".") > cleaned.rfind(",") else "COMMA")
            elif "," in cleaned and cleaned.count(",") == 1 and len(cleaned.split(",")[1]) != 3:
                styles.add("COMMA")
            elif "." in cleaned and cleaned.count(".") == 1 and len(cleaned.split(".")[1]) != 3:
                styles.add("DOT")
    if len(styles) == 1 and not ambiguous:
        return next(iter(styles)), False
    return None, ambiguous or len(styles) > 1
