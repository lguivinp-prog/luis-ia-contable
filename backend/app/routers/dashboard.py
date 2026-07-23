import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core import clock
from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.repositories import declaration_repository
from app.schemas.dashboard import DashboardOut, DashboardRow
from app.services import declaration_risk

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get("/monthly", response_model=DashboardOut)
def monthly_dashboard(
    tax_period_id: uuid.UUID | None = None,
    client_id: uuid.UUID | None = None,
    workflow_status: str | None = None,
    filing_status: str | None = None,
    assigned_user_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DashboardOut:
    """Tablero mensual basico de 3.1 (workflow/filing/riesgo unicamente --
    honorarios/documentos/impuestos llegan en 3.2). Riesgo calculado en la
    misma consulta via `risk_status_sql_expression`, nunca N+1."""
    items, total = declaration_repository.list_for_dashboard(
        db,
        current_user.tenant_id,
        tax_period_id=tax_period_id,
        client_id=client_id,
        workflow_status=workflow_status,
        filing_status=filing_status,
        assigned_user_id=assigned_user_id,
        limit=limit,
        offset=offset,
    )
    today = clock.utcnow().date()
    rows = []
    for declaration in items:
        risk = declaration_risk.compute_risk_status(
            declaration.current_effective_due_date,
            declaration.workflow_status,
            declaration.filing_status,
            today,
        )
        row = DashboardRow.model_validate(declaration).model_copy(
            update={"computed_risk_status": risk}
        )
        rows.append(row)

    return DashboardOut(items=rows, total=total, limit=limit, offset=offset)
