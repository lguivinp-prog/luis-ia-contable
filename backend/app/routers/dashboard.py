import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core import clock
from app.core.permissions import Permission, has_permission, require_permission
from app.db.session import get_db
from app.models.user import User
from app.repositories import declaration_repository
from app.schemas.dashboard import DashboardOut, DashboardRow, TaskDashboardSummary
from app.services import dashboard_service, declaration_risk, task_agenda_service

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


@router.get("/monthly", response_model=DashboardOut)
def monthly_dashboard(
    tax_period_id: uuid.UUID | None = None,
    client_id: uuid.UUID | None = None,
    workflow_status: str | None = None,
    filing_status: str | None = None,
    tax_payment_status: str | None = None,
    document_status: str | None = None,
    assigned_user_id: uuid.UUID | None = None,
    risk_status: str | None = None,
    closed_with_pending: bool | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.DASHBOARD_READ)),
) -> DashboardOut:
    """Tablero mensual completo (ver "Tablero mensual completo -- sin
    duplicacion de filas" del plan): sigue devolviendo exactamente una fila
    por declaracion. `document_status`/`tax_payment_status` ya son columnas
    directas de `declarations` (sin join); el agregado de honorarios se
    calcula aparte por `(client_id, tax_period_id)` (nunca 1:1 con una
    declaracion) y se repite -- nunca se multiplica -- en cada declaracion
    del mismo cliente/periodo."""
    items, total = declaration_repository.list_for_dashboard(
        db,
        current_user.tenant_id,
        tax_period_id=tax_period_id,
        client_id=client_id,
        workflow_status=workflow_status,
        filing_status=filing_status,
        tax_payment_status=tax_payment_status,
        document_status=document_status,
        assigned_user_id=assigned_user_id,
        risk_status=risk_status,
        closed_with_pending=closed_with_pending,
        limit=limit,
        offset=offset,
    )
    today = clock.utcnow().date()

    pairs = [(declaration.client_id, declaration.tax_period_id) for declaration in items]
    fee_aggregates = dashboard_service.build_fee_aggregates(db, current_user.tenant_id, pairs, today)
    tax_aggregates = dashboard_service.build_tax_aggregates(
        db, current_user.tenant_id, [declaration.id for declaration in items]
    )

    rows = []
    for declaration in items:
        risk = declaration_risk.compute_risk_status(
            declaration.current_effective_due_date,
            declaration.workflow_status,
            declaration.filing_status,
            today,
            document_status=declaration.document_status,
            tax_payment_status=declaration.tax_payment_status,
            closed_with_pending=declaration.closed_with_pending,
        )
        fee_aggregate = dashboard_service.get_fee_aggregate(
            fee_aggregates, declaration.client_id, declaration.tax_period_id
        )
        tax_aggregate = dashboard_service.get_tax_aggregate(tax_aggregates, declaration.id)
        row = DashboardRow.model_validate(declaration).model_copy(
            update={
                "computed_risk_status": risk,
                "tax_gross_amount": tax_aggregate.gross_tax_amount,
                "tax_total_payable": tax_aggregate.total_payable,
                "tax_total_paid": tax_aggregate.total_paid,
                "missing_documents_count": tax_aggregate.missing_documents_count,
                "fee_total_charges": fee_aggregate.total_charges,
                "fee_total_amount": fee_aggregate.total_amount,
                "fee_total_paid": fee_aggregate.total_paid,
                "fee_balance": fee_aggregate.balance,
                "fee_pending_charges_count": fee_aggregate.pending_charges_count,
                "fee_worst_collection_risk": fee_aggregate.worst_collection_risk,
            }
        )
        rows.append(row)

    task_summary = None
    if has_permission(current_user, Permission.TASK_READ):
        task_summary = TaskDashboardSummary(
            **task_agenda_service.compute_task_summary_for_dashboard(
                db, current_user.tenant_id, current_user.id, clock.utcnow()
            )
        )

    return DashboardOut(items=rows, total=total, limit=limit, offset=offset, task_summary=task_summary)
