"""Ciclo de vida del agente Windows emparejado (Fase 5, Bloque 5.2, Domain
C -- ver docs/phase5/block5_2/). Ningun metodo de este servicio ejecuta
`db.commit()`/`db.rollback()` (mismo patron que sol_vault_service.py,
ADR-0037): el llamador controla la transaccion. `ONLINE`/`OFFLINE` nunca se
persiste -- se deriva de `last_seen_at` (Domain A.1 seccion 2)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.models.audit_log import AuditAction
from app.models.sol_agent_access import SolAgent
from app.repositories import sol_agent_access_repository as repo
from app.services import audit_service


class SolAgentServiceError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class AgentNotFoundError(SolAgentServiceError):
    def __init__(self) -> None:
        super().__init__("SOL_AGENT_NOT_FOUND")


def get_agent(db: Session, *, tenant_id: uuid.UUID, agent_id: uuid.UUID) -> SolAgent:
    agent = repo.get_agent(db, tenant_id=tenant_id, agent_id=agent_id)
    if agent is None:
        raise AgentNotFoundError()
    return agent


def list_agents(db: Session, *, tenant_id: uuid.UUID) -> list[SolAgent]:
    return repo.list_agents(db, tenant_id=tenant_id)


def is_online(agent: SolAgent, *, now=None) -> bool:
    """Derivado unicamente de `last_seen_at` -- NUNCA suficiente por si solo
    para autorizar una accion sensible (Domain A.2 seccion 23): la conexion
    mTLS viva sigue siendo requerida por el broker (Domain futuro), este
    helper es solo para UI/observabilidad."""
    if agent.last_seen_at is None:
        return False
    reference = now if now is not None else clock.utcnow()
    threshold = get_settings().sol_agent_online_threshold_seconds
    return (reference - agent.last_seen_at).total_seconds() <= threshold


def revoke_agent(db: Session, *, tenant_id: uuid.UUID, agent_id: uuid.UUID, actor_user_id: uuid.UUID | None) -> SolAgent:
    """`ACTIVE -> REVOKED` con `revoked_at` autoritativo. Idempotente: un
    agente ya `REVOKED` no cambia nada y no es un error (Domain C seccion
    21). Un agente revocado no puede recibir jobs nuevos ni participar en el
    gate de entrega -- forzado por las precondiciones de
    sol_agent_job_service, no solo por convencion."""
    agent = repo.get_agent_for_update(db, tenant_id=tenant_id, agent_id=agent_id)
    if agent is None:
        raise AgentNotFoundError()
    if agent.status == "REVOKED":
        return agent

    now = clock.utcnow()
    agent.status = "REVOKED"
    agent.revoked_at = now
    agent.version += 1
    db.flush()

    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=actor_user_id,
        action=AuditAction.AGENT_REVOKED,
        entity_type="sol_agent",
        entity_id=agent.id,
        metadata={"status": agent.status},
    )
    return agent


def record_heartbeat(db: Session, *, tenant_id: uuid.UUID, agent_id: uuid.UUID) -> SolAgent:
    """Requiere agente `ACTIVE` -- un agente `REVOKED` no puede refrescar su
    `last_seen_at` (evitaria que se derive como offline de forma indefinida
    tras la revocacion, aunque siga transmitiendo). Sin transporte de red
    real en Domain C: este metodo modela lo que un futuro broker invocaria
    al recibir un heartbeat, sin implementar el transporte en si."""
    agent = repo.get_agent_for_update(db, tenant_id=tenant_id, agent_id=agent_id)
    if agent is None:
        raise AgentNotFoundError()
    if agent.status != "ACTIVE":
        raise SolAgentServiceError("SOL_AGENT_NOT_ACTIVE", "Solo un agente ACTIVE puede reportar heartbeat")

    agent.last_seen_at = clock.utcnow()
    db.flush()
    return agent
