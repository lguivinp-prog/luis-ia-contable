import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_client_ip, get_current_user, verify_csrf
from app.db.session import get_db
from app.models.audit_log import AuditAction
from app.models.user import User
from app.schemas.tax_schedule import (
    ApprovalHistoryOut,
    ApproveRequest,
    CancelRequest,
    CompareVersionsOut,
    ConfirmImportRequest,
    ImportRunOut,
    PreviewDueDateOut,
    RejectRequest,
    SubmitForReviewRequest,
    TaxScheduleEntryOut,
    TaxScheduleSourceCreate,
    TaxScheduleSourceOut,
    TaxScheduleVersionOut,
)
from app.services import (
    client_service,
    tax_schedule_import_service,
    tax_schedule_resolver,
    tax_schedule_service,
)
from app.services.audit_service import record as record_audit
from app.services.client_service import ClientNotFoundError
from app.services.csv_utils import sanitize_csv_cell
from app.services.tax_schedule_import_service import (
    CsvContentTypeError,
    CsvEncodingError,
    CsvTooLargeError,
    CsvValidationError,
    ImportRunNotConfirmableError,
    ImportRunNotFoundError,
)
from app.services.tax_schedule_resolver import (
    AmbiguousScheduleRuleError,
    NoApplicableScheduleRuleError,
    NoApprovedVersionError,
)
from app.services.tax_schedule_service import (
    ConcurrentApprovalConflictError,
    InvalidTransitionError,
    MissingCommentError,
    TaxScheduleSourceNotFoundError,
    TaxScheduleVersionNotFoundError,
)

router = APIRouter(prefix="/api/v1/tax-schedules", tags=["tax-schedules"])


# --- sources ---


@router.get("/sources", response_model=list[TaxScheduleSourceOut])
def list_sources(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
) -> list[TaxScheduleSourceOut]:
    return tax_schedule_service.list_sources(db, current_user.tenant_id)


@router.post("/sources", response_model=TaxScheduleSourceOut, status_code=status.HTTP_201_CREATED)
def create_source(
    payload: TaxScheduleSourceCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> TaxScheduleSourceOut:
    source = tax_schedule_service.create_source(
        db,
        current_user.tenant_id,
        payload.obligation_id,
        payload.name,
        payload.description,
        payload.reference_url,
    )
    record_audit(
        db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        action=AuditAction.TAX_SCHEDULE_SOURCE_CREATE,
        entity_type="tax_schedule_source",
        entity_id=source.id,
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    db.refresh(source)
    return source


@router.get("/sources/{source_id}", response_model=TaxScheduleSourceOut)
def get_source(
    source_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaxScheduleSourceOut:
    try:
        return tax_schedule_service.get_source(db, current_user.tenant_id, source_id)
    except TaxScheduleSourceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


# --- import runs ---


@router.post(
    "/sources/{source_id}/import-runs", response_model=ImportRunOut, status_code=status.HTTP_201_CREATED
)
async def create_import_run(
    source_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    fiscal_year: int = Form(...),
    schedule_type: str = Form("GENERAL"),
    scope_category_code: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ImportRunOut:
    try:
        tax_schedule_service.get_source(db, current_user.tenant_id, source_id)
        file_bytes = await file.read()
        import_run = tax_schedule_import_service.receive_and_validate_import(
            db,
            current_user.tenant_id,
            current_user.id,
            source_id,
            filename=file.filename or "cronograma.csv",
            file_bytes=file_bytes,
            content_type=file.content_type,
            fiscal_year=fiscal_year,
            schedule_type=schedule_type,
            scope_category_code=scope_category_code or None,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TAX_SCHEDULE_IMPORT_RUN_CREATE,
            entity_type="tax_schedule_import_run",
            entity_id=import_run.id,
            metadata={"status": import_run.status, "row_count": import_run.row_count},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(import_run)
        return import_run
    except TaxScheduleSourceNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (CsvTooLargeError, CsvContentTypeError, CsvEncodingError, CsvValidationError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/import-runs/{import_run_id}", response_model=ImportRunOut)
def get_import_run_preview(
    import_run_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ImportRunOut:
    try:
        return tax_schedule_import_service.get_import_run(db, current_user.tenant_id, import_run_id)
    except ImportRunNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/import-runs/{import_run_id}/confirm", response_model=TaxScheduleVersionOut)
def confirm_import_run(
    import_run_id: uuid.UUID,
    payload: ConfirmImportRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> TaxScheduleVersionOut:
    try:
        _import_run, version = tax_schedule_import_service.confirm_import(
            db,
            current_user.tenant_id,
            current_user.id,
            import_run_id,
            version_label=payload.version_label,
            normative_reference=payload.normative_reference,
            source_document_url=payload.source_document_url,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TAX_SCHEDULE_IMPORT_CONFIRM,
            entity_type="tax_schedule_version",
            entity_id=version.id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        db.refresh(version)
        return version
    except ImportRunNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ImportRunNotConfirmableError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/import-runs/{import_run_id}/reject")
def reject_import_run(
    import_run_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> dict:
    try:
        tax_schedule_import_service.reject_import(db, current_user.tenant_id, import_run_id)
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TAX_SCHEDULE_IMPORT_REJECT,
            entity_type="tax_schedule_import_run",
            entity_id=import_run_id,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        return {"status": "ok"}
    except ImportRunNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ImportRunNotConfirmableError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


# --- versions ---


@router.get("/versions", response_model=list[TaxScheduleVersionOut])
def list_versions(
    source_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TaxScheduleVersionOut]:
    return tax_schedule_service.list_versions(db, current_user.tenant_id, source_id=source_id)


@router.get("/versions/compare", response_model=CompareVersionsOut)
def compare_versions(
    version_a: uuid.UUID,
    version_b: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CompareVersionsOut:
    try:
        return tax_schedule_service.compare_versions(db, current_user.tenant_id, version_a, version_b)
    except TaxScheduleVersionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/versions/{version_id}", response_model=TaxScheduleVersionOut)
def get_version(
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TaxScheduleVersionOut:
    try:
        return tax_schedule_service.get_version(db, current_user.tenant_id, version_id)
    except TaxScheduleVersionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/versions/{version_id}/entries", response_model=list[TaxScheduleEntryOut])
def list_version_entries(
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TaxScheduleEntryOut]:
    from app.repositories import tax_schedule_repository

    tax_schedule_service.get_version(db, current_user.tenant_id, version_id)
    return tax_schedule_repository.list_entries(db, current_user.tenant_id, version_id)


@router.get("/versions/{version_id}/entries.csv")
def export_version_entries_csv(
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    from app.repositories import tax_schedule_repository

    tax_schedule_service.get_version(db, current_user.tenant_id, version_id)
    entries = tax_schedule_repository.list_entries(db, current_user.tenant_id, version_id)

    lines = ["tax_period,applicable_last_digits,required_category_code,due_date_effective,due_date_type"]
    for e in entries:
        digits = ";".join(str(d) for d in e.applicable_last_digits) if e.applicable_last_digits else ""
        row = [
            e.tax_period,
            digits,
            e.required_category_code or "",
            e.due_date_effective.isoformat(),
            e.due_date_type,
        ]
        lines.append(",".join(sanitize_csv_cell(cell) for cell in row))

    csv_content = "\n".join(lines) + "\n"
    return Response(content=csv_content, media_type="text/csv")


@router.post("/versions/{version_id}/submit-for-review", response_model=TaxScheduleVersionOut)
def submit_for_review(
    version_id: uuid.UUID,
    payload: SubmitForReviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> TaxScheduleVersionOut:
    try:
        version = tax_schedule_service.submit_for_review(
            db, current_user.tenant_id, current_user.id, version_id, payload.comment
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TAX_SCHEDULE_SUBMIT_FOR_REVIEW,
            entity_type="tax_schedule_version",
            entity_id=version.id,
        )
        db.commit()
        db.refresh(version)
        return version
    except TaxScheduleVersionNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/versions/{version_id}/approve", response_model=TaxScheduleVersionOut)
def approve_version(
    version_id: uuid.UUID,
    payload: ApproveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> TaxScheduleVersionOut:
    try:
        version = tax_schedule_service.approve(
            db, current_user.tenant_id, current_user.id, version_id, payload.comment
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TAX_SCHEDULE_APPROVE,
            entity_type="tax_schedule_version",
            entity_id=version.id,
            metadata={"comment": payload.comment},
        )
        db.commit()
        db.refresh(version)
        return version
    except TaxScheduleVersionNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except MissingCommentError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except InvalidTransitionError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ConcurrentApprovalConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/versions/{version_id}/reject", response_model=TaxScheduleVersionOut)
def reject_version(
    version_id: uuid.UUID,
    payload: RejectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> TaxScheduleVersionOut:
    try:
        version = tax_schedule_service.reject(
            db, current_user.tenant_id, current_user.id, version_id, payload.reason
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TAX_SCHEDULE_REJECT,
            entity_type="tax_schedule_version",
            entity_id=version.id,
            metadata={"reason": payload.reason},
        )
        db.commit()
        db.refresh(version)
        return version
    except TaxScheduleVersionNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (MissingCommentError, InvalidTransitionError) as exc:
        db.rollback()
        code = (
            status.HTTP_422_UNPROCESSABLE_ENTITY
            if isinstance(exc, MissingCommentError)
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.post("/versions/{version_id}/cancel", response_model=TaxScheduleVersionOut)
def cancel_version(
    version_id: uuid.UUID,
    payload: CancelRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> TaxScheduleVersionOut:
    try:
        version = tax_schedule_service.cancel(
            db, current_user.tenant_id, current_user.id, version_id, payload.reason
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TAX_SCHEDULE_CANCEL,
            entity_type="tax_schedule_version",
            entity_id=version.id,
            metadata={"reason": payload.reason},
        )
        db.commit()
        db.refresh(version)
        return version
    except TaxScheduleVersionNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (MissingCommentError, InvalidTransitionError) as exc:
        db.rollback()
        code = (
            status.HTTP_422_UNPROCESSABLE_ENTITY
            if isinstance(exc, MissingCommentError)
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.get("/versions/{version_id}/approval-history", response_model=list[ApprovalHistoryOut])
def get_approval_history(
    version_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ApprovalHistoryOut]:
    tax_schedule_service.get_version(db, current_user.tenant_id, version_id)
    return tax_schedule_service.list_approval_history(db, current_user.tenant_id, version_id)


# --- preview ---


@router.get("/preview-due-date", response_model=PreviewDueDateOut)
def preview_due_date(
    request: Request,
    source_id: uuid.UUID,
    client_id: uuid.UUID,
    tax_period: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PreviewDueDateOut:
    try:
        client = client_service.get_client(db, current_user.tenant_id, client_id)
        resolution = tax_schedule_resolver.resolve_due_date(
            db,
            current_user.tenant_id,
            source_id=source_id,
            client_id=client_id,
            last_digit=client.ruc_last_digit,
            tax_period=tax_period,
        )
        record_audit(
            db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=AuditAction.TAX_SCHEDULE_PREVIEW_QUERY,
            entity_type="client",
            entity_id=client_id,
            metadata={"source_id": str(source_id), "tax_period": tax_period},
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        return PreviewDueDateOut(
            version_id=resolution.version_id,
            version_label=resolution.version_label,
            source_name=resolution.source_name,
            rule_type=resolution.rule_type,
            last_digit=resolution.last_digit,
            matched_category_code=resolution.matched_category_code,
            due_date_original=resolution.due_date_original,
            due_date_effective=resolution.due_date_effective,
            due_date_type=resolution.due_date_type,
            is_extension=resolution.is_extension,
            warnings=resolution.warnings,
        )
    except ClientNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except NoApprovedVersionError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except NoApplicableScheduleRuleError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AmbiguousScheduleRuleError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
