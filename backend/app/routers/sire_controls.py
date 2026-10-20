"""M4 -- API del expediente de control SIRE (RVIE/RCE) por contribuyente y periodo. Lectura y escritura con permisos SEPARADOS; CSRF en toda mutacion; tenant en
cada consulta; nunca expone rutas fisicas ni claves de almacenamiento. NINGUNA ruta toca declaraciones, pagos, vencimientos, obligaciones ni SUNAT."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.dependencies import get_file_storage, verify_csrf
from app.core.file_storage import FileStorageBackend, compensate_physical_write
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.models.user import User
from app.schemas.sire_control import (
    Component as ComponentLiteral,
    ControlOut,
    ControlsForPeriodOut,
    FileIntegrityOut,
    FileStoredOut,
    HistoryOut,
    ImportRequest,
    ManualSourceRequest,
    PreviewOut,
    ReportOut,
    ReviewOut,
    ReviewRequest,
    SourceCreatedOut,
    ToleranceRequest,
)
from app.services import sire_control_service as svc
from app.services.document_file_naming import build_content_disposition

router = APIRouter(prefix="/api/v1/sire-controls", tags=["sire-controls"])
_settings = get_settings()

_NOT_FOUND = {"CLIENT_NOT_FOUND", "PERIOD_NOT_FOUND", "CONTROL_NOT_FOUND", "FILE_NOT_FOUND", "SOURCE_NOT_FOUND", "FILE_MISSING"}
_CONFLICT = {
    "SOURCE_CHANGED", "COMPARISON_CHANGED", "VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT", "FILE_ALREADY_IMPORTED", "FILE_NOT_IMPORTABLE", "FILE_INTEGRITY_FAILED",
}
_STATUS = {"FILE_TOO_LARGE": 413, "FILE_TYPE_NOT_ALLOWED": 415, "FILE_EMPTY": 400}


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _http(exc: svc.SireControlError) -> HTTPException:
    if exc.code in _NOT_FOUND:
        http_status = status.HTTP_404_NOT_FOUND
    elif exc.code in _CONFLICT:
        http_status = status.HTTP_409_CONFLICT
    else:
        http_status = _STATUS.get(exc.code, status.HTTP_422_UNPROCESSABLE_ENTITY)
    detail: dict = {"code": exc.code, "message": exc.message}
    if getattr(exc, "errors", None):
        detail["errors"] = exc.errors
    return HTTPException(status_code=http_status, detail=detail)


# --- lectura (sire_control:read) ---------------------------------------------------------------------------------------------------


@router.get("/by-period", response_model=ControlsForPeriodOut)
def controls_for_period(
    response: Response,
    client_id: uuid.UUID = Query(),
    tax_period_id: uuid.UUID = Query(),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SIRE_CONTROL_READ)),
) -> dict:
    """RVIE y RCE de un cliente y periodo. Consultar NUNCA crea expedientes: un componente sin expediente se informa con `exists=false`."""
    _no_store(response)
    try:
        return svc.view_for_period(db, tenant_id=current_user.tenant_id, client_id=client_id, period_id=tax_period_id)
    except svc.SireControlError as exc:
        raise _http(exc) from exc


@router.get("/{control_id}", response_model=ControlOut)
def get_control(
    control_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SIRE_CONTROL_READ)),
) -> dict:
    _no_store(response)
    try:
        return svc.view_by_id(db, current_user.tenant_id, control_id)
    except svc.SireControlError as exc:
        raise _http(exc) from exc


@router.get("/{control_id}/history", response_model=HistoryOut)
def get_history(
    control_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SIRE_CONTROL_READ)),
) -> dict:
    _no_store(response)
    try:
        return svc.history(db, current_user.tenant_id, control_id)
    except svc.SireControlError as exc:
        raise _http(exc) from exc


@router.get("/{control_id}/report", response_model=ReportOut)
def get_report(
    control_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SIRE_CONTROL_READ)),
) -> dict:
    """Informe compacto verificable (huella SHA-256 del contenido). NO es una constancia oficial de presentacion."""
    _no_store(response)
    try:
        return svc.build_report(db, current_user.tenant_id, control_id)
    except svc.SireControlError as exc:
        raise _http(exc) from exc


@router.get("/{control_id}/files/{file_id}/verify", response_model=FileIntegrityOut)
def verify_file(
    control_id: uuid.UUID,
    file_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    storage: FileStorageBackend = Depends(get_file_storage),
    current_user: User = Depends(require_permission(Permission.SIRE_CONTROL_READ)),
) -> dict:
    _no_store(response)
    try:
        return svc.verify_file(storage, svc.get_file(db, current_user.tenant_id, control_id, file_id))
    except svc.SireControlError as exc:
        raise _http(exc) from exc


@router.get("/{control_id}/files/{file_id}/download")
def download_file(
    control_id: uuid.UUID,
    file_id: uuid.UUID,
    db: Session = Depends(get_db),
    storage: FileStorageBackend = Depends(get_file_storage),
    current_user: User = Depends(require_permission(Permission.SIRE_CONTROL_READ)),
) -> StreamingResponse:
    try:
        file = svc.get_file(db, current_user.tenant_id, control_id, file_id)
        if not storage.exists(file.storage_key):
            raise svc.SireControlError("FILE_MISSING", "El archivo ya no está en el almacenamiento.")
        svc.record_download(db, tenant_id=current_user.tenant_id, actor_id=current_user.id, file=file)
        db.commit()
    except svc.SireControlError as exc:
        db.rollback()
        raise _http(exc) from exc

    def chunks():
        stream = storage.open(file.storage_key)
        try:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                yield block
        finally:
            stream.close()

    return StreamingResponse(
        chunks(), media_type=file.mime_type,
        headers={"Content-Disposition": build_content_disposition(file.original_filename), "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
                 "Content-Security-Policy": "default-src 'none'; sandbox"},
    )


# --- escritura (sire_control:write + CSRF) -------------------------------------------------------------------------------------------


def _discard(db: Session, storage: FileStorageBackend, new_keys: list[str]) -> None:
    """Fallo en cualquier punto: la transaccion se revierte Y los blobs recien escritos se eliminan (nunca quedan archivos huerfanos)."""
    db.rollback()
    for key in new_keys:
        compensate_physical_write(storage, key=key)


@router.post("/sources/manual", response_model=SourceCreatedOut)
def add_manual_source(
    payload: ManualSourceRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SIRE_CONTROL_WRITE)),
    _csrf: None = Depends(verify_csrf),
) -> dict:
    _no_store(response)
    try:
        source, created, control = svc.add_manual_source(
            db, tenant_id=current_user.tenant_id, actor_id=current_user.id, client_id=payload.client_id, tax_period_id=payload.tax_period_id,
            component=payload.component, origin=payload.origin, currency=payload.currency, record_count=payload.record_count, base_amount=payload.base_amount,
            igv_amount=payload.igv_amount, total_amount=payload.total_amount, source_label=payload.source_label, source_date=payload.source_date, notes=payload.notes,
            replaces_source_id=payload.replaces_source_id, idempotency_key=payload.idempotency_key,
        )
        db.commit()
    except svc.SireControlError as exc:
        db.rollback()
        raise _http(exc) from exc
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return {"created": created, "source": svc._source_out(source), "control": svc.view_by_id(db, current_user.tenant_id, control.id)}


@router.post("/files/preview", response_model=PreviewOut)
def preview_import_file(
    response: Response,
    client_id: uuid.UUID = Form(),
    tax_period_id: uuid.UUID = Form(),
    component: ComponentLiteral = Form(),
    origin: str = Form(),
    sheet: str | None = Form(None),
    encoding: str | None = Form(None),
    delimiter: str | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    storage: FileStorageBackend = Depends(get_file_storage),
    current_user: User = Depends(require_permission(Permission.SIRE_CONTROL_WRITE)),
    _csrf: None = Depends(verify_csrf),
) -> dict:
    """Sube el archivo (queda conservado y hasheado, SIN usarse todavia) y devuelve la vista previa: hojas, encabezados, muestra y estilo decimal detectado."""
    _no_store(response)
    if origin not in ("SUNAT", "DARWIN"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "ORIGIN_INVALID", "message": "Origen inválido."})
    new_keys: list[str] = []
    try:
        result = svc.preview_import(
            db, storage, tenant_id=current_user.tenant_id, actor_id=current_user.id, client_id=client_id, tax_period_id=tax_period_id, component=component, origin=origin,
            stream=file.file, original_filename=file.filename or "archivo", max_bytes=_settings.sire_file_max_bytes, sheet=sheet, encoding=encoding, delimiter=delimiter,
            created_keys=new_keys,
        )
        db.commit()
    except svc.SireControlError as exc:
        _discard(db, storage, new_keys)
        raise _http(exc) from exc
    except Exception:
        _discard(db, storage, new_keys)
        raise
    return result


@router.post("/sources/import", response_model=SourceCreatedOut)
def import_source(
    payload: ImportRequest,
    response: Response,
    db: Session = Depends(get_db),
    storage: FileStorageBackend = Depends(get_file_storage),
    current_user: User = Depends(require_permission(Permission.SIRE_CONTROL_WRITE)),
    _csrf: None = Depends(verify_csrf),
) -> dict:
    _no_store(response)
    try:
        source, created, control = svc.commit_import(
            db, storage, tenant_id=current_user.tenant_id, actor_id=current_user.id, file_id=payload.file_id, mode=payload.mode, decimal_style=payload.decimal_style,
            mapping=payload.mapping.model_dump(), ignore_total_rows=payload.ignore_total_rows, sheet=payload.sheet, encoding=payload.encoding, delimiter=payload.delimiter,
            currency=payload.currency, source_label=payload.source_label, source_date=payload.source_date, notes=payload.notes,
            replaces_source_id=payload.replaces_source_id, idempotency_key=payload.idempotency_key, max_rows=_settings.sire_import_max_rows,
        )
        db.commit()
    except svc.SireControlError as exc:
        db.rollback()
        raise _http(exc) from exc
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return {"created": created, "source": svc._source_out(source), "control": svc.view_by_id(db, current_user.tenant_id, control.id)}


@router.post("/{control_id}/files", response_model=FileStoredOut)
def attach_evidence_file(
    control_id: uuid.UUID,
    response: Response,
    origin: str = Form(),
    description: str | None = Form(None),
    source_id: uuid.UUID | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    storage: FileStorageBackend = Depends(get_file_storage),
    current_user: User = Depends(require_permission(Permission.SIRE_CONTROL_WRITE)),
    _csrf: None = Depends(verify_csrf),
) -> dict:
    """Adjunta evidencia (PDF, imagen, CSV o XLSX). Se conserva con su SHA-256; NO se interpreta. Un archivo identico ya vigente no se duplica."""
    _no_store(response)
    if origin not in ("SUNAT", "DARWIN"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "ORIGIN_INVALID", "message": "Origen inválido."})
    new_keys: list[str] = []
    try:
        stored, created = svc.attach_evidence(
            db, storage, tenant_id=current_user.tenant_id, actor_id=current_user.id, control_id=control_id, origin=origin, stream=file.file,
            original_filename=file.filename or "archivo", description=description, source_id=source_id, max_bytes=_settings.sire_file_max_bytes,
            created_keys=new_keys,
        )
        out = svc._file_out(stored)
        db.commit()
    except svc.SireControlError as exc:
        _discard(db, storage, new_keys)
        raise _http(exc) from exc
    except Exception:
        _discard(db, storage, new_keys)
        raise
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return {"created": created, "file": out}


@router.post("/{control_id}/reviews", response_model=ReviewOut)
def record_review(
    control_id: uuid.UUID,
    payload: ReviewRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SIRE_CONTROL_WRITE)),
    _csrf: None = Depends(verify_csrf),
) -> dict:
    """Revision PROFESIONAL del cuadre. No presenta nada ante SUNAT ni cambia ningun estado tributario."""
    _no_store(response)
    try:
        review, created, _control = svc.record_review(
            db, tenant_id=current_user.tenant_id, actor_id=current_user.id, control_id=control_id, decision=payload.decision, notes=payload.notes,
            comparison_hash=payload.comparison_hash, idempotency_key=payload.idempotency_key,
        )
        out = svc._review_out(db, review)
        db.commit()
    except svc.SireControlError as exc:
        db.rollback()
        raise _http(exc) from exc
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return out


@router.post("/{control_id}/tolerance", response_model=ControlOut)
def set_tolerance(
    control_id: uuid.UUID,
    payload: ToleranceRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SIRE_CONTROL_WRITE)),
    _csrf: None = Depends(verify_csrf),
) -> dict:
    """Tolerancia EXPLICITA y trazable (solo importes). Por defecto la comparacion es exacta; nunca oculta las diferencias originales."""
    _no_store(response)
    try:
        svc.set_tolerance(
            db, tenant_id=current_user.tenant_id, actor_id=current_user.id, control_id=control_id, amount=payload.amount, reason=payload.reason,
            expected_row_version=payload.expected_row_version,
        )
        db.commit()
    except svc.SireControlError as exc:
        db.rollback()
        raise _http(exc) from exc
    return svc.view_by_id(db, current_user.tenant_id, control_id)
