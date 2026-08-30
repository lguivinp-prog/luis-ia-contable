"""Ciclo de vida de la sesion SOL supervisada (Fase 5, Bloque 5.2, Domain C
-- ver docs/phase5/block5_2/BLOCK5_2_STATE_MACHINE.md). Maquina de
transiciones centralizada: cualquier mutacion de `status` pasa por
`_transition_locked`, nunca se asigna `session.status` fuera de esta
funcion. Ningun metodo ejecuta `db.commit()`/`db.rollback()` (ADR-0037,
salvo la unica excepcion documentada de `create_session` ante una carrera
real de idempotencia, mismo patron que `google_oauth_service.py`)."""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock
from app.models.audit_log import AuditAction
from app.models.client import Client
from app.models.client_sol_credential import ClientSolCredential
from app.models.sol_agent_access import (
    SESSION_TERMINAL_STATUSES,
    SolAccessSession,
    SolAccessTerminalReasonCode,
)
from app.repositories import client_repository
from app.repositories import sol_agent_access_repository as repo
from app.services import audit_service

SESSION_TTL_SECONDS_DEFAULT = 30 * 60  # ver Domain A.1 seccion 6 -- valor deferred sin impacto DDL

_TERMINAL: frozenset[str] = frozenset(SESSION_TERMINAL_STATUSES)
_TRANSITIONS: dict[str, frozenset[str]] = {
    # Corregido en Domain C.1, Gate 1 (ver BLOCK5_2_STATE_MACHINE.md
    # seccion 2): cualquier estado no-terminal puede expirar, incluido
    # IN_PROGRESS -- el session_ttl debe seguir teniendo efecto durante
    # toda la sesion, no solo mientras espera a un agente/usuario.
    "CREATED": frozenset({"WAITING_FOR_AGENT", "CANCELLED", "EXPIRED"}),
    "WAITING_FOR_AGENT": frozenset({"IN_PROGRESS", "CANCELLED", "EXPIRED", "FAILED"}),
    "IN_PROGRESS": frozenset(
        {"WAITING_FOR_USER", "SUCCEEDED", "PARTIAL_FAILURE", "FAILED", "CANCELLED", "EXPIRED"}
    ),
    "WAITING_FOR_USER": frozenset({"IN_PROGRESS", "CANCELLED", "EXPIRED", "FAILED"}),
}
_TERMINAL_REASON_CODE_VALUES = frozenset(c.value for c in SolAccessTerminalReasonCode)
_WRONG_TAXPAYER_CONTEXT = SolAccessTerminalReasonCode.WRONG_TAXPAYER_CONTEXT.value
_SESSION_EXPIRED_REASON = SolAccessTerminalReasonCode.SESSION_EXPIRED.value
_CANCELLED_BY_USER_REASON = SolAccessTerminalReasonCode.CANCELLED_BY_USER.value


class SolAccessSessionServiceError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class SessionNotFoundError(SolAccessSessionServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_SESSION_NOT_FOUND")


class ClientNotFoundError(SolAccessSessionServiceError):
    def __init__(self) -> None:
        super().__init__("CLIENT_NOT_FOUND")


class CredentialNotFoundError(SolAccessSessionServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_CREDENTIAL_NOT_FOUND")


class SessionVersionConflictError(SolAccessSessionServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_SESSION_VERSION_CONFLICT")


class SessionTerminalError(SolAccessSessionServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_SESSION_TERMINAL", "La sesion ya esta en un estado terminal")


class SessionNotExpiredError(SolAccessSessionServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_SESSION_NOT_YET_EXPIRED")


class InvalidSessionTransitionError(SolAccessSessionServiceError):
    def __init__(self, from_status: str, to_status: str) -> None:
        super().__init__("SOL_SESSION_INVALID_TRANSITION", f"Transicion invalida: {from_status} -> {to_status}")


class IdempotencyKeyConflictError(SolAccessSessionServiceError):
    def __init__(self) -> None:
        super().__init__(
            "SOL_SESSION_IDEMPOTENCY_CONFLICT",
            "La clave de idempotencia ya se uso con una sesion de datos distintos",
        )


class ActiveSessionAlreadyExistsError(SolAccessSessionServiceError):
    def __init__(self) -> None:
        super().__init__(
            "SOL_SESSION_ALREADY_ACTIVE_FOR_CREDENTIAL",
            "Ya existe una sesion no-terminal para esta credencial",
        )


def get_session(db: Session, *, tenant_id: uuid.UUID, session_id: uuid.UUID) -> SolAccessSession:
    session_row = repo.get_session(db, tenant_id=tenant_id, session_id=session_id)
    if session_row is None:
        raise SessionNotFoundError()
    return session_row


def _idempotency_payload_matches(session_row: SolAccessSession, payload: dict) -> bool:
    """Canonico == (client_id, credential_id, requested_by_user_id, scope)
    -- mismo patron que `tax_payment_service._idempotency_payload_matches`:
    comparacion directa contra columnas ya persistidas, sin columna de
    fingerprint (Domain A.2 Gate 6/7, `IDEMPOTENCY_PAYLOAD_FINGERPRINT_COLUMN_REQUIRED=NO`)."""
    return (
        session_row.client_id == payload["client_id"]
        and session_row.credential_id == payload["credential_id"]
        and session_row.requested_by_user_id == payload["requested_by_user_id"]
        and session_row.scope == payload["scope"]
    )


def create_session(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    client_id: uuid.UUID,
    credential_id: uuid.UUID,
    requested_by_user_id: uuid.UUID,
    idempotency_key: uuid.UUID,
    scope: str = "AUTHENTICATE_TO_LANDING_ONLY",
    session_ttl_seconds: int = SESSION_TTL_SECONDS_DEFAULT,
) -> SolAccessSession:
    """Valida tenant/cliente/credencial antes de insertar (en vez de confiar
    unicamente en el `IntegrityError` de las FKs compuestas, que son el
    backstop, no la unica linea de defensa). Idempotente por
    `(tenant_id, idempotency_key)`: mismo payload canonico -> devuelve la
    sesion existente; payload distinto -> `IdempotencyKeyConflictError`."""
    if scope != "AUTHENTICATE_TO_LANDING_ONLY":
        raise SolAccessSessionServiceError(
            "SOL_ACCESS_SCOPE_NOT_SUPPORTED", f"scope no soportado: {scope}"
        )

    payload = {
        "client_id": client_id,
        "credential_id": credential_id,
        "requested_by_user_id": requested_by_user_id,
        "scope": scope,
    }
    existing = repo.get_session_by_idempotency_key(db, tenant_id=tenant_id, idempotency_key=idempotency_key)
    if existing is not None:
        if _idempotency_payload_matches(existing, payload):
            return existing
        raise IdempotencyKeyConflictError()

    client = client_repository.get_by_id(db, tenant_id, client_id)
    if client is None:
        raise ClientNotFoundError()
    credential = db.execute(
        select(ClientSolCredential).where(
            ClientSolCredential.id == credential_id,
            ClientSolCredential.tenant_id == tenant_id,
            ClientSolCredential.client_id == client_id,
        )
    ).scalar_one_or_none()
    if credential is None:
        raise CredentialNotFoundError()

    if repo.get_active_session_for_credential(
        db, tenant_id=tenant_id, client_id=client_id, credential_id=credential_id
    ):
        raise ActiveSessionAlreadyExistsError()

    # Domain C.1, Gate 2: tiempo autoritativo de PostgreSQL, no del proceso
    # backend -- define la ventana de TTL desde el mismo reloj que luego la
    # valida (expire_session/create_job usan db_now igual).
    now = clock.db_now(db)
    session_row = SolAccessSession(
        tenant_id=tenant_id,
        client_id=client_id,
        credential_id=credential_id,
        requested_by_user_id=requested_by_user_id,
        scope=scope,
        status="CREATED",
        idempotency_key=idempotency_key,
        created_at=now,
        expires_at=now + timedelta(seconds=session_ttl_seconds),
    )
    db.add(session_row)
    try:
        db.flush()
    except IntegrityError:
        # Carrera real de idempotencia (dos requests concurrentes con la
        # MISMA (tenant_id, idempotency_key)) -- mismo patron ya productivo
        # de google_oauth_service.py Seccion 21: el servicio SI controla el
        # rollback aqui, deliberadamente, porque es el unico punto en que
        # recupera una transaccion de un fallo real de BD en vez de dejarla
        # abortada para el llamador.
        db.rollback()
        winner = repo.get_session_by_idempotency_key(db, tenant_id=tenant_id, idempotency_key=idempotency_key)
        if winner is not None and _idempotency_payload_matches(winner, payload):
            return winner
        raise IdempotencyKeyConflictError() from None

    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=requested_by_user_id,
        action=AuditAction.SOL_SESSION_CREATED,
        entity_type="sol_access_session",
        entity_id=session_row.id,
        metadata={"client_id": str(client_id), "credential_id": str(credential_id), "scope": scope},
    )
    return session_row


def _transition_locked(
    db: Session,
    session_row: SolAccessSession,
    *,
    target_status: str,
    expected_version: int,
    terminal_reason_code: str | None,
    operation_result: str | None,
) -> SolAccessSession:
    if session_row.version != expected_version:
        raise SessionVersionConflictError()
    if session_row.status in _TERMINAL:
        raise SessionTerminalError()
    allowed = _TRANSITIONS.get(session_row.status, frozenset())
    if target_status not in allowed:
        raise InvalidSessionTransitionError(session_row.status, target_status)

    if terminal_reason_code is not None and terminal_reason_code not in _TERMINAL_REASON_CODE_VALUES:
        raise SolAccessSessionServiceError(
            "SOL_TERMINAL_REASON_CODE_INVALID", f"codigo no reconocido: {terminal_reason_code}"
        )
    if target_status == "SUCCEEDED" and terminal_reason_code is not None:
        raise SolAccessSessionServiceError(
            "SOL_TERMINAL_REASON_CODE_FORBIDDEN", "SUCCEEDED nunca lleva terminal_reason_code"
        )
    if target_status in _TERMINAL and target_status != "SUCCEEDED" and terminal_reason_code is None:
        raise SolAccessSessionServiceError(
            "SOL_TERMINAL_REASON_CODE_REQUIRED", f"{target_status} requiere terminal_reason_code"
        )
    if terminal_reason_code == _WRONG_TAXPAYER_CONTEXT and target_status != "FAILED":
        # Regla dura congelada (STATE_MACHINE seccion 6): WRONG_TAXPAYER_CONTEXT
        # -> FAILED siempre, nunca PARTIAL_FAILURE ni ningun otro terminal.
        raise SolAccessSessionServiceError(
            "SOL_WRONG_TAXPAYER_CONTEXT_MUST_FAIL",
            "WRONG_TAXPAYER_CONTEXT solo puede resultar en FAILED",
        )

    # Domain C.1, Gate 2: timestamps de transicion (incluidos los de
    # carrera terminal -- completed_at/cancelled_at) con tiempo autoritativo
    # de PostgreSQL, nunca del proceso backend.
    now = clock.db_now(db)
    session_row.status = target_status
    if terminal_reason_code is not None:
        session_row.terminal_reason_code = terminal_reason_code
    if operation_result is not None:
        session_row.operation_result = operation_result
    if target_status == "IN_PROGRESS" and session_row.started_at is None:
        session_row.started_at = now
    if target_status == "WAITING_FOR_USER":
        session_row.waiting_for_user_at = now
    if target_status in _TERMINAL:
        session_row.completed_at = now
        if target_status == "CANCELLED":
            session_row.cancelled_at = now
    session_row.version += 1
    db.flush()
    return session_row


def assign_agent(
    db: Session, *, tenant_id: uuid.UUID, session_id: uuid.UUID, expected_version: int, agent_id: uuid.UUID
) -> SolAccessSession:
    """`CREATED -> WAITING_FOR_AGENT` cuando la sesion aun no tenia agente;
    si ya estaba en un estado posterior no-terminal (reasignacion de agente
    antes de la primera entrega de credencial, ver
    `sol_agent_job_service.create_job`), solo actualiza `assigned_agent_id`
    sin forzar una transicion que no esta en la matriz congelada."""
    session_row = repo.get_session_for_update(db, tenant_id=tenant_id, session_id=session_id)
    if session_row is None:
        raise SessionNotFoundError()
    if session_row.version != expected_version:
        raise SessionVersionConflictError()

    if session_row.status == "CREATED":
        _transition_locked(
            db,
            session_row,
            target_status="WAITING_FOR_AGENT",
            expected_version=expected_version,
            terminal_reason_code=None,
            operation_result=None,
        )
        session_row.agent_assigned_at = clock.utcnow()
    elif session_row.status in _TERMINAL:
        raise SessionTerminalError()
    session_row.assigned_agent_id = agent_id
    db.flush()

    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=None,
        action=AuditAction.AGENT_ASSIGNED,
        entity_type="sol_access_session",
        entity_id=session_row.id,
        metadata={"agent_id": str(agent_id)},
    )
    return session_row


def start_progress(
    db: Session, *, tenant_id: uuid.UUID, session_id: uuid.UUID, expected_version: int
) -> SolAccessSession:
    session_row = repo.get_session_for_update(db, tenant_id=tenant_id, session_id=session_id)
    if session_row is None:
        raise SessionNotFoundError()
    is_first_entry = session_row.started_at is None
    result = _transition_locked(
        db,
        session_row,
        target_status="IN_PROGRESS",
        expected_version=expected_version,
        terminal_reason_code=None,
        operation_result=None,
    )
    if is_first_entry:
        audit_service.record(
            db,
            tenant_id=tenant_id,
            user_id=None,
            action=AuditAction.AUTH_STARTED,
            entity_type="sol_access_session",
            entity_id=result.id,
        )
    return result


def report_waiting_for_user(
    db: Session, *, tenant_id: uuid.UUID, session_id: uuid.UUID, expected_version: int
) -> SolAccessSession:
    session_row = repo.get_session_for_update(db, tenant_id=tenant_id, session_id=session_id)
    if session_row is None:
        raise SessionNotFoundError()
    result = _transition_locked(
        db,
        session_row,
        target_status="WAITING_FOR_USER",
        expected_version=expected_version,
        terminal_reason_code=None,
        operation_result=None,
    )
    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=None,
        action=AuditAction.WAITING_FOR_HUMAN,
        entity_type="sol_access_session",
        entity_id=result.id,
    )
    return result


def confirm_taxpayer(
    db: Session, *, tenant_id: uuid.UUID, session_id: uuid.UUID, expected_version: int
) -> SolAccessSession:
    """El humano confirmo "Si, es el contribuyente correcto" (STATE_MACHINE
    seccion 6) -- continua hacia `IN_PROGRESS`, nunca pide RUC/nombre."""
    session_row = repo.get_session_for_update(db, tenant_id=tenant_id, session_id=session_id)
    if session_row is None:
        raise SessionNotFoundError()
    result = _transition_locked(
        db,
        session_row,
        target_status="IN_PROGRESS",
        expected_version=expected_version,
        terminal_reason_code=None,
        operation_result=None,
    )
    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=None,
        action=AuditAction.TAXPAYER_CONFIRMED,
        entity_type="sol_access_session",
        entity_id=result.id,
    )
    return result


def report_wrong_taxpayer_context(
    db: Session, *, tenant_id: uuid.UUID, session_id: uuid.UUID, expected_version: int
) -> SolAccessSession:
    """El humano confirmo "No" (STATE_MACHINE seccion 6) -- `FAILED` con
    `WRONG_TAXPAYER_CONTEXT`, NUNCA `PARTIAL_FAILURE`, sin excepcion."""
    session_row = repo.get_session_for_update(db, tenant_id=tenant_id, session_id=session_id)
    if session_row is None:
        raise SessionNotFoundError()
    result = _transition_locked(
        db,
        session_row,
        target_status="FAILED",
        expected_version=expected_version,
        terminal_reason_code=_WRONG_TAXPAYER_CONTEXT,
        operation_result=None,
    )
    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=None,
        action=AuditAction.SESSION_FAILED,
        entity_type="sol_access_session",
        entity_id=result.id,
        metadata={"terminal_reason_code": _WRONG_TAXPAYER_CONTEXT},
    )
    return result


def report_succeeded(
    db: Session, *, tenant_id: uuid.UUID, session_id: uuid.UUID, expected_version: int
) -> SolAccessSession:
    """`SUCCEEDED` significa unicamente "la sesion termino correctamente
    dentro de `AUTHENTICATE_TO_LANDING_ONLY`" -- `operation_result` siempre
    `LANDING_REACHED` aqui, nunca Buzon/tramite/documento/pago."""
    session_row = repo.get_session_for_update(db, tenant_id=tenant_id, session_id=session_id)
    if session_row is None:
        raise SessionNotFoundError()
    return _transition_locked(
        db,
        session_row,
        target_status="SUCCEEDED",
        expected_version=expected_version,
        terminal_reason_code=None,
        operation_result="LANDING_REACHED",
    )


def report_partial_failure(
    db: Session, *, tenant_id: uuid.UUID, session_id: uuid.UUID, expected_version: int, terminal_reason_code: str
) -> SolAccessSession:
    """Solo la matriz exacta congelada en STATE_MACHINE seccion 9: landing +
    contribuyente confirmados, pero un paso posterior de cierre/auditoria
    fallo -- NUNCA un cajon de sastre. `operation_result=LANDING_REACHED`
    siempre (el resultado principal SI se logro)."""
    if terminal_reason_code == _WRONG_TAXPAYER_CONTEXT:
        raise SolAccessSessionServiceError(
            "SOL_WRONG_TAXPAYER_CONTEXT_MUST_FAIL",
            "WRONG_TAXPAYER_CONTEXT solo puede resultar en FAILED",
        )
    session_row = repo.get_session_for_update(db, tenant_id=tenant_id, session_id=session_id)
    if session_row is None:
        raise SessionNotFoundError()
    return _transition_locked(
        db,
        session_row,
        target_status="PARTIAL_FAILURE",
        expected_version=expected_version,
        terminal_reason_code=terminal_reason_code,
        operation_result="LANDING_REACHED",
    )


def report_failed(
    db: Session, *, tenant_id: uuid.UUID, session_id: uuid.UUID, expected_version: int, terminal_reason_code: str
) -> SolAccessSession:
    session_row = repo.get_session_for_update(db, tenant_id=tenant_id, session_id=session_id)
    if session_row is None:
        raise SessionNotFoundError()
    result = _transition_locked(
        db,
        session_row,
        target_status="FAILED",
        expected_version=expected_version,
        terminal_reason_code=terminal_reason_code,
        operation_result=None,
    )
    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=None,
        action=AuditAction.SESSION_FAILED,
        entity_type="sol_access_session",
        entity_id=result.id,
        metadata={"terminal_reason_code": terminal_reason_code},
    )
    return result


def cancel_session(
    db: Session, *, tenant_id: uuid.UUID, session_id: uuid.UUID, actor_user_id: uuid.UUID | None = None
) -> SolAccessSession:
    """Idempotente (STATE_MACHINE seccion 7): ya `CANCELLED` -> no-op
    exitoso; cualquier OTRO terminal nunca se reescribe. Cancela tambien el
    job activo de la sesion, si existe, dentro de la MISMA transaccion (el
    lock de la sesion se adquiere ANTES que el del job, mismo orden que
    `sol_agent_job_service`, para que cancel-vs-claim quede serializado sin
    deadlock)."""
    from app.services import sol_agent_job_service  # evita import circular a nivel de modulo

    session_row = repo.get_session_for_update(db, tenant_id=tenant_id, session_id=session_id)
    if session_row is None:
        raise SessionNotFoundError()
    if session_row.status == "CANCELLED":
        return session_row
    if session_row.status in _TERMINAL:
        raise SessionTerminalError()

    result = _transition_locked(
        db,
        session_row,
        target_status="CANCELLED",
        expected_version=session_row.version,
        terminal_reason_code=_CANCELLED_BY_USER_REASON,
        operation_result=None,
    )
    sol_agent_job_service.cancel_active_job_for_session(db, tenant_id=tenant_id, session_id=session_id)

    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=actor_user_id,
        action=AuditAction.SESSION_CANCELLED,
        entity_type="sol_access_session",
        entity_id=result.id,
    )
    return result


def expire_session(db: Session, *, tenant_id: uuid.UUID, session_id: uuid.UUID) -> SolAccessSession:
    """Operacion explicita (sin scheduler en Domain C, seccion 31 del
    encargo). Solo actua sobre una sesion no-terminal cuyo `expires_at` ya
    paso segun el tiempo autoritativo de PostgreSQL (`clock.db_now`, Domain
    C.1 Gate 2 -- nunca el reloj del proceso backend, que un `clock.set_clock()`
    sesgado no puede usar para extender artificialmente la sesion).

    Cancela tambien el job activo de la sesion, si existe, en la MISMA
    transaccion (Domain C.1, Gate 1/seccion 3 -- identico efecto que
    `cancel_session`: ninguna sesion terminal queda con un job todavia
    ejecutable asociado)."""
    from app.services import sol_agent_job_service  # evita import circular a nivel de modulo

    session_row = repo.get_session_for_update(db, tenant_id=tenant_id, session_id=session_id)
    if session_row is None:
        raise SessionNotFoundError()
    if session_row.status == "EXPIRED":
        return session_row
    if session_row.status in _TERMINAL:
        raise SessionTerminalError()
    if session_row.expires_at > clock.db_now(db):
        raise SessionNotExpiredError()

    result = _transition_locked(
        db,
        session_row,
        target_status="EXPIRED",
        expected_version=session_row.version,
        terminal_reason_code=_SESSION_EXPIRED_REASON,
        operation_result=None,
    )
    sol_agent_job_service.cancel_active_job_for_session(db, tenant_id=tenant_id, session_id=session_id)
    return result
