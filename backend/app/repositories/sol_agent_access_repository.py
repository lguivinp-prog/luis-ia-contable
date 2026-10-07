"""Consultas de acceso SOL supervisado (Fase 5, Bloque 5.2, ver
docs/phase5/block5_2/). Ningun servicio de este bloque ejecuta
`db.execute(select(...))` directamente -- misma convencion que
sol_vault_repository.py."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.sol_agent_access import (
    JOB_ACTIVE_STATUSES,
    SESSION_TERMINAL_STATUSES,
    SolAccessSession,
    SolAgent,
    SolAgentJob,
    SolAgentPairing,
)

# --- sol_agents ---


def get_agent(db: Session, *, tenant_id: uuid.UUID, agent_id: uuid.UUID) -> SolAgent | None:
    return db.execute(
        select(SolAgent).where(SolAgent.id == agent_id, SolAgent.tenant_id == tenant_id)
    ).scalar_one_or_none()


def get_agent_for_update(db: Session, *, tenant_id: uuid.UUID, agent_id: uuid.UUID) -> SolAgent | None:
    """`populate_existing=True` (hallazgo de Domain E1-B, confirmado con
    una prueba diagnostica directa contra PostgreSQL real): sin esto, si
    esta `Session` ya tenia este PK en su identity map por una lectura
    PREVIA sin bloqueo (ej. `get_agent`), `with_for_update()` SI adquiere
    el lock de fila correcto a nivel de BD, pero el ORM devuelve el objeto
    Python ya cacheado SIN refrescar sus atributos -- una mutacion
    concurrente ya comprometida por otra sesion (ej. revocacion) puede
    pasar inadvertida en el chequeo que sigue. Toda funcion `*_for_update`
    de este modulo existe especificamente para tomar una decision de
    seguridad sobre el estado ACTUAL de la fila -- nunca debe devolver un
    valor obsoleto."""
    return db.execute(
        select(SolAgent)
        .where(SolAgent.id == agent_id, SolAgent.tenant_id == tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def get_agent_by_certificate_fingerprint(db: Session, *, certificate_fingerprint: str) -> SolAgent | None:
    """`certificate_fingerprint` es UNIQUE global (Domain A.1/A.2) -- la
    busqueda no filtra por tenant a proposito."""
    return db.execute(
        select(SolAgent).where(SolAgent.certificate_fingerprint == certificate_fingerprint)
    ).scalar_one_or_none()


def list_agents(db: Session, *, tenant_id: uuid.UUID) -> list[SolAgent]:
    return list(
        db.execute(select(SolAgent).where(SolAgent.tenant_id == tenant_id)).scalars().all()
    )


# --- sol_access_sessions ---


def get_session(db: Session, *, tenant_id: uuid.UUID, session_id: uuid.UUID) -> SolAccessSession | None:
    return db.execute(
        select(SolAccessSession).where(
            SolAccessSession.id == session_id, SolAccessSession.tenant_id == tenant_id
        )
    ).scalar_one_or_none()


def get_session_for_update(
    db: Session, *, tenant_id: uuid.UUID, session_id: uuid.UUID
) -> SolAccessSession | None:
    """`populate_existing=True` -- ver el docstring identico de
    `get_agent_for_update` (mismo hallazgo de Domain E1-B, misma correccion
    aplicada de forma consistente a las cuatro funciones `*_for_update` de
    este modulo)."""
    return db.execute(
        select(SolAccessSession)
        .where(SolAccessSession.id == session_id, SolAccessSession.tenant_id == tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def get_session_by_idempotency_key(
    db: Session, *, tenant_id: uuid.UUID, idempotency_key: uuid.UUID
) -> SolAccessSession | None:
    return db.execute(
        select(SolAccessSession).where(
            SolAccessSession.tenant_id == tenant_id,
            SolAccessSession.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()


def get_active_session_for_credential(
    db: Session, *, tenant_id: uuid.UUID, client_id: uuid.UUID, credential_id: uuid.UUID
) -> SolAccessSession | None:
    """Refleja `ux_sol_access_sessions_active_per_credential` -- como mucho
    una sesion no-terminal por (tenant_id, client_id, credential_id)."""
    return db.execute(
        select(SolAccessSession).where(
            SolAccessSession.tenant_id == tenant_id,
            SolAccessSession.client_id == client_id,
            SolAccessSession.credential_id == credential_id,
            SolAccessSession.status.not_in(SESSION_TERMINAL_STATUSES),
        )
    ).scalar_one_or_none()


# --- sol_agent_jobs ---


def get_job(db: Session, *, tenant_id: uuid.UUID, job_id: uuid.UUID) -> SolAgentJob | None:
    return db.execute(
        select(SolAgentJob).where(SolAgentJob.id == job_id, SolAgentJob.tenant_id == tenant_id)
    ).scalar_one_or_none()


def get_job_for_update(db: Session, *, tenant_id: uuid.UUID, job_id: uuid.UUID) -> SolAgentJob | None:
    """`populate_existing=True` -- ver el docstring identico de
    `get_agent_for_update` (mismo hallazgo de Domain E1-B)."""
    return db.execute(
        select(SolAgentJob)
        .where(SolAgentJob.id == job_id, SolAgentJob.tenant_id == tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def get_job_by_token_hash_for_update(db: Session, *, job_token_hash: str) -> SolAgentJob | None:
    """`job_token_hash` es UNIQUE global (Domain A.1/A.2) -- el claim
    identifica el job por el token, no por `job_id` (el agente solo conoce
    el token en claro que recibio, nunca el UUID del job). `populate_existing=True`
    -- ver el docstring identico de `get_agent_for_update` (mismo hallazgo
    de Domain E1-B)."""
    return db.execute(
        select(SolAgentJob)
        .where(SolAgentJob.job_token_hash == job_token_hash)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def get_active_job_for_session(db: Session, *, tenant_id: uuid.UUID, session_id: uuid.UUID) -> SolAgentJob | None:
    return db.execute(
        select(SolAgentJob).where(
            SolAgentJob.tenant_id == tenant_id,
            SolAgentJob.session_id == session_id,
            SolAgentJob.status.in_(JOB_ACTIVE_STATUSES),
        )
    ).scalar_one_or_none()


def get_active_job_for_agent(db: Session, *, tenant_id: uuid.UUID, agent_id: uuid.UUID) -> SolAgentJob | None:
    return db.execute(
        select(SolAgentJob).where(
            SolAgentJob.tenant_id == tenant_id,
            SolAgentJob.agent_id == agent_id,
            SolAgentJob.status.in_(JOB_ACTIVE_STATUSES),
        )
    ).scalar_one_or_none()


def has_delivered_credential_for_session(
    db: Session, *, tenant_id: uuid.UUID, session_id: uuid.UUID
) -> bool:
    row = db.execute(
        select(SolAgentJob.id).where(
            SolAgentJob.tenant_id == tenant_id,
            SolAgentJob.session_id == session_id,
            SolAgentJob.credential_delivered_at.is_not(None),
        )
    ).first()
    return row is not None


def list_jobs_for_session(db: Session, *, tenant_id: uuid.UUID, session_id: uuid.UUID) -> list[SolAgentJob]:
    return list(
        db.execute(
            select(SolAgentJob)
            .where(SolAgentJob.tenant_id == tenant_id, SolAgentJob.session_id == session_id)
            .order_by(SolAgentJob.created_at)
        ).scalars().all()
    )


# --- sol_agent_pairings ---


def get_pairing(db: Session, *, tenant_id: uuid.UUID, pairing_id: uuid.UUID) -> SolAgentPairing | None:
    return db.execute(
        select(SolAgentPairing).where(
            SolAgentPairing.id == pairing_id, SolAgentPairing.tenant_id == tenant_id
        )
    ).scalar_one_or_none()


def get_pairing_for_update(
    db: Session, *, tenant_id: uuid.UUID, pairing_id: uuid.UUID
) -> SolAgentPairing | None:
    return db.execute(
        select(SolAgentPairing)
        .where(SolAgentPairing.id == pairing_id, SolAgentPairing.tenant_id == tenant_id)
        .with_for_update()
    ).scalar_one_or_none()


def get_pending_pairing_for_requester(
    db: Session, *, tenant_id: uuid.UUID, requested_by_user_id: uuid.UUID
) -> SolAgentPairing | None:
    return db.execute(
        select(SolAgentPairing).where(
            SolAgentPairing.tenant_id == tenant_id,
            SolAgentPairing.requested_by_user_id == requested_by_user_id,
            SolAgentPairing.status == "PENDING",
        )
    ).scalar_one_or_none()


# --- rate limit de creacion de pairing (Domain C.1, Gate 3 -- segunda capa,
# mismo idioma de conteo de filas que login_attempt_repository.py) ---


def get_last_completed_pairing_created_at(
    db: Session, *, tenant_id: uuid.UUID, requested_by_user_id: uuid.UUID
) -> datetime | None:
    return db.execute(
        select(func.max(SolAgentPairing.created_at)).where(
            SolAgentPairing.tenant_id == tenant_id,
            SolAgentPairing.requested_by_user_id == requested_by_user_id,
            SolAgentPairing.status == "COMPLETED",
        )
    ).scalar_one_or_none()


def count_pairings_created_since(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    requested_by_user_id: uuid.UUID,
    since: datetime | None,
) -> int:
    query = select(func.count()).select_from(SolAgentPairing).where(
        SolAgentPairing.tenant_id == tenant_id,
        SolAgentPairing.requested_by_user_id == requested_by_user_id,
    )
    if since is not None:
        query = query.where(SolAgentPairing.created_at > since)
    return db.execute(query).scalar_one()


def get_most_recent_pairing_created_at(
    db: Session, *, tenant_id: uuid.UUID, requested_by_user_id: uuid.UUID
) -> datetime | None:
    return db.execute(
        select(func.max(SolAgentPairing.created_at)).where(
            SolAgentPairing.tenant_id == tenant_id,
            SolAgentPairing.requested_by_user_id == requested_by_user_id,
        )
    ).scalar_one_or_none()


def get_latest_session_for_client(
    db: Session, *, tenant_id: uuid.UUID, client_id: uuid.UUID
) -> SolAccessSession | None:
    """Domain F: sesion mas reciente del cliente (la activa si existe, pues es siempre la mas reciente por la
    unicidad de sesion activa por credencial). Solo lectura, tenant-scoped."""
    return db.execute(
        select(SolAccessSession)
        .where(SolAccessSession.tenant_id == tenant_id, SolAccessSession.client_id == client_id)
        .order_by(SolAccessSession.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
