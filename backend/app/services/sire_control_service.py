"""M4 -- servicio del expediente de control SIRE. Ningun metodo hace `commit()`/`rollback()` (el router controla la transaccion; mismo principio que el resto del
sistema). NUNCA modifica declaraciones, pagos, vencimientos ni obligaciones, ni abre SUNAT: solo organiza, compara y documenta lo que aporta el contador."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import BinaryIO

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock
from app.core.file_storage import FileStorageBackend, compensate_physical_write
from app.models.audit_log import AuditAction
from app.models.sire_control import SireControl, SireControlFile, SireControlReview, SireControlSource
from app.models.tax_period import TaxPeriod
from app.models.user import User
from app.repositories import client_repository
from app.services import audit_service
from app.services import sire_control_logic as logic
from app.services import sire_import_parser as parser
from app.services.document_file_naming import sanitize_original_filename
from app.services.sire_control_file_service import SireFileRejected, TABULAR_KINDS, sha256_of, stage_upload

REPORT_VERSION = 1
ORIGIN_LABEL = {"SUNAT": "SUNAT (información oficial)", "DARWIN": "DARWIN / CONTASIS (sistema contable)"}
COMPONENT_LABEL = {"RVIE": "Registro de Ventas e Ingresos Electrónicos", "RCE": "Registro de Compras Electrónico"}


class SireControlError(Exception):
    def __init__(self, code: str, message: str = "", errors: list[dict] | None = None) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.errors = errors or []


# --------------------------------------------------------------------------------------------------------- lectura de entidades


def _client_and_period(db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID, period_id: uuid.UUID) -> TaxPeriod:
    if client_repository.get_by_id(db, tenant_id, client_id) is None:
        raise SireControlError("CLIENT_NOT_FOUND")
    period = db.execute(select(TaxPeriod).where(TaxPeriod.id == period_id, TaxPeriod.tenant_id == tenant_id)).scalar_one_or_none()
    if period is None:
        raise SireControlError("PERIOD_NOT_FOUND")
    if period.month is None or period.period_type != "MONTHLY":
        raise SireControlError("PERIOD_NOT_MONTHLY", "El control SIRE solo aplica a periodos mensuales.")
    return period


def _find(db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID, period_id: uuid.UUID, component: str, *, lock: bool = False) -> SireControl | None:
    query = select(SireControl).where(
        SireControl.tenant_id == tenant_id, SireControl.client_id == client_id, SireControl.tax_period_id == period_id, SireControl.component == component
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return db.execute(query).scalar_one_or_none()


def get_control(db: Session, tenant_id: uuid.UUID, control_id: uuid.UUID, *, lock: bool = False) -> SireControl:
    query = select(SireControl).where(SireControl.id == control_id, SireControl.tenant_id == tenant_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    control = db.execute(query).scalar_one_or_none()
    if control is None:
        raise SireControlError("CONTROL_NOT_FOUND")  # inexistente O de otro tenant: mismo contrato (404)
    return control


def get_or_create_control(db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID, client_id: uuid.UUID, period_id: uuid.UUID, component: str) -> tuple[SireControl, bool]:
    """Creacion EXPLICITA (primera incorporacion de una fuente o archivo): consultar el cockpit o el expediente jamas crea nada."""
    _client_and_period(db, tenant_id, client_id, period_id)
    existing = _find(db, tenant_id, client_id, period_id, component)
    if existing is not None:
        return existing, False
    control = SireControl(tenant_id=tenant_id, client_id=client_id, tax_period_id=period_id, component=component, row_version=1, created_by=actor_id)
    try:
        with db.begin_nested():
            db.add(control)
            db.flush()
    except IntegrityError:  # carrera: otro request creo el mismo expediente
        existing = _find(db, tenant_id, client_id, period_id, component)
        if existing is None:
            raise
        return existing, False
    audit_service.record(
        db, tenant_id=tenant_id, user_id=actor_id, action=AuditAction.SIRE_CONTROL_CREATED, entity_type="sire_control", entity_id=control.id,
        metadata={"client_id": str(client_id), "tax_period_id": str(period_id), "component": component},
    )
    return control, True


def _active_sources(db: Session, control_id: uuid.UUID) -> dict[str, SireControlSource]:
    rows = db.execute(select(SireControlSource).where(SireControlSource.control_id == control_id, SireControlSource.status == "ACTIVE")).scalars().all()
    return {r.origin: r for r in rows}


def _snapshot(source: SireControlSource | None) -> logic.SourceSnapshot | None:
    if source is None:
        return None
    return logic.SourceSnapshot(
        id=str(source.id), origin=source.origin, version=source.version, entry_mode=source.entry_mode, currency=source.currency, source_label=source.source_label,
        source_date=source.source_date, content_hash=source.content_hash, record_count=source.record_count, base_amount=source.base_amount,
        igv_amount=source.igv_amount, total_amount=source.total_amount,
    )


def _current_review(db: Session, control_id: uuid.UUID) -> SireControlReview | None:
    return db.execute(select(SireControlReview).where(SireControlReview.control_id == control_id, SireControlReview.status == "CURRENT")).scalar_one_or_none()


# --------------------------------------------------------------------------------------------------------------- salidas (dict)


def _source_out(s: SireControlSource) -> dict:
    meta = s.import_meta or {}
    summary = None
    if s.entry_mode == "IMPORT" and meta:
        summary = {k: meta.get(k) for k in ("mode", "sheet", "encoding", "delimiter", "decimal_style", "rows_read", "rows_used", "ignored_total_rows", "mapping")}
    return {
        "id": s.id, "origin": s.origin, "entry_mode": s.entry_mode, "version": s.version, "status": s.status, "currency": s.currency,
        "record_count": s.record_count, "base_amount": logic.fmt(s.base_amount), "igv_amount": logic.fmt(s.igv_amount), "total_amount": logic.fmt(s.total_amount),
        "source_label": s.source_label, "source_date": s.source_date, "notes": s.notes, "file_id": s.file_id, "import_summary": summary,
        "replaces_source_id": s.replaces_source_id, "created_by": s.created_by, "created_at": s.created_at, "superseded_at": s.superseded_at,
    }


def _file_out(f: SireControlFile) -> dict:
    return {
        "id": f.id, "origin": f.origin, "purpose": f.purpose, "status": f.status, "source_id": f.source_id, "original_filename": f.original_filename,
        "description": f.description, "mime_type": f.mime_type, "size_bytes": f.size_bytes, "sha256_hash": f.sha256_hash, "uploaded_by": f.uploaded_by,
        "uploaded_at": f.uploaded_at,
    }


def _review_out(db: Session, r: SireControlReview) -> dict:
    name = db.execute(select(User.full_name).where(User.id == r.reviewed_by, User.tenant_id == r.tenant_id)).scalar_one_or_none()
    return {
        "id": r.id, "version": r.version, "status": r.status, "decision": r.decision, "notes": r.notes, "comparison_status": r.comparison_status,
        "comparison_hash": r.comparison_hash, "comparison_snapshot": r.comparison_snapshot, "sunat_source_id": r.sunat_source_id,
        "darwin_source_id": r.darwin_source_id, "reviewed_by": r.reviewed_by, "reviewed_by_name": name, "reviewed_at": r.reviewed_at, "superseded_at": r.superseded_at,
    }


def _comparison_out(comparison: logic.Comparison, hash_: str) -> dict:
    snap = logic.snapshot_dict(comparison)
    return {
        "status": comparison.status, "comparison_hash": hash_, "currency_mismatch": snap["currency_mismatch"], "single_source": snap["single_source"],
        "one_sided_concepts": snap["one_sided_concepts"], "tolerance": snap["tolerance"], "tolerance_applied": snap["tolerance_applied"],
        "messages": snap["messages"], "concepts": snap["concepts"],
    }


def control_view(db: Session, *, tenant_id: uuid.UUID, client_id: uuid.UUID, period_id: uuid.UUID, component: str, control: SireControl | None) -> dict:
    """Vista de un componente. Si el expediente no existe, devuelve la estructura VACIA sin crear nada (consultar no escribe)."""
    if control is None:
        comparison = logic.compare(None, None, None)
        return {
            "exists": False, "id": None, "client_id": client_id, "tax_period_id": period_id, "component": component, "summary_code": logic.CONTROL_NONE,
            "row_version": None, "tolerance_amount": None, "tolerance_reason": None, "tolerance_set_at": None, "sunat_source": None, "darwin_source": None,
            "comparison": _comparison_out(comparison, logic.comparison_hash(None, None, None)), "current_review": None, "review_stale": False, "files": [],
            "history_counts": {"sources": 0, "reviews": 0, "files": 0}, "created_at": None,
        }
    active = _active_sources(db, control.id)
    sunat, darwin = _snapshot(active.get("SUNAT")), _snapshot(active.get("DARWIN"))
    comparison = logic.compare(sunat, darwin, control.tolerance_amount)
    hash_ = logic.comparison_hash(sunat, darwin, control.tolerance_amount)
    review = _current_review(db, control.id)
    stale = review is not None and review.comparison_hash != hash_
    files = db.execute(
        select(SireControlFile).where(SireControlFile.control_id == control.id, SireControlFile.status.in_(("ACTIVE", "STAGED"))).order_by(SireControlFile.uploaded_at.desc())
    ).scalars().all()
    counts = {
        "sources": db.execute(select(func.count()).select_from(SireControlSource).where(SireControlSource.control_id == control.id)).scalar_one(),
        "reviews": db.execute(select(func.count()).select_from(SireControlReview).where(SireControlReview.control_id == control.id)).scalar_one(),
        "files": db.execute(select(func.count()).select_from(SireControlFile).where(SireControlFile.control_id == control.id, SireControlFile.status != "STAGED")).scalar_one(),
    }
    return {
        "exists": True, "id": control.id, "client_id": client_id, "tax_period_id": period_id, "component": component,
        "summary_code": logic.summary_code(exists=True, status=comparison.status, review_decision=review.decision if review else None, review_stale=stale),
        "row_version": control.row_version, "tolerance_amount": logic.fmt(control.tolerance_amount), "tolerance_reason": control.tolerance_reason,
        "tolerance_set_at": control.tolerance_set_at, "sunat_source": _source_out(active["SUNAT"]) if "SUNAT" in active else None,
        "darwin_source": _source_out(active["DARWIN"]) if "DARWIN" in active else None, "comparison": _comparison_out(comparison, hash_),
        "current_review": _review_out(db, review) if review else None, "review_stale": stale, "files": [_file_out(f) for f in files], "history_counts": counts,
        "created_at": control.created_at,
    }


def view_for_period(db: Session, *, tenant_id: uuid.UUID, client_id: uuid.UUID, period_id: uuid.UUID) -> dict:
    period = _client_and_period(db, tenant_id, client_id, period_id)
    controls = [
        control_view(db, tenant_id=tenant_id, client_id=client_id, period_id=period_id, component=component, control=_find(db, tenant_id, client_id, period_id, component))
        for component in ("RVIE", "RCE")
    ]
    return {"client_id": client_id, "tax_period_id": period_id, "tax_period_code": period.canonical_code, "controls": controls, "disclaimer": logic.DISCLAIMER}


def view_by_id(db: Session, tenant_id: uuid.UUID, control_id: uuid.UUID) -> dict:
    control = get_control(db, tenant_id, control_id)
    return control_view(db, tenant_id=tenant_id, client_id=control.client_id, period_id=control.tax_period_id, component=control.component, control=control)


# ------------------------------------------------------------------------------------------------------------------- fuentes


def _clean_text(value: str | None, *, max_len: int, required: bool, code: str) -> str | None:
    text = (value or "").strip()
    if required and not text:
        raise SireControlError(code)
    if len(text) > max_len:
        raise SireControlError(code)
    return text or None


def _check_source_date(value: date) -> None:
    if value > clock.utcnow().date():
        raise SireControlError("SOURCE_DATE_IN_FUTURE", "La fecha de la fuente no puede ser futura.")


def _amounts(base, igv, total) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    try:
        return logic.parse_amount(base), logic.parse_amount(igv), logic.parse_amount(total)
    except logic.AmountError as exc:
        raise SireControlError("AMOUNT_INVALID", str(exc)) from exc


def _insert_source(
    db: Session, *, control: SireControl, actor_id: uuid.UUID, origin: str, entry_mode: str, currency: str, record_count: int | None, base: Decimal | None,
    igv: Decimal | None, total: Decimal | None, source_label: str, source_date: date, notes: str | None, file: SireControlFile | None, import_meta: dict | None,
    replaces_source_id: uuid.UUID | None, idempotency_key: uuid.UUID | None,
) -> tuple[SireControlSource, bool]:
    if record_count is None and base is None and igv is None and total is None:
        raise SireControlError("SOURCE_HAS_NO_DATA", "Ingresa al menos un dato (cantidad o importe): lo desconocido no se asume como cero.")
    content_hash = logic.source_content_hash(
        component=control.component, origin=origin, currency=currency, record_count=record_count, base=base, igv=igv, total=total,
        file_sha256=file.sha256_hash if file is not None else None,
    )
    if idempotency_key is not None:
        prior = db.execute(select(SireControlSource).where(SireControlSource.tenant_id == control.tenant_id, SireControlSource.idempotency_key == idempotency_key)).scalar_one_or_none()
        if prior is not None:
            if prior.control_id == control.id and prior.origin == origin and prior.content_hash == content_hash:
                return prior, False
            raise SireControlError("IDEMPOTENCY_CONFLICT", "La clave de idempotencia ya se usó con datos distintos.")
    locked = get_control(db, control.tenant_id, control.id, lock=True)  # serializa las versiones del expediente
    active = _active_sources(db, locked.id).get(origin)
    if replaces_source_id is not None and (active is None or active.id != replaces_source_id):
        raise SireControlError("SOURCE_CHANGED", "La fuente vigente cambió mientras trabajabas: recarga el expediente antes de reemplazarla.")
    if active is not None and active.content_hash == content_hash and active.source_label == source_label and active.source_date == source_date:
        return active, False  # duplicado exacto (doble clic): no se crea otra version
    now = clock.utcnow()
    version = (db.execute(select(func.max(SireControlSource.version)).where(SireControlSource.control_id == locked.id, SireControlSource.origin == origin)).scalar_one() or 0) + 1
    if active is not None:
        active.status = "SUPERSEDED"
        active.superseded_at = now
        db.flush()  # el indice unico parcial no es diferible
        if active.file_id is not None:
            previous = db.get(SireControlFile, active.file_id)
            if previous is not None and previous.status == "ACTIVE" and previous.purpose == "SOURCE_FILE":
                previous.status = "SUPERSEDED"
    source = SireControlSource(
        tenant_id=locked.tenant_id, control_id=locked.id, client_id=locked.client_id, tax_period_id=locked.tax_period_id, component=locked.component, origin=origin,
        entry_mode=entry_mode, version=version, status="ACTIVE", record_count=record_count, base_amount=base, igv_amount=igv, total_amount=total, currency=currency,
        source_label=source_label, source_date=source_date, notes=notes, file_id=file.id if file is not None else None, import_meta=import_meta,
        content_hash=content_hash, idempotency_key=idempotency_key, replaces_source_id=active.id if active is not None else None, created_by=actor_id, created_at=now,
    )
    db.add(source)
    db.flush()
    if file is not None:
        file.source_id = source.id
        file.status = "ACTIVE"
    locked.row_version += 1
    db.flush()
    audit_service.record(
        db, tenant_id=locked.tenant_id, user_id=actor_id, action=AuditAction.SIRE_SOURCE_ADDED, entity_type="sire_control_source", entity_id=source.id,
        metadata={"control_id": str(locked.id), "component": locked.component, "origin": origin, "entry_mode": entry_mode, "version": version, "currency": currency,
                  "replaces": str(active.id) if active is not None else None, "file_sha256": file.sha256_hash[:16] if file is not None else None},
    )
    return source, True


def add_manual_source(
    db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID, client_id: uuid.UUID, tax_period_id: uuid.UUID, component: str, origin: str, currency: str,
    record_count: int | None, base_amount, igv_amount, total_amount, source_label: str, source_date: date, notes: str | None, replaces_source_id: uuid.UUID | None,
    idempotency_key: uuid.UUID | None,
) -> tuple[SireControlSource, bool, SireControl]:
    label = _clean_text(source_label, max_len=120, required=True, code="SOURCE_LABEL_REQUIRED")
    _check_source_date(source_date)
    base, igv, total = _amounts(base_amount, igv_amount, total_amount)
    control, _created = get_or_create_control(db, tenant_id=tenant_id, actor_id=actor_id, client_id=client_id, period_id=tax_period_id, component=component)
    source, created = _insert_source(
        db, control=control, actor_id=actor_id, origin=origin, entry_mode="MANUAL", currency=currency, record_count=record_count, base=base, igv=igv, total=total,
        source_label=label or "", source_date=source_date, notes=_clean_text(notes, max_len=2000, required=False, code="NOTES_TOO_LONG"), file=None, import_meta=None,
        replaces_source_id=replaces_source_id, idempotency_key=idempotency_key,
    )
    return source, created, control


# ------------------------------------------------------------------------------------------------------------------ archivos


def _find_file_by_hash(db: Session, control_id: uuid.UUID, origin: str, sha256: str) -> SireControlFile | None:
    return db.execute(
        select(SireControlFile).where(
            SireControlFile.control_id == control_id, SireControlFile.origin == origin, SireControlFile.sha256_hash == sha256, SireControlFile.status.in_(("STAGED", "ACTIVE"))
        )
    ).scalar_one_or_none()


def store_file(
    db: Session, storage: FileStorageBackend, *, control: SireControl, actor_id: uuid.UUID, origin: str, purpose: str, stream: BinaryIO, original_filename: str,
    description: str | None, max_bytes: int, source_id: uuid.UUID | None = None, status: str = "STAGED", created_keys: list[str] | None = None,
) -> tuple[SireControlFile, bool]:
    """Escribe, clasifica por CONTENIDO y registra con SHA-256. Un archivo identico ya vigente del mismo expediente/origen NO se duplica (doble clic): se devuelve el existente."""
    try:
        blob = stage_upload(storage, tenant_id=control.tenant_id, control_id=control.id, stream=stream, max_bytes=max_bytes)
    except SireFileRejected as exc:
        raise SireControlError(exc.code, exc.message) from exc
    existing = _find_file_by_hash(db, control.id, origin, blob.sha256_hash)
    if existing is not None:
        storage.delete(blob.temporary_key)
        return existing, False
    if source_id is not None:
        owner = db.execute(select(SireControlSource.id).where(SireControlSource.id == source_id, SireControlSource.control_id == control.id)).first()
        if owner is None:
            storage.delete(blob.temporary_key)
            raise SireControlError("SOURCE_NOT_FOUND")
    storage.promote(blob.temporary_key, blob.final_key)
    if created_keys is not None:
        created_keys.append(blob.final_key)  # el llamador compensa (borra el blob) si la transaccion NO se confirma
    file = SireControlFile(
        tenant_id=control.tenant_id, control_id=control.id, client_id=control.client_id, tax_period_id=control.tax_period_id, component=control.component, origin=origin,
        purpose=purpose, status=status, source_id=source_id, original_filename=sanitize_original_filename(original_filename),
        description=_clean_text(description, max_len=300, required=False, code="DESCRIPTION_TOO_LONG"), mime_type=blob.classified.mime_type,
        extension=blob.classified.extension, size_bytes=blob.size_bytes, sha256_hash=blob.sha256_hash, storage_key=blob.final_key, uploaded_by=actor_id,
        uploaded_at=clock.utcnow(),
    )
    try:
        with db.begin_nested():
            db.add(file)
            db.flush()
    except IntegrityError:
        compensate_physical_write(storage, key=blob.final_key)
        existing = _find_file_by_hash(db, control.id, origin, blob.sha256_hash)
        if existing is None:
            raise
        return existing, False
    except Exception:
        compensate_physical_write(storage, key=blob.final_key)
        raise
    try:
        audit_service.record(
            db, tenant_id=control.tenant_id, user_id=actor_id, action=AuditAction.SIRE_FILE_STORED, entity_type="sire_control_file", entity_id=file.id,
            metadata={"control_id": str(control.id), "component": control.component, "origin": origin, "purpose": purpose, "kind": blob.classified.kind,
                      "size_bytes": blob.size_bytes, "sha256_prefix": blob.sha256_hash[:16]},
        )
    except Exception:
        compensate_physical_write(storage, key=blob.final_key)
        raise
    return file, True


def attach_evidence(
    db: Session, storage: FileStorageBackend, *, tenant_id: uuid.UUID, actor_id: uuid.UUID, control_id: uuid.UUID, origin: str, stream: BinaryIO, original_filename: str,
    description: str | None, source_id: uuid.UUID | None, max_bytes: int, created_keys: list[str] | None = None,
) -> tuple[SireControlFile, bool]:
    control = get_control(db, tenant_id, control_id)
    return store_file(
        db, storage, control=control, actor_id=actor_id, origin=origin, purpose="EVIDENCE", stream=stream, original_filename=original_filename, description=description,
        max_bytes=max_bytes, source_id=source_id, status="ACTIVE", created_keys=created_keys,
    )


def _suggest(headers: list[str]) -> dict[str, str | None]:
    """SUGERENCIAS genericas por palabra clave (el contador confirma); no se presume ningun formato oficial."""
    hints = {
        "base_amount": ("base imponible", "base", "valor"),
        "igv_amount": ("igv", "impuesto"),
        "total_amount": ("importe total", "total"),
        "period": ("periodo", "período"),
        "record_type": ("tipo registro", "tipo de registro", "registro"),
        "record_count": ("cantidad", "nro registros", "numero de registros", "registros"),
    }
    normalized = {parser.normalize_header(h): h for h in headers}
    out: dict[str, str | None] = {}
    for field_name, keywords in hints.items():
        out[field_name] = None
        for kw in keywords:
            match = [orig for norm, orig in normalized.items() if norm == parser.normalize_header(kw)]
            if len(match) == 1:
                out[field_name] = match[0]
                break
    return out


def preview_import(
    db: Session, storage: FileStorageBackend, *, tenant_id: uuid.UUID, actor_id: uuid.UUID, client_id: uuid.UUID, tax_period_id: uuid.UUID, component: str, origin: str,
    stream: BinaryIO, original_filename: str, max_bytes: int, sheet: str | None, encoding: str | None, delimiter: str | None, created_keys: list[str] | None = None,
) -> dict:
    control, _ = get_or_create_control(db, tenant_id=tenant_id, actor_id=actor_id, client_id=client_id, period_id=tax_period_id, component=component)
    file, created = store_file(
        db, storage, control=control, actor_id=actor_id, origin=origin, purpose="SOURCE_FILE", stream=stream, original_filename=original_filename, description=None,
        max_bytes=max_bytes, status="STAGED", created_keys=created_keys,
    )
    if file.extension not in (".csv", ".xlsx"):
        raise SireControlError("FILE_NOT_TABULAR", "Solo CSV y XLSX se pueden importar; los demás formatos se conservan como evidencia sin interpretarse.")
    table = _read_table(storage, file, max_rows=0, sheet=sheet, encoding=encoding, delimiter=delimiter, preview=True)
    amount_cols = [i for i, h in enumerate(table.headers) if any(k in parser.normalize_header(h) for k in ("base", "igv", "total", "importe", "monto", "valor"))]
    style, needs_choice = parser.detect_decimal_style(table, amount_cols)
    return {
        "control_id": control.id, "file": _file_out(file), "duplicate_of_existing": not created, "kind": table.kind, "sheets": table.sheets, "sheet": table.sheet,
        "encoding": table.encoding, "delimiter": table.delimiter, "headers": table.headers, "sample_rows": list(parser.iter_sample(table)), "truncated": table.truncated,
        "suggested_mapping": _suggest(table.headers), "decimal_style_detected": style, "decimal_style_needs_choice": needs_choice,
    }


def _read_table(storage: FileStorageBackend, file: SireControlFile, *, max_rows: int, sheet, encoding, delimiter, preview: bool) -> parser.TableView:
    try:
        with storage.open(file.storage_key) as fh:
            if file.extension == ".csv":
                return parser.read_csv(fh, max_rows=max_rows, encoding=encoding, delimiter=delimiter, preview=preview)
            return parser.read_xlsx(fh, max_rows=max_rows, sheet=sheet, preview=preview)
    except parser.ImportRejected as exc:
        raise SireControlError(exc.code, exc.message, exc.errors) from exc
    except FileNotFoundError as exc:
        raise SireControlError("FILE_MISSING", "El archivo ya no está en el almacenamiento.") from exc


def commit_import(
    db: Session, storage: FileStorageBackend, *, tenant_id: uuid.UUID, actor_id: uuid.UUID, file_id: uuid.UUID, mode: str, decimal_style: str, mapping: dict,
    ignore_total_rows: bool, sheet: str | None, encoding: str | None, delimiter: str | None, currency: str, source_label: str, source_date: date, notes: str | None,
    replaces_source_id: uuid.UUID | None, idempotency_key: uuid.UUID | None, max_rows: int,
) -> tuple[SireControlSource, bool, SireControl]:
    file = db.execute(select(SireControlFile).where(SireControlFile.id == file_id, SireControlFile.tenant_id == tenant_id)).scalar_one_or_none()
    if file is None or file.purpose != "SOURCE_FILE":
        raise SireControlError("FILE_NOT_FOUND")
    control = get_control(db, tenant_id, file.control_id)
    if file.status == "ACTIVE" and file.source_id is not None:
        prior = db.get(SireControlSource, file.source_id)
        if prior is not None and idempotency_key is not None and prior.idempotency_key == idempotency_key:
            return prior, False, control  # reintento idempotente del mismo envio
        raise SireControlError("FILE_ALREADY_IMPORTED", "Este archivo ya se importó como una fuente del expediente.")
    if file.status != "STAGED":
        raise SireControlError("FILE_NOT_IMPORTABLE")
    label = _clean_text(source_label, max_len=120, required=True, code="SOURCE_LABEL_REQUIRED")
    _check_source_date(source_date)
    try:
        if sha256_of(storage, file.storage_key) != file.sha256_hash:
            raise SireControlError("FILE_INTEGRITY_FAILED", "El archivo almacenado no coincide con su huella SHA-256.")
    except FileNotFoundError as exc:
        raise SireControlError("FILE_MISSING", "El archivo ya no está en el almacenamiento.") from exc
    table = _read_table(storage, file, max_rows=max_rows, sheet=sheet, encoding=encoding, delimiter=delimiter, preview=False)
    period = db.execute(select(TaxPeriod).where(TaxPeriod.id == control.tax_period_id, TaxPeriod.tenant_id == tenant_id)).scalar_one()
    try:
        result = parser.aggregate(
            table, {k: v for k, v in mapping.items()}, mode=mode, decimal_style=decimal_style, component=control.component, period_year=period.fiscal_year,
            period_month=period.month or 0, ignore_total_rows=ignore_total_rows,
        )
    except parser.ImportRejected as exc:
        raise SireControlError(exc.code, exc.message, exc.errors) from exc
    meta = {
        "mode": mode, "sheet": table.sheet, "encoding": table.encoding, "delimiter": table.delimiter, "decimal_style": result.decimal_style, "rows_read": result.rows_read,
        "rows_used": result.rows_used, "ignored_total_rows": result.ignored_total_rows, "mapping": {k: v for k, v in mapping.items() if v},
    }
    source, created = _insert_source(
        db, control=control, actor_id=actor_id, origin=file.origin, entry_mode="IMPORT", currency=currency, record_count=result.record_count, base=result.base_amount,
        igv=result.igv_amount, total=result.total_amount, source_label=label or "", source_date=source_date,
        notes=_clean_text(notes, max_len=2000, required=False, code="NOTES_TOO_LONG"), file=file, import_meta=meta, replaces_source_id=replaces_source_id,
        idempotency_key=idempotency_key,
    )
    return source, created, control


# --------------------------------------------------------------------------------------------------------- revision y tolerancia


def record_review(
    db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID, control_id: uuid.UUID, decision: str, notes: str | None, comparison_hash: str, idempotency_key: uuid.UUID | None,
) -> tuple[SireControlReview, bool, SireControl]:
    control = get_control(db, tenant_id, control_id, lock=True)
    if idempotency_key is not None:
        prior = db.execute(select(SireControlReview).where(SireControlReview.tenant_id == tenant_id, SireControlReview.idempotency_key == idempotency_key)).scalar_one_or_none()
        if prior is not None:
            if prior.control_id == control.id and prior.decision == decision and prior.comparison_hash == comparison_hash:
                return prior, False, control
            raise SireControlError("IDEMPOTENCY_CONFLICT", "La clave de idempotencia ya se usó con otra decisión.")
    active = _active_sources(db, control.id)
    sunat, darwin = _snapshot(active.get("SUNAT")), _snapshot(active.get("DARWIN"))
    current_hash = logic.comparison_hash(sunat, darwin, control.tolerance_amount)
    if current_hash != comparison_hash:
        raise SireControlError("COMPARISON_CHANGED", "La comparación cambió desde que la viste (nueva fuente o tolerancia): recarga antes de revisar.")
    comparison = logic.compare(sunat, darwin, control.tolerance_amount)
    try:
        logic.validate_review(decision, comparison.status, notes)
    except logic.ReviewRuleError as exc:
        raise SireControlError(exc.code) from exc
    now = clock.utcnow()
    current = _current_review(db, control.id)
    version = (db.execute(select(func.max(SireControlReview.version)).where(SireControlReview.control_id == control.id)).scalar_one() or 0) + 1
    if current is not None:
        current.status = "SUPERSEDED"
        current.superseded_at = now
        db.flush()
    review = SireControlReview(
        tenant_id=tenant_id, control_id=control.id, client_id=control.client_id, tax_period_id=control.tax_period_id, component=control.component, version=version,
        status="CURRENT", decision=decision, notes=(notes or "").strip() or None, comparison_hash=current_hash, comparison_status=comparison.status,
        comparison_snapshot=logic.snapshot_dict(comparison), sunat_source_id=active["SUNAT"].id if "SUNAT" in active else None,
        darwin_source_id=active["DARWIN"].id if "DARWIN" in active else None, idempotency_key=idempotency_key, reviewed_by=actor_id, reviewed_at=now,
    )
    db.add(review)
    db.flush()
    control.row_version += 1
    db.flush()
    audit_service.record(
        db, tenant_id=tenant_id, user_id=actor_id, action=AuditAction.SIRE_REVIEW_RECORDED, entity_type="sire_control_review", entity_id=review.id,
        metadata={"control_id": str(control.id), "component": control.component, "version": version, "decision": decision, "comparison_status": comparison.status},
    )
    return review, True, control


def set_tolerance(
    db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID, control_id: uuid.UUID, amount, reason: str | None, expected_row_version: int,
) -> SireControl:
    control = get_control(db, tenant_id, control_id, lock=True)
    if control.row_version != expected_row_version:
        raise SireControlError("VERSION_CONFLICT", "El expediente cambió: recarga antes de modificar la tolerancia.")
    try:
        value = logic.parse_amount(amount)
    except logic.AmountError as exc:
        raise SireControlError("AMOUNT_INVALID", str(exc)) from exc
    if value is not None and value < 0:
        raise SireControlError("AMOUNT_INVALID", "La tolerancia no puede ser negativa.")
    text = (reason or "").strip()
    if value is not None and not text:
        raise SireControlError("TOLERANCE_REASON_REQUIRED", "Una tolerancia exige indicar el motivo (queda visible y auditada).")
    control.tolerance_amount = value
    control.tolerance_reason = text or None if value is not None else None
    control.tolerance_set_by = actor_id if value is not None else None
    control.tolerance_set_at = clock.utcnow() if value is not None else None
    control.row_version += 1
    db.flush()
    audit_service.record(
        db, tenant_id=tenant_id, user_id=actor_id, action=AuditAction.SIRE_TOLERANCE_SET, entity_type="sire_control", entity_id=control.id,
        metadata={"component": control.component, "tolerance_set": value is not None},
    )
    return control


# ------------------------------------------------------------------------------------------------------- descarga e integridad


def get_file(db: Session, tenant_id: uuid.UUID, control_id: uuid.UUID, file_id: uuid.UUID) -> SireControlFile:
    file = db.execute(
        select(SireControlFile).where(SireControlFile.id == file_id, SireControlFile.control_id == control_id, SireControlFile.tenant_id == tenant_id)
    ).scalar_one_or_none()
    if file is None:
        raise SireControlError("FILE_NOT_FOUND")
    return file


def verify_file(storage: FileStorageBackend, file: SireControlFile) -> dict:
    present = storage.exists(file.storage_key)
    computed = sha256_of(storage, file.storage_key) if present else None
    return {"file_id": file.id, "stored_sha256": file.sha256_hash, "computed_sha256": computed, "intact": present and computed == file.sha256_hash, "present": present}


def record_download(db: Session, *, tenant_id: uuid.UUID, actor_id: uuid.UUID, file: SireControlFile) -> None:
    audit_service.record(
        db, tenant_id=tenant_id, user_id=actor_id, action=AuditAction.SIRE_FILE_DOWNLOADED, entity_type="sire_control_file", entity_id=file.id,
        metadata={"control_id": str(file.control_id), "sha256_prefix": file.sha256_hash[:16]},
    )


# ---------------------------------------------------------------------------------------------------------------- historial


def history(db: Session, tenant_id: uuid.UUID, control_id: uuid.UUID) -> dict:
    control = get_control(db, tenant_id, control_id)
    sources = db.execute(select(SireControlSource).where(SireControlSource.control_id == control.id).order_by(SireControlSource.created_at.desc(), SireControlSource.version.desc())).scalars().all()
    reviews = db.execute(select(SireControlReview).where(SireControlReview.control_id == control.id).order_by(SireControlReview.reviewed_at.desc(), SireControlReview.version.desc())).scalars().all()
    files = db.execute(select(SireControlFile).where(SireControlFile.control_id == control.id, SireControlFile.status != "STAGED").order_by(SireControlFile.uploaded_at.desc())).scalars().all()
    items = (
        [{"kind": "SOURCE", "at": s.created_at, "version": s.version, "origin": s.origin, "status": s.status, "label": s.source_label, "by": s.created_by, "ref_id": s.id} for s in sources]
        + [{"kind": "REVIEW", "at": r.reviewed_at, "version": r.version, "origin": None, "status": r.status, "label": r.decision, "by": r.reviewed_by, "ref_id": r.id} for r in reviews]
        + [{"kind": "FILE", "at": f.uploaded_at, "version": None, "origin": f.origin, "status": f.status, "label": f.original_filename, "by": f.uploaded_by, "ref_id": f.id} for f in files]
    )
    items.sort(key=lambda i: (i["at"], i["version"] or 0), reverse=True)  # desempate por version: relojes iguales no desordenan el historial
    return {
        "control_id": control.id, "items": items, "sources": [_source_out(s) for s in sources], "reviews": [_review_out(db, r) for r in reviews], "files": [_file_out(f) for f in files],
    }


# ------------------------------------------------------------------------------------------------------------------ reporte


def build_report(db: Session, tenant_id: uuid.UUID, control_id: uuid.UUID) -> dict:
    control = get_control(db, tenant_id, control_id)
    view = control_view(db, tenant_id=tenant_id, client_id=control.client_id, period_id=control.tax_period_id, component=control.component, control=control)
    from app.models.client import Client

    client = client_repository.get_by_id(db, tenant_id, control.client_id)
    period = db.execute(select(TaxPeriod).where(TaxPeriod.id == control.tax_period_id, TaxPeriod.tenant_id == tenant_id)).scalar_one()
    assert isinstance(client, Client)
    hist = history(db, tenant_id, control_id)
    differences = [c for c in view["comparison"]["concepts"] if c["comparable"] and not c["exact"]]
    body = {
        "report_version": REPORT_VERSION,
        "disclaimer": logic.DISCLAIMER,
        "identification": {
            "control_id": str(control.id), "component": control.component, "component_label": COMPONENT_LABEL[control.component], "client_id": str(control.client_id),
            "client_business_name": client.business_name, "client_ruc": client.ruc, "tax_period_id": str(control.tax_period_id), "tax_period_code": period.canonical_code,
            "control_row_version": control.row_version, "summary_code": view["summary_code"],
        },
        "sources": {
            "SUNAT": _report_source(view["sunat_source"]), "DARWIN": _report_source(view["darwin_source"]),
        },
        "comparison": {
            "status": view["comparison"]["status"], "comparison_hash": view["comparison"]["comparison_hash"], "tolerance": view["comparison"]["tolerance"],
            "tolerance_reason": view["tolerance_reason"], "tolerance_applied": view["comparison"]["tolerance_applied"], "concepts": view["comparison"]["concepts"],
            "one_sided_concepts": view["comparison"]["one_sided_concepts"], "currency_mismatch": view["comparison"]["currency_mismatch"],
        },
        "differences": differences,
        "files": [
            {"id": str(f["id"]), "origin": f["origin"], "purpose": f["purpose"], "status": f["status"], "original_filename": f["original_filename"], "mime_type": f["mime_type"],
             "size_bytes": f["size_bytes"], "sha256": f["sha256_hash"], "uploaded_at": f["uploaded_at"].isoformat(), "source_id": str(f["source_id"]) if f["source_id"] else None}
            for f in hist["files"]
        ],
        "review": _report_review(view["current_review"], view["review_stale"]),
        "review_history": [_report_review(r, False) for r in hist["reviews"]],
        "status_legend": {
            "numeric_match": "Cuadre numérico entre las dos fuentes aportadas.", "professional_review": "Decisión humana registrada por el responsable.",
            "documentary_evidence": "Archivos conservados con su huella SHA-256 (no se interpretan automáticamente salvo CSV/XLSX con mapeo).",
            "official_filing": "NO está acreditada por este informe: la presentación oficial ante SUNAT se verifica en SUNAT.",
        },
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return {**body, "generated_at": clock.utcnow(), "report_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


def _report_source(source: dict | None) -> dict | None:
    if source is None:
        return None
    return {k: (str(v) if isinstance(v, (uuid.UUID, date, datetime)) else v) for k, v in source.items() if k not in {"created_by", "import_summary"}} | {
        "created_by": str(source["created_by"]), "import_summary": source["import_summary"],
    }


def _report_review(review: dict | None, stale: bool) -> dict | None:
    if review is None:
        return None
    return {
        "version": review["version"], "status": review["status"], "decision": review["decision"], "notes": review["notes"], "reviewed_by": str(review["reviewed_by"]),
        "reviewed_by_name": review.get("reviewed_by_name"), "reviewed_at": review["reviewed_at"].isoformat(), "comparison_status": review["comparison_status"],
        "comparison_hash": review["comparison_hash"], "stale": stale,
    }


# ----------------------------------------------------------------------------------------------------------------- cockpit


def cockpit_codes(db: Session, *, tenant_id: uuid.UUID, period_id: uuid.UUID, client_ids: list[uuid.UUID]) -> dict[tuple[uuid.UUID, str], str]:
    """Codigo resumido por (cliente, componente) en TRES consultas (sin N+1). No crea expedientes. Falta de expediente => NO_CONTROL."""
    if not client_ids:
        return {}
    controls = db.execute(
        select(SireControl).where(SireControl.tenant_id == tenant_id, SireControl.tax_period_id == period_id, SireControl.client_id.in_(client_ids))
    ).scalars().all()
    if not controls:
        return {}
    ids = [c.id for c in controls]
    sources: dict[uuid.UUID, dict[str, SireControlSource]] = {}
    for s in db.execute(select(SireControlSource).where(SireControlSource.control_id.in_(ids), SireControlSource.status == "ACTIVE")).scalars():
        sources.setdefault(s.control_id, {})[s.origin] = s
    reviews = {r.control_id: r for r in db.execute(select(SireControlReview).where(SireControlReview.control_id.in_(ids), SireControlReview.status == "CURRENT")).scalars()}
    out: dict[tuple[uuid.UUID, str], str] = {}
    for control in controls:
        active = sources.get(control.id, {})
        sunat, darwin = _snapshot(active.get("SUNAT")), _snapshot(active.get("DARWIN"))
        comparison = logic.compare(sunat, darwin, control.tolerance_amount)
        review = reviews.get(control.id)
        stale = review is not None and review.comparison_hash != logic.comparison_hash(sunat, darwin, control.tolerance_amount)
        out[(control.client_id, control.component)] = logic.summary_code(
            exists=True, status=comparison.status, review_decision=review.decision if review else None, review_stale=stale
        )
    return out
