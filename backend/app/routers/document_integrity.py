from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_file_storage
from app.core.file_storage import FileStorageBackend
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.models.user import User
from app.schemas.document_integrity import IntegrityCategory, IntegrityReportEntryOut, IntegrityReportOut
from app.services import document_integrity_service

router = APIRouter(prefix="/api/v1", tags=["document-integrity"])

_VALID_CATEGORIES = set(IntegrityCategory.__args__)  # type: ignore[attr-defined]


@router.get("/document-files/integrity-report", response_model=IntegrityReportOut)
def get_integrity_report(
    category: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    storage: FileStorageBackend = Depends(get_file_storage),
    current_user: User = Depends(require_permission(Permission.DOCUMENT_MANAGE)),
) -> dict:
    if category is not None and category not in _VALID_CATEGORIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Categoria invalida. Valores permitidos: {sorted(_VALID_CATEGORIES)}",
        )
    report = document_integrity_service.build_integrity_report(
        db,
        storage,
        current_user.tenant_id,
        category=category,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [IntegrityReportEntryOut.model_validate(entry) for entry in report.items],
        "total": report.total,
        "limit": limit,
        "offset": offset,
        "counts": report.counts,
    }
