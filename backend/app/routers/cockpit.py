import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core import clock
from app.core.dependencies import verify_csrf
from app.core.permissions import Permission, has_permission, require_permission
from app.core.timezone import today_lima
from app.db.session import get_db
from app.models.user import User
from app.repositories import sol_agent_access_repository as sol_repo
from app.repositories import sol_vault_repository as vault_repo
from app.schemas.cockpit import (
    CockpitOut,
    OperationsHistoryEntryOut,
    OperationsOut,
    OperationsUpdateIn,
    SolAccessCapabilityOut,
    WorkspaceOut,
)
from app.services import cockpit_logic, cockpit_operations_service, cockpit_service

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


def _operations_out(db: Session, tenant_id: uuid.UUID, state) -> OperationsOut:
    name = None
    if state.updated_by is not None:
        user = db.get(User, state.updated_by)
        name = user.full_name if user is not None and user.tenant_id == tenant_id else None
    return OperationsOut(
        buzon_status=state.buzon_status,
        accounting_status=state.accounting_status,
        client_communication_status=state.client_communication_status,
        notes=state.notes,
        row_version=state.row_version,
        updated_at=state.updated_at,
        updated_by_name=name,
    )


def _not_found(code: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"code": code})


@router.get("/clients/{client_id}/periods/{period_id}", response_model=WorkspaceOut)
def client_period_workspace(
    client_id: uuid.UUID,
    period_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.DASHBOARD_READ)),
) -> WorkspaceOut:
    """Agregado del workspace cliente/periodo (solo lectura; no crea filas ni declaraciones; sin secretos)."""
    now = clock.utcnow()
    try:
        workspace = cockpit_service.build_workspace(
            db, current_user.tenant_id, client_id, period_id, today=today_lima(now), now=now
        )
        state = cockpit_operations_service.get_operations(db, current_user.tenant_id, client_id, period_id)
    except cockpit_service.CockpitPeriodNotFoundError:
        raise _not_found("TAX_PERIOD_NOT_FOUND") from None
    except (cockpit_service.CockpitClientNotFoundError, cockpit_operations_service.OperationsScopeNotFoundError):
        raise _not_found("CLIENT_NOT_FOUND") from None
    workspace.operations = _operations_out(db, current_user.tenant_id, state)
    workspace.can_update_operations = has_permission(current_user, Permission.COCKPIT_UPDATE)
    if has_permission(current_user, Permission.SOL_CREDENTIAL_READ) and has_permission(current_user, Permission.SOL_SESSION_READ):
        profiles = vault_repo.list_active_credentials_for_client(db, tenant_id=current_user.tenant_id, client_id=client_id)
        credential = profiles[0] if profiles else None  # estado del perfil mas antiguo; el detalle por perfil vive en el Centro SUNAT
        latest = sol_repo.get_latest_session_for_client(db, tenant_id=current_user.tenant_id, client_id=client_id)
        workspace.sol_access = SolAccessCapabilityOut(
            credential_configured=credential is not None,
            credential_status=credential.status if credential is not None else None,
            latest_session_status=latest.status if latest is not None else None,
        )
    return workspace


@router.put("/clients/{client_id}/periods/{period_id}/operations", response_model=OperationsOut)
def update_client_period_operations(
    client_id: uuid.UUID,
    period_id: uuid.UUID,
    payload: OperationsUpdateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.COCKPIT_UPDATE)),
    _csrf: None = Depends(verify_csrf),
) -> OperationsOut:
    """Actualiza estados operativos MANUALES (Buzon, Contabilidad, Comunicacion, notas) con concurrencia optimista."""
    changes = {field: getattr(payload, field) for field in payload.model_fields_set if field != "expected_row_version"}
    try:
        state = cockpit_operations_service.update_operations(
            db, current_user.tenant_id, current_user.id, client_id, period_id,
            changes=changes, expected_row_version=payload.expected_row_version,
        )
        db.commit()
    except cockpit_operations_service.OperationsScopeNotFoundError:
        db.rollback()
        raise _not_found("CLIENT_OR_PERIOD_NOT_FOUND") from None
    except cockpit_operations_service.OperationsVersionConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CLIENT_PERIOD_OPERATIONS_VERSION_CONFLICT", "current_row_version": exc.current_row_version},
        ) from None
    return _operations_out(db, current_user.tenant_id, state)


@router.get("/clients/{client_id}/periods/{period_id}/history", response_model=list[OperationsHistoryEntryOut])
def client_period_operations_history(
    client_id: uuid.UUID,
    period_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.DASHBOARD_READ)),
) -> list[OperationsHistoryEntryOut]:
    try:
        entries = cockpit_operations_service.list_history(db, current_user.tenant_id, client_id, period_id)
    except cockpit_operations_service.OperationsScopeNotFoundError:
        raise _not_found("CLIENT_OR_PERIOD_NOT_FOUND") from None
    return [
        OperationsHistoryEntryOut(
            id=e.id, field_name=e.field_name, from_value=e.from_value, to_value=e.to_value,
            row_version_after=e.row_version_after, actor_user_id=e.actor_user_id, actor_name=name, created_at=e.created_at,
        )
        for e, name in entries
    ]
