from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core import clock
from app.core.dependencies import get_client_ip, verify_csrf
from app.core.permissions import Permission, require_permission
from app.db.session import get_db
from app.models.audit_log import AuditAction
from app.models.user import User
from app.schemas.report import ReportExportRequest
from app.services import report_service
from app.services.audit_service import record as record_audit

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])

_CSV_MEDIA_TYPE = "text/csv"
_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.post("/export")
def export_report(
    payload: ReportExportRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.REPORT_EXPORT)),
    _csrf: None = Depends(verify_csrf),
) -> Response:
    try:
        data = report_service.build_report_data(
            db,
            current_user.tenant_id,
            payload.report_type,
            tax_period_id=payload.tax_period_id,
            client_id=payload.client_id,
        )
    except report_service.UnknownReportTypeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    filename = f"{payload.report_type.lower()}.{payload.format}"
    if payload.format == "csv":
        content: bytes | str = report_service.render_csv(data)
        media_type = _CSV_MEDIA_TYPE
    else:
        content = report_service.render_xlsx(
            data, generated_at=clock.utcnow(), generated_by=current_user.email
        )
        media_type = _XLSX_MEDIA_TYPE

    record_audit(
        db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        action=AuditAction.REPORT_EXPORT,
        entity_type="report",
        entity_id=None,
        metadata={
            "report_type": payload.report_type,
            "format": payload.format,
            "row_count": len(data.rows),
            "truncated": data.truncated,
        },
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
