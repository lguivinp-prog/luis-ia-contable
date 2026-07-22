"""Importacion de cronogramas desde CSV (ver ADR-0012, ADR-0014, ADR-0011).

Flujo: recibir -> validar (estructura/tipos/fechas/periodos/duplicados
internos) -> comparar contra la version APPROVED previa del mismo scope
(clasificado como diff, nunca como rechazo automatico) -> vista previa ->
confirmar (crea la version + entries) o rechazar. El CSV crudo se conserva
siempre como evidencia (ADR-0012), incluso si se rechaza.
"""

import csv
import hashlib
import io
import re
from datetime import datetime

from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.models.tax_schedule import TaxScheduleEntry, TaxScheduleImportRun
from app.repositories import tax_category_repository, tax_schedule_repository
from app.services import tax_schedule_service
from app.services.tax_schedule_digits import InvalidDigitsError, normalize_applicable_last_digits

settings = get_settings()

REQUIRED_HEADERS = [
    "tax_period",
    "applicable_last_digits",
    "required_category_code",
    "priority",
    "due_date_original",
    "due_date_effective",
    "due_date_type",
    "notes",
]

_ALLOWED_CONTENT_TYPES = {"text/csv", "application/vnd.ms-excel", "application/csv", "text/plain", "", None}
_TAX_PERIOD_RE = re.compile(r"^\d{4}(-\d{2})?$")


class CsvValidationError(Exception):
    pass


class CsvTooLargeError(Exception):
    pass


class CsvEncodingError(Exception):
    pass


class CsvContentTypeError(Exception):
    pass


class ImportRunNotFoundError(Exception):
    pass


class ImportRunNotConfirmableError(Exception):
    pass


def _validate_size(file_bytes: bytes) -> None:
    if len(file_bytes) > settings.tax_schedule_csv_max_bytes:
        raise CsvTooLargeError(
            f"El archivo excede el tamaño máximo permitido "
            f"({settings.tax_schedule_csv_max_bytes} bytes)"
        )


def _validate_content_type(filename: str, content_type: str | None) -> None:
    if not filename.lower().endswith(".csv"):
        raise CsvContentTypeError("El archivo debe tener extensión .csv")
    if content_type not in _ALLOWED_CONTENT_TYPES:
        raise CsvContentTypeError(f"Tipo de contenido no permitido: {content_type!r}")


def _decode_utf8_strict(file_bytes: bytes) -> str:
    if b"\x00" in file_bytes:
        raise CsvEncodingError("El archivo parece binario (contiene bytes nulos), no un CSV de texto")
    try:
        return file_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CsvEncodingError(f"El archivo no es UTF-8 válido: {exc}") from exc


def _parse_digits_cell(raw: str) -> list[int] | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        values = [int(x.strip()) for x in raw.split(";") if x.strip() != ""]
    except ValueError as exc:
        raise InvalidDigitsError(
            f"Dígitos inválidos: {raw!r} (se esperan enteros separados por ';')"
        ) from exc
    return normalize_applicable_last_digits(values)


def _parse_date_cell(raw: str, field: str) -> str:
    raw = raw.strip()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise CsvValidationError(
            f"Fecha inválida en '{field}': {raw!r} (formato esperado YYYY-MM-DD)"
        ) from exc


def _validate_tax_period_cell(raw: str) -> str:
    raw = raw.strip()
    if not _TAX_PERIOD_RE.fullmatch(raw):
        raise CsvValidationError(f"Periodo tributario inválido: {raw!r} (formato esperado YYYY o YYYY-MM)")
    return raw


def parse_csv_rows(content: str, valid_category_codes: set[str]) -> tuple[list[dict], list[dict]]:
    reader = csv.DictReader(io.StringIO(content))
    fieldnames = reader.fieldnames or []
    missing_headers = set(REQUIRED_HEADERS) - set(fieldnames)
    if missing_headers:
        return [], [{"row": 0, "message": f"Encabezados faltantes: {sorted(missing_headers)}"}]

    parsed: list[dict] = []
    errors: list[dict] = []
    seen_keys: dict[tuple, int] = {}

    for row_number, row in enumerate(reader, start=2):  # la fila 1 es el encabezado
        try:
            tax_period = _validate_tax_period_cell(row["tax_period"])
            digits = _parse_digits_cell(row["applicable_last_digits"])
            category = (row.get("required_category_code") or "").strip() or None
            if category is not None and category not in valid_category_codes:
                raise CsvValidationError(f"Código de categoría desconocido: {category!r}")
            priority_raw = (row.get("priority") or "").strip()
            priority = int(priority_raw) if priority_raw else None
            due_original = _parse_date_cell(row["due_date_original"], "due_date_original")
            due_effective = _parse_date_cell(row["due_date_effective"], "due_date_effective")
            due_type = (row.get("due_date_type") or "").strip() or "REGULAR"
            notes = (row.get("notes") or "").strip() or None

            key = (tax_period, tuple(digits) if digits else None, category)
            if key in seen_keys:
                errors.append(
                    {
                        "row": row_number,
                        "message": (
                            f"Fila duplicada/ambigua respecto a la fila {seen_keys[key]} "
                            "(misma combinación periodo/dígitos/categoría)"
                        ),
                    }
                )
                continue
            seen_keys[key] = row_number

            parsed.append(
                {
                    "row": row_number,
                    "tax_period": tax_period,
                    "applicable_last_digits": digits,
                    "required_category_code": category,
                    "priority": priority,
                    "due_date_original": due_original,
                    "due_date_effective": due_effective,
                    "due_date_type": due_type,
                    "notes": notes,
                }
            )
        except (CsvValidationError, InvalidDigitsError, ValueError) as exc:
            errors.append({"row": row_number, "message": str(exc)})

    return parsed, errors


def compute_diff_against_previous(
    db: Session,
    tenant_id,
    source_id,
    fiscal_year: int,
    schedule_type: str,
    scope_category_code: str | None,
    parsed_rows: list[dict],
) -> dict:
    """Clasifica diferencias vs. la version APPROVED previa del MISMO scope.
    Nunca rechaza el CSV: solo informa (added/removed/modified_dates)."""
    previous = tax_schedule_repository.get_approved_version(
        db, tenant_id, source_id, fiscal_year, schedule_type, scope_category_code
    )
    if previous is None:
        return {"has_previous": False, "added": [], "removed": [], "modified_dates": [], "warnings": []}

    previous_entries = tax_schedule_repository.list_entries(db, tenant_id, previous.id)
    def _previous_key(e):
        digits = tuple(e.applicable_last_digits) if e.applicable_last_digits else None
        return (e.tax_period, digits, e.required_category_code)

    def _new_key(r):
        digits = tuple(r["applicable_last_digits"]) if r["applicable_last_digits"] else None
        return (r["tax_period"], digits, r["required_category_code"])

    previous_map = {_previous_key(e): e for e in previous_entries}
    new_map = {_new_key(r): r for r in parsed_rows}

    added = [
        {
            "tax_period": k[0],
            "applicable_last_digits": list(k[1]) if k[1] else None,
            "required_category_code": k[2],
        }
        for k in (new_map.keys() - previous_map.keys())
    ]
    removed = [
        {
            "tax_period": k[0],
            "applicable_last_digits": list(k[1]) if k[1] else None,
            "required_category_code": k[2],
        }
        for k in (previous_map.keys() - new_map.keys())
    ]

    modified_dates = []
    for key in new_map.keys() & previous_map.keys():
        old_entry = previous_map[key]
        new_row = new_map[key]
        if old_entry.due_date_effective.isoformat() != new_row["due_date_effective"]:
            modified_dates.append(
                {
                    "tax_period": key[0],
                    "from_due_date_effective": old_entry.due_date_effective.isoformat(),
                    "to_due_date_effective": new_row["due_date_effective"],
                }
            )

    return {
        "has_previous": True,
        "previous_version_label": previous.version_label,
        "added": added,
        "removed": removed,
        "modified_dates": modified_dates,
        "warnings": [],
    }


def receive_and_validate_import(
    db: Session,
    tenant_id,
    user_id,
    source_id,
    *,
    filename: str,
    file_bytes: bytes,
    content_type: str | None,
    fiscal_year: int,
    schedule_type: str,
    scope_category_code: str | None,
) -> TaxScheduleImportRun:
    _validate_size(file_bytes)
    _validate_content_type(filename, content_type)
    text_content = _decode_utf8_strict(file_bytes)

    valid_category_codes = {c.code for c in tax_category_repository.list_catalog(db, active_only=False)}
    parsed_rows, structural_errors = parse_csv_rows(text_content, valid_category_codes)

    if len(parsed_rows) > settings.tax_schedule_csv_max_rows:
        structural_errors.append(
            {
                "row": 0,
                "message": f"Demasiadas filas ({len(parsed_rows)} > {settings.tax_schedule_csv_max_rows})",
            }
        )

    diff_summary = None
    if not structural_errors:
        diff_summary = compute_diff_against_previous(
            db, tenant_id, source_id, fiscal_year, schedule_type, scope_category_code, parsed_rows
        )

    checksum = hashlib.sha256(file_bytes).hexdigest()
    status = "REJECTED" if structural_errors else "VALIDATED"

    import_run = TaxScheduleImportRun(
        tenant_id=tenant_id,
        source_id=source_id,
        original_filename=filename,
        file_checksum=checksum,
        raw_content=text_content,
        fiscal_year=fiscal_year,
        schedule_type=schedule_type,
        scope_category_code=scope_category_code,
        parsed_rows=parsed_rows,
        row_count=len(parsed_rows),
        status=status,
        validation_errors=structural_errors or None,
        diff_summary=diff_summary,
        created_at=clock.utcnow(),
        created_by=user_id,
    )
    return tax_schedule_repository.create_import_run(db, import_run)


def get_import_run(db: Session, tenant_id, import_run_id) -> TaxScheduleImportRun:
    import_run = tax_schedule_repository.get_import_run(db, tenant_id, import_run_id)
    if import_run is None:
        raise ImportRunNotFoundError(f"Importación {import_run_id} no encontrada")
    return import_run


def reject_import(db: Session, tenant_id, import_run_id) -> TaxScheduleImportRun:
    import_run = get_import_run(db, tenant_id, import_run_id)
    if import_run.status == "CONFIRMED":
        raise ImportRunNotConfirmableError("La importación ya fue confirmada, no puede rechazarse")
    import_run.status = "REJECTED"
    db.flush()
    return import_run


def confirm_import(
    db: Session,
    tenant_id,
    user_id,
    import_run_id,
    *,
    version_label: str,
    normative_reference: str | None,
    source_document_url: str | None,
) -> tuple[TaxScheduleImportRun, "object"]:
    import_run = get_import_run(db, tenant_id, import_run_id)
    if import_run.status != "VALIDATED":
        raise ImportRunNotConfirmableError(
            f"Solo se puede confirmar una importación en estado VALIDATED (actual: {import_run.status})"
        )

    from app.models.tax_schedule import TaxScheduleVersion

    version = TaxScheduleVersion(
        tenant_id=tenant_id,
        source_id=import_run.source_id,
        version_label=version_label,
        fiscal_year=import_run.fiscal_year,
        schedule_type=import_run.schedule_type,
        scope_category_code=import_run.scope_category_code,
        status="IMPORTED",
        normative_reference=normative_reference,
        source_document_url=source_document_url,
        checksum=import_run.file_checksum,
        created_by=user_id,
    )
    tax_schedule_repository.create_version(db, version)

    tax_schedule_service.ensure_mutable(version)

    entries = [
        TaxScheduleEntry(
            tenant_id=tenant_id,
            version_id=version.id,
            tax_period=row["tax_period"],
            applicable_last_digits=row["applicable_last_digits"],
            required_category_code=row["required_category_code"],
            priority=row["priority"],
            due_date_original=row["due_date_original"],
            due_date_effective=row["due_date_effective"],
            due_date_type=row["due_date_type"],
            notes=row["notes"],
        )
        for row in (import_run.parsed_rows or [])
    ]
    tax_schedule_repository.create_entries(db, entries)

    import_run.status = "CONFIRMED"
    import_run.version_id = version.id
    db.flush()

    return import_run, version
