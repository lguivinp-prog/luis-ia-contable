"""Fase 5, Bloque 5.2, Domain F: API de usuario del acceso SOL supervisado (control plane humano).

NO automatiza nada de SUNAT ni expone credenciales: crea/lee/cancela sesiones, lista agentes y delega TODO el estado en
los servicios autoritativos de Domain C. El despacho del job y el contexto humano de autorizacion viven en el proceso
del broker (`request.app.state.sol_broker`): la autorizacion de entrega NO se crea aqui, sino JUST-IN-TIME cuando el
Agente senala `PREAUTH_READY` (ver `BrokerServer._handle_preauth_ready`). Ninguna ruta devuelve secretos."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.broker.dispatch import dispatch_job_to_agent
from app.broker.session_authorization import USABLE_CREDENTIAL_STATUSES
from app.core import clock
from app.core.dependencies import get_current_session, verify_csrf
from app.core.permissions import Permission, has_permission, require_permission
from app.core.sol_vault_registry import vault_unlock_registry
from app.db.session import get_db
from app.broker.protocol import DIAGNOSTIC_CODES
from app.models.audit_log import AuditAction, AuditLog
from app.models.session import Session as AppSession
from app.models.sol_agent_access import SESSION_TERMINAL_STATUSES, SolAccessSession
from app.models.user import User
from app.repositories import client_repository
from app.repositories import sol_agent_access_repository as repo
from app.repositories import sol_vault_repository as vault_repo
from app.schemas.sol_access import (
    CreateSolAccessSessionRequest,
    SolAccessSessionOut,
    SolAgentOut,
    SolAgentsOut,
    SolDestinationsOut,
)
from app.services import sol_access_session_service as session_service
from app.services import sol_agent_service as agent_service
from app.services import sol_agent_job_service as job_service
from app.services import sol_human_control_reconciler as hc_reconciler

router = APIRouter(prefix="/api/v1/sol-access", tags=["sol-access"])

# NEEDS_VALIDATION es elegible para el primer acceso supervisado (ADR-0068: asi se comprueba); ACTIVE normal. Los
# estados conocidos como malos (INVALID, LOCKED_BY_SUNAT, PASSWORD_EXPIRED, DISABLED) NO se intentan.
_USABLE_CREDENTIAL_STATUSES = USABLE_CREDENTIAL_STATUSES


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _error(http_status: int, code: str) -> HTTPException:
    return HTTPException(status_code=http_status, detail={"code": code})


def _broker(request: Request):
    return getattr(request.app.state, "sol_broker", None)


def _diagnostic_code(db: Session, row: SolAccessSession) -> str | None:
    """Codigo de diagnostico estructural guardado en la auditoria del cierre FAILED (sin migracion; lista cerrada)."""
    action = {"FAILED": AuditAction.SESSION_FAILED, "WAITING_FOR_USER": AuditAction.WAITING_FOR_HUMAN}.get(row.status)
    if action is None:
        return None
    entry = db.execute(
        select(AuditLog)
        .where(
            AuditLog.tenant_id == row.tenant_id,
            AuditLog.entity_type == "sol_access_session",
            AuditLog.entity_id == row.id,
            AuditLog.action == action,
        )
        .order_by(AuditLog.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    code = (entry.log_metadata or {}).get("diagnostic_code") if entry is not None else None
    return code if code in DIAGNOSTIC_CODES else None


def _session_out(row: SolAccessSession, diagnostic_code: str | None = None) -> SolAccessSessionOut:
    return SolAccessSessionOut(
        diagnostic_code=diagnostic_code,
        id=row.id,
        client_id=row.client_id,
        scope=row.scope,
        status=row.status,
        terminal_reason_code=row.terminal_reason_code,
        operation_result=row.operation_result,
        created_at=row.created_at,
        started_at=row.started_at,
        waiting_for_user_at=row.waiting_for_user_at,
        completed_at=row.completed_at,
        expires_at=row.expires_at,
        version=row.version,
        is_terminal=row.status in SESSION_TERMINAL_STATUSES,
        destination=row.destination,
        extract_ficha=bool(row.extract_ficha),
        human_control_at=row.human_control_at,
    )


def _refresh_expiry(db: Session, row: SolAccessSession) -> SolAccessSession:
    """No hay scheduler: la expiracion se materializa de forma perezosa por la autoridad existente
    (`expire_session`, idempotente, tiempo autoritativo de PostgreSQL) al leer una sesion vencida."""
    if row.status in SESSION_TERMINAL_STATUSES:
        return row
    if row.expires_at > clock.db_now(db):
        # M2: una automatizacion `IN_PROGRESS` sin ninguna transicion (ejecutor que murio sin reportar) se cierra al consultarla
        if row.status == "IN_PROGRESS" and hc_reconciler.reconcile_stalled_session(db, tenant_id=row.tenant_id, session_id=row.id):
            db.commit()
            return repo.get_session(db, tenant_id=row.tenant_id, session_id=row.id) or row
        return row
    try:
        row = session_service.expire_session(db, tenant_id=row.tenant_id, session_id=row.id)
        db.commit()
    except (session_service.SessionNotExpiredError, session_service.SessionTerminalError):
        db.rollback()
        row = repo.get_session(db, tenant_id=row.tenant_id, session_id=row.id) or row
    return row


def _reconcile_absent_agents(db: Session, tenant_id: uuid.UUID, agents: list) -> None:
    """M2: libera (lease acotado) la reserva de un agente que perdio su sesion en control humano porque dejo de emitir
    heartbeat; un agente vivo que la sostiene nunca se toca (lo decide el heartbeat, ver el reconciliador)."""
    released = False
    for agent in agents:
        if agent.status == "ACTIVE":
            released |= hc_reconciler.reconcile_absent_agent(db, tenant_id=tenant_id, agent=agent)
    if released:
        db.commit()


def _busy_agent_ids(db: Session, tenant_id: uuid.UUID, agent_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    return {a for a in agent_ids if repo.get_active_job_for_agent(db, tenant_id=tenant_id, agent_id=a) is not None}


@router.get("/agents", response_model=SolAgentsOut)
def list_agents(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_AGENT_READ)),
) -> SolAgentsOut:
    _no_store(response)
    broker = _broker(request)
    agents = repo.list_agents(db, tenant_id=current_user.tenant_id)
    _reconcile_absent_agents(db, current_user.tenant_id, agents)
    busy = _busy_agent_ids(db, current_user.tenant_id, [a.id for a in agents])
    now = clock.db_now(db)
    return SolAgentsOut(
        broker_enabled=broker is not None,
        agents=[
            SolAgentOut(
                id=a.id,
                display_name=a.display_name,
                status=a.status,
                paired_at=a.paired_at,
                last_seen_at=a.last_seen_at,
                available=(
                    broker is not None
                    and a.status == "ACTIVE"
                    and a.id not in busy
                    and broker.connections.is_connected(agent_id=a.id)
                    and agent_service.is_online(a, now=now)
                ),
            )
            for a in agents
        ],
    )


# Destinos habilitados por el SERVIDOR (LIVE-VALIDADOS en el reconocimiento M2). SIRE, Declaraciones y Pagos,
# Declara Facil y Tramites y Consultas NO figuran: la cuenta de control no los expone y no se muestran como funcionales.
ENABLED_DESTINATIONS = ["MENU_SOL", "FICHA_RUC"]


@router.get("/destinations", response_model=SolDestinationsOut)
def list_destinations(
    response: Response,
    current_user: User = Depends(require_permission(Permission.SOL_SESSION_READ)),
) -> SolDestinationsOut:
    _no_store(response)
    return SolDestinationsOut(destinations=ENABLED_DESTINATIONS)


@router.get("/clients/{client_id}/session", response_model=SolAccessSessionOut | None)
def get_client_session(
    client_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_SESSION_READ)),
) -> SolAccessSessionOut | None:
    """Sesion mas reciente del cliente (la activa si existe): permite retomar la supervision tras refrescar."""
    _no_store(response)
    if client_repository.get_by_id(db, current_user.tenant_id, client_id) is None:
        raise _error(status.HTTP_404_NOT_FOUND, "CLIENT_NOT_FOUND")
    row = repo.get_latest_session_for_client(db, tenant_id=current_user.tenant_id, client_id=client_id)
    if row is None:
        return None
    row = _refresh_expiry(db, row)
    return _session_out(row, _diagnostic_code(db, row))


@router.get("/sessions/{session_id}", response_model=SolAccessSessionOut)
def get_session(
    session_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_SESSION_READ)),
) -> SolAccessSessionOut:
    _no_store(response)
    row = repo.get_session(db, tenant_id=current_user.tenant_id, session_id=session_id)
    if row is None:
        raise _error(status.HTTP_404_NOT_FOUND, "SOL_SESSION_NOT_FOUND")
    row = _refresh_expiry(db, row)
    return _session_out(row, _diagnostic_code(db, row))


@router.post("/sessions", response_model=SolAccessSessionOut, status_code=status.HTTP_201_CREATED)
def create_session(
    payload: CreateSolAccessSessionRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_SESSION_CREATE)),
    app_session: AppSession = Depends(get_current_session),
    _csrf: None = Depends(verify_csrf),
) -> SolAccessSessionOut:
    """Inicia UNA sesion supervisada (idempotente por `idempotency_key`). Nunca se inicia sola: solo por esta accion
    explicita. No autoriza la entrega de credencial: eso ocurre just-in-time tras `PREAUTH_READY`."""
    _no_store(response)
    if not has_permission(current_user, Permission.SOL_CREDENTIAL_READ):
        raise _error(status.HTTP_403_FORBIDDEN, "PERMISSION_DENIED")
    # Extraer la Ficha propone cambios al cliente: exige ademas poder editarlo (composicion de permisos existentes).
    if payload.extract_ficha and (payload.destination != "FICHA_RUC" or not has_permission(current_user, Permission.CLIENT_WRITE)):
        raise _error(
            status.HTTP_403_FORBIDDEN if payload.destination == "FICHA_RUC" else status.HTTP_422_UNPROCESSABLE_ENTITY,
            "PERMISSION_DENIED" if payload.destination == "FICHA_RUC" else "SOL_EXTRACTION_NOT_ALLOWED",
        )
    broker = _broker(request)
    if broker is None:
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "SOL_BROKER_UNAVAILABLE")
    tenant_id = current_user.tenant_id

    if client_repository.get_by_id(db, tenant_id, payload.client_id) is None:
        raise _error(status.HTTP_404_NOT_FOUND, "CLIENT_NOT_FOUND")
    credential = vault_repo.get_active_credential_for_client(db, tenant_id=tenant_id, client_id=payload.client_id)
    if credential is None or credential.status not in _USABLE_CREDENTIAL_STATUSES:
        raise _error(status.HTTP_409_CONFLICT, "SOL_CREDENTIAL_NOT_CONFIGURED")

    # Reintento idempotente: misma clave => la misma sesion logica; nunca se despacha un segundo job.
    existing = repo.get_session_by_idempotency_key(db, tenant_id=tenant_id, idempotency_key=payload.idempotency_key)
    if existing is not None:
        try:
            row = session_service.create_session(
                db,
                tenant_id=tenant_id,
                client_id=payload.client_id,
                credential_id=credential.id,
                requested_by_user_id=current_user.id,
                idempotency_key=payload.idempotency_key,
                destination=payload.destination,
                extract_ficha=payload.extract_ficha,
            )
        except session_service.IdempotencyKeyConflictError:
            raise _error(status.HTTP_409_CONFLICT, "SOL_SESSION_IDEMPOTENCY_CONFLICT") from None
        response.status_code = status.HTTP_200_OK
        return _session_out(_refresh_expiry(db, row))

    # Preflight autoritativo en servidor (la UI muestra lo mismo, pero el backend es la frontera de seguridad).
    if (
        vault_unlock_registry.get_handle(
            tenant_id=tenant_id,
            vault_id=credential.vault_id,
            user_id=current_user.id,
            app_session_id=app_session.id,
        )
        is None
    ):
        raise _error(status.HTTP_409_CONFLICT, "SOL_VAULT_LOCKED")

    agents = [a for a in repo.list_agents(db, tenant_id=tenant_id) if a.status == "ACTIVE"]
    _reconcile_absent_agents(db, tenant_id, agents)
    busy = _busy_agent_ids(db, tenant_id, [a.id for a in agents])
    now = clock.db_now(db)
    candidates = [
        a
        for a in agents
        if a.id not in busy and broker.connections.is_connected(agent_id=a.id) and agent_service.is_online(a, now=now)
    ]
    if not candidates:
        raise _error(status.HTTP_409_CONFLICT, "SOL_AGENT_UNAVAILABLE")
    agent = max(candidates, key=lambda a: a.last_seen_at or a.paired_at)

    try:
        row = session_service.create_session(
            db,
            tenant_id=tenant_id,
            client_id=payload.client_id,
            credential_id=credential.id,
            requested_by_user_id=current_user.id,
            idempotency_key=payload.idempotency_key,
            destination=payload.destination,
            extract_ficha=payload.extract_ficha,
        )
        dispatch_job_to_agent(
            db, tenant_id=tenant_id, session_id=row.id, agent_id=agent.id, pending_dispatch=broker.pending_dispatch
        )
        db.commit()
    except session_service.ActiveSessionAlreadyExistsError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, "SOL_SESSION_ALREADY_ACTIVE") from None
    except session_service.IdempotencyKeyConflictError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, "SOL_SESSION_IDEMPOTENCY_CONFLICT") from None
    except (session_service.SolAccessSessionServiceError, job_service.SolAgentJobServiceError):
        db.rollback()
        broker.pending_dispatch.discard(agent_id=agent.id)
        raise _error(status.HTTP_409_CONFLICT, "SOL_SESSION_NOT_STARTABLE") from None
    except Exception:
        db.rollback()
        broker.pending_dispatch.discard(agent_id=agent.id)
        raise

    # Contexto humano (solo ids, en memoria) que el broker usara si el Agente senala PREAUTH_READY.
    broker.session_authorizations.register(
        tenant_id=tenant_id, session_id=row.id, user_id=current_user.id, app_session_id=app_session.id
    )
    db.refresh(row)
    return _session_out(row)


@router.post("/sessions/{session_id}/cancel", response_model=SolAccessSessionOut)
def cancel_session(
    session_id: uuid.UUID,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_SESSION_CANCEL)),
    _csrf: None = Depends(verify_csrf),
) -> SolAccessSessionOut:
    """Cancelacion explicita por la autoridad existente (`cancel_session`: idempotente, cancela el job activo). No
    promete el cierre del navegador: el agente lo observa por su heartbeat (E2/E3)."""
    _no_store(response)
    try:
        row = session_service.cancel_session(
            db, tenant_id=current_user.tenant_id, session_id=session_id, actor_user_id=current_user.id
        )
        db.commit()
    except session_service.SessionNotFoundError:
        db.rollback()
        raise _error(status.HTTP_404_NOT_FOUND, "SOL_SESSION_NOT_FOUND") from None
    except session_service.SessionTerminalError:
        db.rollback()
        raise _error(status.HTTP_409_CONFLICT, "SOL_SESSION_TERMINAL") from None
    broker = _broker(request)
    if broker is not None:
        broker.session_authorizations.discard(tenant_id=current_user.tenant_id, session_id=session_id)
    return _session_out(row)
