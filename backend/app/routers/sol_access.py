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
from app.core import clock, sunat_destinations
from app.core.dependencies import get_current_session, verify_csrf
from app.core.permissions import Permission, has_permission, require_permission
from app.core.sol_vault_registry import vault_unlock_registry
from app.db.session import get_db
from app.broker.protocol import DIAGNOSTIC_CODES
from app.models.audit_log import AuditAction, AuditLog
from app.models.session import Session as AppSession
from app.models.sol_agent_access import SESSION_TERMINAL_STATUSES, SolAccessSession
from app.models.tax_period import TaxPeriod
from app.models.user import User
from app.repositories import client_repository
from app.repositories import sol_agent_access_repository as repo
from app.repositories import sol_vault_repository as vault_repo
from app.schemas.sol_access import (
    CreateSolAccessSessionRequest,
    AcceptanceGrantRequest,
    AcceptanceListOut,
    AcceptanceOut,
    AcceptanceResultRequest,
    SolAccessSessionOut,
    SolAgentOut,
    SolAgentsOut,
    SolCredentialDestinationsOut,
    SolCredentialPermissionsOut,
    SolDestinationInfo,
    SolDestinationPermissionOut,
    SolDestinationsOut,
)
from app.services import sol_access_session_service as session_service
from app.services import sol_agent_service as agent_service
from app.services import sol_destination_acceptance_service as acceptance_service
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
        credential_id=row.credential_id,
        acceptance_test=row.acceptance_grant_id is not None,
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


# Los destinos habilitados salen SIEMPRE del catalogo unico `app.core.sunat_destinations` (solo LIVE_VALIDADOS). Los conocidos pero no
# validados (SIRE, Declaraciones y Pagos, Declara Facil, Tramites y Consultas) se informan con su estado, sin ofrecerse como funcionales.


@router.get("/destinations", response_model=SolDestinationsOut)
def list_destinations(
    response: Response,
    current_user: User = Depends(require_permission(Permission.SOL_SESSION_READ)),
) -> SolDestinationsOut:
    _no_store(response)
    return SolDestinationsOut(
        destinations=sunat_destinations.enabled_ids(),
        catalog=[
            SolDestinationInfo(
                id=d.id, label=d.label, group=d.group, validation=d.validation, enabled=d.enabled and (d.alias_of is None or sunat_destinations.is_enabled(d.id)),
                evidence=d.evidence, alias_of=d.alias_of, contained_in=d.contained_in, open_only=d.open_only, offered=d.offered,
                acceptance_eligible=acceptance_service.is_eligible(d.id), requires_profile_proof=d.requires_profile_proof,
            )
            for d in sunat_destinations.CATALOG
        ],
    )


@router.get("/clients/{client_id}/credential-destinations", response_model=SolCredentialDestinationsOut)
def credential_destinations(
    client_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_SESSION_READ)),
) -> SolCredentialDestinationsOut:
    """Por perfil de credencial y destino ofrecido: reconocido / habilitado / permitido (OBSERVADO en sesiones reales) / completado. Nunca se infiere
    el permiso del nombre del perfil ni se extiende lo observado con un perfil a otro."""
    _no_store(response)
    if client_repository.get_by_id(db, current_user.tenant_id, client_id) is None:
        raise _error(status.HTTP_404_NOT_FOUND, "CLIENT_NOT_FOUND")
    if not has_permission(current_user, Permission.SOL_CREDENTIAL_READ):
        raise _error(status.HTTP_403_FORBIDDEN, "PERMISSION_DENIED")
    profiles = vault_repo.list_active_credentials_for_client(db, tenant_id=current_user.tenant_id, client_id=client_id)
    sessions = list(db.execute(
        select(SolAccessSession).where(SolAccessSession.tenant_id == current_user.tenant_id, SolAccessSession.client_id == client_id)
        .order_by(SolAccessSession.created_at.desc())
    ).scalars())
    reached_status = {"HUMAN_CONTROL", "SUCCEEDED"}
    proven_set = acceptance_service.proven_destinations(db, tenant_id=current_user.tenant_id, client_id=client_id)
    out = []
    for credential in profiles:
        rows = []
        for policy in sunat_destinations.CATALOG:
            if not policy.offered:
                continue
            relevant = [
                s for s in sessions
                if s.credential_id == credential.id and (s.destination == policy.id or (policy.id == "MENU_SOL" and s.destination is None))
            ]
            conclusive = None
            for s in relevant:  # del mas reciente al mas antiguo: el primero concluyente manda
                if s.status in reached_status and s.operation_result in {"DESTINATION_REACHED", "LANDING_REACHED"}:
                    conclusive = (s, "OBSERVED_ALLOWED")
                    break
                if s.status == "FAILED" and _diagnostic_code(db, s) == "DESTINATION_ENTRY_NOT_FOUND":
                    conclusive = (s, "OBSERVED_NOT_IN_MENU")
                    break
            is_enabled = policy.enabled and (policy.alias_of is None or sunat_destinations.is_enabled(policy.id))
            proven = (credential.id, policy.id) in proven_set
            rows.append(SolDestinationPermissionOut(
                destination=policy.id, recognized=policy.evidence, enabled=is_enabled,
                permitted=conclusive[1] if conclusive else "UNKNOWN", completed=bool(conclusive and conclusive[1] == "OBSERVED_ALLOWED"),
                last_session_id=conclusive[0].id if conclusive else None,
                proven=proven, available=is_enabled and (not policy.requires_profile_proof or proven),
            ))
        out.append(SolCredentialPermissionsOut(credential_id=credential.id, profile_name=credential.profile_name,
                                               credential_kind=credential.credential_kind, status=credential.status, destinations=rows))
    return SolCredentialDestinationsOut(credentials=out)


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
    # Un alias se resuelve al destino funcional (Tramites y Consultas == Menu SOL): jamas dos sesiones ni dos autenticaciones para lo mismo.
    destination = sunat_destinations.resolve(payload.destination)
    # Destino sin forma de abrirse (desconocido, solo-apertura/manual como Generacion, historico SIRE, contenido como Declara Facil): se rechaza ANTES de tocar
    # broker, Boveda, agente o credencial. Los destinos con estructura validada aun pasan por la compuerta POR PERFIL mas abajo.
    if destination is not None:
        policy = sunat_destinations.policy_of(destination)
        if policy is None or (not policy.enabled and not acceptance_service.is_eligible(destination)):
            raise _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "SOL_DESTINATION_DISABLED")
    # Extraer la Ficha propone cambios al cliente: exige ademas poder editarlo (composicion de permisos existentes).
    if payload.extract_ficha and (destination != "FICHA_RUC" or not has_permission(current_user, Permission.CLIENT_WRITE)):
        raise _error(
            status.HTTP_403_FORBIDDEN if destination == "FICHA_RUC" else status.HTTP_422_UNPROCESSABLE_ENTITY,
            "PERMISSION_DENIED" if destination == "FICHA_RUC" else "SOL_EXTRACTION_NOT_ALLOWED",
        )
    broker = _broker(request)
    if broker is None:
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "SOL_BROKER_UNAVAILABLE")
    tenant_id = current_user.tenant_id

    if client_repository.get_by_id(db, tenant_id, payload.client_id) is None:
        raise _error(status.HTTP_404_NOT_FOUND, "CLIENT_NOT_FOUND")
    if payload.origin_tax_period_id is not None and db.execute(
        select(TaxPeriod.id).where(TaxPeriod.tenant_id == tenant_id, TaxPeriod.id == payload.origin_tax_period_id)
    ).first() is None:
        raise _error(status.HTTP_404_NOT_FOUND, "TAX_PERIOD_NOT_FOUND")  # el periodo de contexto es de OTRO tenant o no existe
    profiles = vault_repo.list_active_credentials_for_client(db, tenant_id=tenant_id, client_id=payload.client_id)
    if payload.credential_id is not None:
        credential = next((c for c in profiles if c.id == payload.credential_id), None)
        if credential is None:
            raise _error(status.HTTP_404_NOT_FOUND, "SOL_CREDENTIAL_NOT_FOUND")  # de otro cliente/tenant, inexistente o deshabilitada
    elif len(profiles) > 1:
        raise _error(status.HTTP_409_CONFLICT, "SOL_CREDENTIAL_SELECTION_REQUIRED")  # varios perfiles: la eleccion es del usuario
    else:
        credential = profiles[0] if profiles else None
    # Una credencial no utilizable (INVALID, bloqueada, vencida...) NO se sustituye por otra: ni mas privilegiada ni alternativa.
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
                destination=destination,
                extract_ficha=payload.extract_ficha,
                origin_tax_period_id=payload.origin_tax_period_id,
            )
        except session_service.IdempotencyKeyConflictError:
            raise _error(status.HTTP_409_CONFLICT, "SOL_SESSION_IDEMPOTENCY_CONFLICT") from None
        response.status_code = status.HTTP_200_OK
        return _session_out(_refresh_expiry(db, row))

    # Compuerta por PERFIL (la autorizacion de una credencial no se extrapola a otros contribuyentes, perfiles ni tenants): el perfil elegido debe haber superado
    # su propia prueba real de este destino o contar con una autorizacion explicita y vigente del propietario (un solo uso). Nunca se prueba otro perfil.
    if destination is not None:
        gate = acceptance_service.access_gate(db, tenant_id=tenant_id, client_id=payload.client_id, credential_id=credential.id, destination=destination)
        if gate == acceptance_service.GATE_DISABLED:
            raise _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "SOL_DESTINATION_DISABLED")
        if gate == acceptance_service.GATE_NEEDS_GRANT and not (
            has_permission(current_user, Permission.SOL_ACCEPTANCE_MANAGE)
            and acceptance_service.has_valid_grant(
                db, tenant_id=tenant_id, user_id=current_user.id, client_id=payload.client_id, credential_id=credential.id, destination=destination,
            )
        ):
            raise _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "SOL_PROFILE_NOT_PROVEN")

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
            destination=destination,
            extract_ficha=payload.extract_ficha,
            origin_tax_period_id=payload.origin_tax_period_id,
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


# --- M3: aceptacion real controlada (SOLO propietario/administrador) -----------------------------------------------------------------------------


def _acceptance_out(db: Session, row) -> AcceptanceOut:
    phase, session = acceptance_service.phase_of(db, row)
    return AcceptanceOut(
        id=row.id, client_id=row.client_id, credential_id=row.credential_id, destination=row.destination, status=row.status, created_at=row.created_at,
        expires_at=row.expires_at, consumed_at=row.consumed_at, session_id=row.session_id, result=row.result, result_note=row.result_note, phase=phase,
        session_status=session.status if session is not None else None, session_operation_result=session.operation_result if session is not None else None,
        diagnostic_code=acceptance_service.diagnostic_of(db, session),
        result_source=None if row.result is None else ("SYSTEM" if (row.result_note or "").startswith("SYSTEM:") else "OWNER"),
    )


_ACCEPTANCE_HTTP = {
    "CLIENT_NOT_FOUND": status.HTTP_404_NOT_FOUND,
    "ACCEPTANCE_NOT_FOUND": status.HTTP_404_NOT_FOUND,
    "SOL_CREDENTIAL_NOT_CONFIGURED": status.HTTP_409_CONFLICT,
    "ACCEPTANCE_ALREADY_AUTHORIZED_BY_ANOTHER_USER": status.HTTP_409_CONFLICT,
    "ACCEPTANCE_NOT_REVOCABLE": status.HTTP_409_CONFLICT,
    "ACCEPTANCE_NOT_USED": status.HTTP_409_CONFLICT,
    "ACCEPTANCE_RESULT_ALREADY_RECORDED": status.HTTP_409_CONFLICT,
    "ACCEPTANCE_RESULT_NOT_BY_AUTHORIZER": status.HTTP_403_FORBIDDEN,
    "ACCEPTANCE_SESSION_STILL_RUNNING": status.HTTP_409_CONFLICT,
    "ACCEPTANCE_ALREADY_PROVEN": status.HTTP_409_CONFLICT,
}


def _acceptance_error(exc: acceptance_service.AcceptanceError) -> HTTPException:
    return _error(_ACCEPTANCE_HTTP.get(exc.code, status.HTTP_422_UNPROCESSABLE_ENTITY), exc.code)


@router.get("/clients/{client_id}/acceptances", response_model=AcceptanceListOut)
def list_acceptances(
    client_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_ACCEPTANCE_MANAGE)),
) -> AcceptanceListOut:
    _no_store(response)
    if client_repository.get_by_id(db, current_user.tenant_id, client_id) is None:
        raise _error(status.HTTP_404_NOT_FOUND, "CLIENT_NOT_FOUND")
    rows = acceptance_service.list_for_client(db, tenant_id=current_user.tenant_id, client_id=client_id)
    db.commit()  # materializa vencimientos y fallos de sesion
    return AcceptanceListOut(acceptances=[_acceptance_out(db, r) for r in rows])


@router.post("/acceptances", response_model=AcceptanceOut, status_code=status.HTTP_201_CREATED)
def grant_acceptance(
    payload: AcceptanceGrantRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_ACCEPTANCE_MANAGE)),
    _csrf: None = Depends(verify_csrf),
) -> AcceptanceOut:
    """Autoriza UNA prueba real de UN destino para UN cliente y UN perfil (un solo uso, vencimiento corto). No habilita el destino para nadie mas."""
    _no_store(response)
    try:
        row = acceptance_service.grant(
            db, tenant_id=current_user.tenant_id, user_id=current_user.id, client_id=payload.client_id,
            credential_id=payload.credential_id, destination=payload.destination,
        )
        db.commit()
    except acceptance_service.AcceptanceError as exc:
        db.rollback()
        raise _acceptance_error(exc) from None
    return _acceptance_out(db, row)


@router.post("/acceptances/{acceptance_id}/revoke", response_model=AcceptanceOut)
def revoke_acceptance(
    acceptance_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_ACCEPTANCE_MANAGE)),
    _csrf: None = Depends(verify_csrf),
) -> AcceptanceOut:
    _no_store(response)
    try:
        row = acceptance_service.revoke(db, tenant_id=current_user.tenant_id, user_id=current_user.id, acceptance_id=acceptance_id)
        db.commit()
    except acceptance_service.AcceptanceError as exc:
        db.rollback()
        raise _acceptance_error(exc) from None
    return _acceptance_out(db, row)


@router.post("/acceptances/{acceptance_id}/result", response_model=AcceptanceOut)
def record_acceptance_result(
    acceptance_id: uuid.UUID,
    payload: AcceptanceResultRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SOL_ACCEPTANCE_MANAGE)),
    _csrf: None = Depends(verify_csrf),
) -> AcceptanceOut:
    """El propietario confirma el resultado tras ver la pantalla oficial. PASS exige que el agente haya llegado de verdad al destino."""
    _no_store(response)
    try:
        row = acceptance_service.record_result(
            db, tenant_id=current_user.tenant_id, user_id=current_user.id, acceptance_id=acceptance_id, result=payload.result, note=payload.note,
        )
        db.commit()
    except acceptance_service.AcceptanceError as exc:
        db.rollback()
        raise _acceptance_error(exc) from None
    return _acceptance_out(db, row)
