"""Ciclo de vida del job dirigido a un agente (Fase 5, Bloque 5.2, Domain C
-- ver docs/phase5/block5_2/). Ningun metodo ejecuta `db.commit()`/
`db.rollback()` (ADR-0037): el llamador controla la transaccion.

Domain C NO descifra la credencial SOL ni devuelve texto plano -- ver
`mark_credential_delivered`, que unicamente registra el gate DB/lifecycle de
"como mucho una entrega por sesion", nunca ejecuta ni simula la entrega de
red en si (encargo de Domain C, secciones 47-51)."""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.security import hash_session_token
from app.models.audit_log import AuditAction
from app.models.sol_agent_access import SESSION_TERMINAL_STATUSES, SolAgentJob
from app.repositories import sol_agent_access_repository as repo
from app.services import audit_service

JOB_TTL_SECONDS_DEFAULT = 5 * 60  # mas corto que session_ttl (Domain A.1 seccion 8)

_SESSION_TERMINAL: frozenset[str] = frozenset(SESSION_TERMINAL_STATUSES)
_JOB_ACTIVE_STATUSES = frozenset({"PENDING", "CLAIMED"})


class SolAgentJobServiceError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class JobNotFoundError(SolAgentJobServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_JOB_NOT_FOUND")


class JobVersionConflictError(SolAgentJobServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_JOB_VERSION_CONFLICT")


class JobTerminalError(SolAgentJobServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_JOB_TERMINAL", "El job ya esta en un estado terminal")


class JobNotExpiredError(SolAgentJobServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_JOB_NOT_YET_EXPIRED")


class SessionNotEligibleForJobError(SolAgentJobServiceError):
    def __init__(self, reason: str) -> None:
        super().__init__("SOL_SESSION_NOT_ELIGIBLE_FOR_JOB", reason)


class AgentNotEligibleError(SolAgentJobServiceError):
    def __init__(self, reason: str) -> None:
        super().__init__("SOL_AGENT_NOT_ELIGIBLE", reason)


class ActiveJobAlreadyExistsError(SolAgentJobServiceError):
    def __init__(self, reason: str) -> None:
        super().__init__("SOL_JOB_ACTIVE_ALREADY_EXISTS", reason)


class CredentialAlreadyDeliveredError(SolAgentJobServiceError):
    def __init__(self) -> None:
        super().__init__(
            "SOL_CREDENTIAL_ALREADY_DELIVERED",
            "Esta sesion ya consumio su unica entrega de credencial autorizada",
        )


class InvalidJobTransitionError(SolAgentJobServiceError):
    def __init__(self, from_status: str, to_status: str) -> None:
        super().__init__("SOL_JOB_INVALID_TRANSITION", f"Transicion invalida: {from_status} -> {to_status}")


class JobClaimInvalidError(SolAgentJobServiceError):
    """Generico y deliberadamente poco especifico (encargo de Domain C,
    seccion 42): cubre token invalido, job inexistente/no-PENDING, agente no
    ACTIVE, y replay -- un llamador no confiable no debe poder distinguir
    estos casos entre si."""

    def __init__(self) -> None:
        super().__init__("SOL_JOB_CLAIM_INVALID")


@dataclass
class JobCreateResult:
    """`job_token` es texto plano, disponible SOLO aqui -- nunca se persiste
    (solo `job_token_hash`, ver Domain A.2). `__repr__` redactado (encargo
    de Domain C, seccion 40)."""

    job: SolAgentJob
    job_token: str

    def __repr__(self) -> str:
        return f"JobCreateResult(job_id={self.job.id!r}, job_token='***REDACTED***')"


def get_job(db: Session, *, tenant_id: uuid.UUID, job_id: uuid.UUID) -> SolAgentJob:
    job = repo.get_job(db, tenant_id=tenant_id, job_id=job_id)
    if job is None:
        raise JobNotFoundError()
    return job


def create_job(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    agent_id: uuid.UUID,
    job_ttl_seconds: int = JOB_TTL_SECONDS_DEFAULT,
) -> JobCreateResult:
    """Precondiciones congeladas (Domain A.1/A.2, encargo de Domain C
    seccion 36-37): sesion no-terminal y no vencida; agente ACTIVE del mismo
    tenant; ningun job de la sesion con `credential_delivered_at` poblado;
    sin job activo para la sesion NI para el agente (el indice unico
    parcial de BD es el backstop final, esta comprobacion proactiva da un
    error legible antes de intentarlo)."""
    from app.services import sol_access_session_service as session_service

    session_row = _lock_session(db, tenant_id=tenant_id, session_id=session_id)
    if session_row.status in _SESSION_TERMINAL:
        raise SessionNotEligibleForJobError("la sesion ya esta en un estado terminal")
    if session_row.expires_at <= clock.db_now(db):
        raise SessionNotEligibleForJobError("la sesion ya vencio su TTL")

    agent = repo.get_agent_for_update(db, tenant_id=tenant_id, agent_id=agent_id)
    if agent is None:
        raise AgentNotEligibleError("agente no encontrado en este tenant")
    if agent.status != "ACTIVE":
        raise AgentNotEligibleError("el agente no esta ACTIVE")

    if repo.has_delivered_credential_for_session(db, tenant_id=tenant_id, session_id=session_id):
        raise CredentialAlreadyDeliveredError()
    if repo.get_active_job_for_session(db, tenant_id=tenant_id, session_id=session_id) is not None:
        raise ActiveJobAlreadyExistsError("ya existe un job activo para esta sesion")
    if repo.get_active_job_for_agent(db, tenant_id=tenant_id, agent_id=agent_id) is not None:
        raise ActiveJobAlreadyExistsError("ya existe un job activo para este agente")

    now = clock.db_now(db)
    job_expires_at = min(now + timedelta(seconds=job_ttl_seconds), session_row.expires_at)
    token = secrets.token_urlsafe(32)
    job = SolAgentJob(
        tenant_id=tenant_id,
        session_id=session_id,
        agent_id=agent_id,
        status="PENDING",
        job_token_hash=hash_session_token(token),
        created_at=now,
        expires_at=job_expires_at,
    )
    db.add(job)
    db.flush()

    if session_row.status == "CREATED":
        session_service.assign_agent(
            db, tenant_id=tenant_id, session_id=session_id, expected_version=session_row.version, agent_id=agent_id
        )
    else:
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

    return JobCreateResult(job=job, job_token=token)


def _lock_session(db: Session, *, tenant_id: uuid.UUID, session_id: uuid.UUID):
    """Bloquea la fila de `sol_access_sessions` (`SELECT ... FOR UPDATE`)
    ANTES de bloquear ninguna fila de job/agente -- mismo orden global de
    locks (sesion primero) que usa `sol_access_session_service.cancel_session`,
    para que las carreras cancel-vs-claim / cancel-vs-creacion de job queden
    serializadas sin deadlock."""
    session_row = repo.get_session_for_update(db, tenant_id=tenant_id, session_id=session_id)
    if session_row is None:
        raise SessionNotEligibleForJobError("sesion no encontrada")
    return session_row


def claim_job(db: Session, *, job_token: str) -> SolAgentJob:
    """Atomico: bloquea la sesion PRIMERO (mismo orden global de locks que
    `_lock_session_then_job`/`cancel_session` -- session antes que job,
    siempre, para que cancel-vs-claim quede serializado sin deadlock), luego
    el job por `job_token_hash` (UNIQUE global). Un segundo intento con el
    MISMO token, tras el primero exitoso, encuentra `status != 'PENDING'` y
    se rechaza -- replay detectado sin distinguir motivos especificos ante
    el llamador (seccion 42).

    La localizacion previa de `tenant_id`/`session_id` usa una consulta de
    SOLO columnas (`select(SolAgentJob.tenant_id, ...)`), nunca
    `select(SolAgentJob)` -- cargar la entidad completa aqui poblaria el
    identity map de esta `Session` con un objeto potencialmente obsoleto
    (leido ANTES de bloquear la sesion) que SQLAlchemy reutilizaria sin
    refrescar en la relectura posterior con lock, incluso si la fila
    cambio de verdad bajo concurrencia real."""
    token_hash = hash_session_token(job_token)
    located = db.execute(
        select(SolAgentJob.tenant_id, SolAgentJob.session_id).where(SolAgentJob.job_token_hash == token_hash)
    ).first()
    if located is None:
        raise JobClaimInvalidError()
    tenant_id, session_id = located

    session_row = repo.get_session_for_update(db, tenant_id=tenant_id, session_id=session_id)
    job = repo.get_job_by_token_hash_for_update(db, job_token_hash=token_hash)
    if session_row is None or job is None:
        raise JobClaimInvalidError()
    if job.status != "PENDING":
        raise JobClaimInvalidError()
    if job.expires_at <= clock.db_now(db):
        raise JobClaimInvalidError()
    if session_row.status in _SESSION_TERMINAL or session_row.expires_at <= clock.db_now(db):
        raise JobClaimInvalidError()

    agent = repo.get_agent_for_update(db, tenant_id=tenant_id, agent_id=job.agent_id)
    if agent is None or agent.status != "ACTIVE":
        raise JobClaimInvalidError()

    job.status = "CLAIMED"
    job.claimed_at = clock.db_now(db)
    job.version += 1
    db.flush()

    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=None,
        action=AuditAction.JOB_CLAIMED,
        entity_type="sol_agent_job",
        entity_id=job.id,
        metadata={"session_id": str(session_id), "agent_id": str(job.agent_id)},
    )
    return job


def _lock_session_then_job(db: Session, *, tenant_id: uuid.UUID, job_id: uuid.UUID) -> tuple:
    """Localiza `session_id` con una consulta de SOLO columnas (nunca
    `select(SolAgentJob)`) para no poblar el identity map de esta `Session`
    con un objeto potencialmente obsoleto antes de bloquear -- ver la nota
    equivalente en `claim_job`, mismo bug de fondo que corrige."""
    located = db.execute(
        select(SolAgentJob.session_id).where(SolAgentJob.tenant_id == tenant_id, SolAgentJob.id == job_id)
    ).first()
    if located is None:
        raise JobNotFoundError()
    (session_id,) = located
    session_row = repo.get_session_for_update(db, tenant_id=tenant_id, session_id=session_id)
    job = repo.get_job_for_update(db, tenant_id=tenant_id, job_id=job_id)
    if session_row is None or job is None:
        raise JobNotFoundError()
    return session_row, job


def complete_job(db: Session, *, tenant_id: uuid.UUID, job_id: uuid.UUID, expected_version: int) -> SolAgentJob:
    _session_row, job = _lock_session_then_job(db, tenant_id=tenant_id, job_id=job_id)
    if job.version != expected_version:
        raise JobVersionConflictError()
    if job.status != "CLAIMED":
        raise InvalidJobTransitionError(job.status, "COMPLETED")

    job.status = "COMPLETED"
    job.completed_at = clock.db_now(db)
    job.version += 1
    db.flush()
    return job


def fail_job(db: Session, *, tenant_id: uuid.UUID, job_id: uuid.UUID, expected_version: int) -> SolAgentJob:
    _session_row, job = _lock_session_then_job(db, tenant_id=tenant_id, job_id=job_id)
    if job.version != expected_version:
        raise JobVersionConflictError()
    if job.status not in {"PENDING", "CLAIMED"}:
        raise JobTerminalError()

    job.status = "FAILED"
    job.completed_at = clock.db_now(db)
    job.version += 1
    db.flush()
    return job


def cancel_active_job_for_session(db: Session, *, tenant_id: uuid.UUID, session_id: uuid.UUID) -> SolAgentJob | None:
    """Usado por `sol_access_session_service.cancel_session` -- idempotente
    por construccion: si no hay job activo, no hace nada. La sesion YA debe
    estar bloqueada (`FOR UPDATE`) por el llamador antes de invocar esto."""
    located = db.execute(
        select(SolAgentJob.id).where(
            SolAgentJob.tenant_id == tenant_id,
            SolAgentJob.session_id == session_id,
            SolAgentJob.status.in_(_JOB_ACTIVE_STATUSES),
        )
    ).first()
    if located is None:
        return None
    (job_id,) = located
    locked_job = repo.get_job_for_update(db, tenant_id=tenant_id, job_id=job_id)
    if locked_job is None or locked_job.status not in _JOB_ACTIVE_STATUSES:
        return None

    locked_job.status = "CANCELLED"
    locked_job.cancelled_at = clock.db_now(db)
    locked_job.version += 1
    db.flush()
    return locked_job


def expire_job(db: Session, *, tenant_id: uuid.UUID, job_id: uuid.UUID) -> SolAgentJob:
    """Operacion explicita (sin scheduler en Domain C, seccion 44)."""
    _session_row, job = _lock_session_then_job(db, tenant_id=tenant_id, job_id=job_id)
    if job.status == "EXPIRED":
        return job
    if job.status not in _JOB_ACTIVE_STATUSES:
        raise JobTerminalError()
    if job.expires_at > clock.db_now(db):
        raise JobNotExpiredError()

    job.status = "EXPIRED"
    job.version += 1
    db.flush()
    return job


def mark_credential_delivered(db: Session, *, tenant_id: uuid.UUID, job_id: uuid.UUID, expected_version: int) -> SolAgentJob:
    """El gate DB/lifecycle de "a lo sumo una entrega de credencial por
    sesion" (Domain A.2 Gate 3/4) -- NO decodifica ni devuelve la
    credencial en si (secciones 47-51 del encargo). Precondiciones: sesion
    no-terminal/no-vencida; job CLAIMED de esa sesion/agente, no vencido,
    `credential_delivered_at IS NULL`; agente ACTIVE; ningun job de la
    sesion con entrega previa. El indice unico parcial
    `ux_sol_agent_jobs_one_delivery_per_session` es el backstop final bajo
    concurrencia real (ver test de 2 conexiones).

    IMPORTANTE (Domain C.1, seccion 9 del encargo): al exito, emite
    `AuditAction.CREDENTIAL_DELIVERY_GATE_CONSUMED`, que significa
    UNICAMENTE que este gate transaccional fue consumido -- JAMAS que la
    credencial en claro efectivamente llego al agente por la red. El
    transporte real (broker/mTLS/Windows Agent) es de un dominio posterior,
    fuera de alcance de Domain C, y puede fallar incluso despues de que
    este metodo retorne exitosamente."""
    session_row, job = _lock_session_then_job(db, tenant_id=tenant_id, job_id=job_id)
    if job.version != expected_version:
        raise JobVersionConflictError()
    if session_row.status in _SESSION_TERMINAL or session_row.expires_at <= clock.db_now(db):
        raise SessionNotEligibleForJobError("la sesion no es elegible para entrega de credencial")
    if job.status != "CLAIMED":
        raise JobClaimInvalidError()
    if job.expires_at <= clock.db_now(db):
        raise JobClaimInvalidError()
    if job.credential_delivered_at is not None:
        raise CredentialAlreadyDeliveredError()

    agent = repo.get_agent_for_update(db, tenant_id=tenant_id, agent_id=job.agent_id)
    if agent is None or agent.status != "ACTIVE":
        raise AgentNotEligibleError("el agente no esta ACTIVE")

    if repo.has_delivered_credential_for_session(db, tenant_id=tenant_id, session_id=job.session_id):
        raise CredentialAlreadyDeliveredError()

    job.credential_delivered_at = clock.db_now(db)
    job.version += 1
    db.flush()

    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=None,
        action=AuditAction.CREDENTIAL_DELIVERY_GATE_CONSUMED,
        entity_type="sol_agent_job",
        entity_id=job.id,
        metadata={"session_id": str(job.session_id)},
    )
    return job
