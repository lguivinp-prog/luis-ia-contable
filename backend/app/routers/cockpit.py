import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core import clock
from app.core.permissions import Permission, require_permission
from app.core.timezone import today_lima
from app.db.session import get_db
from app.models.user import User
from app.schemas.cockpit import CockpitOut
from app.services import cockpit_logic, cockpit_service

router = APIRouter(prefix="/api/v1/cockpit", tags=["cockpit"])

OverallRisk = Literal[
    "CRITICAL", "OVERDUE", "DUE_TODAY", "PENDING_TAX", "PENDING_DOCUMENTS", "PERIOD_NOT_GENERATED",
    "CLOSED_WITH_PENDING", "PENDING_FEES", "UPCOMING", "ON_TRACK", "CLOSED", "NOT_APPLICABLE",
]
SireStatus = Literal["NOT_CONFIGURED", "NOT_STARTED", "IN_PROGRESS", "READY_FOR_SUNAT", "PRESENTED", "NOT_APPLICABLE"]
DocumentStatus = Literal["NOT_CONFIGURED", "PENDING", "PARTIAL", "OBSERVED", "COMPLETE", "NOT_APPLICABLE"]
FilingProgress = Literal["NOT_GENERATED", "NOT_APPLICABLE", "NONE_FILED", "PARTIAL", "ALL_FILED"]

assert set(OverallRisk.__args__) == set(cockpit_logic.OVERALL_RISK_ORDER)


@router.get("/monthly", response_model=CockpitOut)
def monthly_cockpit(
    tax_period_id: uuid.UUID,
    search: str | None = Query(default=None, max_length=100),
    overall_risk: OverallRisk | None = None,
    sire_status: SireStatus | None = None,
    document_status: DocumentStatus | None = None,
    filing_progress: FilingProgress | None = None,
    client_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.DASHBOARD_READ)),
) -> CockpitOut:
    """Cockpit Multi-RUC mensual: UNA fila por cliente activo y periodo (incluye clientes sin declaraciones generadas).
    Solo lectura y derivado; no genera declaraciones ni persiste nada. Paginacion por CLIENTE."""
    try:
        return cockpit_service.build_monthly_cockpit(
            db,
            current_user.tenant_id,
            tax_period_id,
            today=today_lima(clock.utcnow()),
            search=search,
            overall_risk=overall_risk,
            sire_status=sire_status,
            document_status=document_status,
            filing_progress=filing_progress,
            client_id=client_id,
            limit=limit,
            offset=offset,
        )
    except cockpit_service.CockpitPeriodNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": "TAX_PERIOD_NOT_FOUND"}) from None
