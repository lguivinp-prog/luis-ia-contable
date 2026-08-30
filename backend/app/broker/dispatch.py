"""Punto de integracion interno del broker para despachar un job ya
autorizado hacia el agente correspondiente (Fase 5, Bloque 5.2, Domain D --
ver docs/phase5/block5_2/). Es la "BROKER INTERNAL API" mencionada en el
informe final -- una funcion Python interna al proceso backend, NUNCA un
endpoint HTTP expuesto, y NUNCA llamable por el agente ni por el frontend.

No reimplementa nada de Domain C: llama a `sol_agent_job_service.create_job`
como UNICA autoridad para crear el job (misma sesion/agente/precondiciones
ya congeladas), y unicamente retiene el token en claro que esa llamada
devuelve para puentearlo hacia el agente en el momento del reclamo (ver
`pending_dispatch.py`) -- nunca lo persiste, nunca lo loguea.

Este modulo es deliberadamente el UNICO punto de entrada que una futura User
API (fuera de alcance de Domain D) deberia invocar para pedir "dame un job
para este agente ya conectado" -- expuesto aqui como funcion Python, listo
para que un router FastAPI futuro lo llame directamente dentro del mismo
proceso (sin salto de red adicional, sin duplicar logica)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.broker.pending_dispatch import PendingDispatchStore
from app.services import sol_agent_job_service as job_service


def dispatch_job_to_agent(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    agent_id: uuid.UUID,
    pending_dispatch: PendingDispatchStore,
) -> uuid.UUID:
    """Crea el job (Domain C, `create_job`, unica autoridad -- todas sus
    precondiciones de sesion/agente/credencial-no-entregada/sin-job-activo
    siguen aplicando sin cambios) y registra su token en claro en el puente
    de un solo uso para que el agente correcto lo consuma exactamente una
    vez, exclusivamente durante su propio protocolo de reclamo mTLS. Nunca
    devuelve el token en claro a este llamador -- ni siquiera el caller
    interno lo necesita despues de este punto."""
    result = job_service.create_job(db, tenant_id=tenant_id, session_id=session_id, agent_id=agent_id)
    pending_dispatch.register(
        agent_id=agent_id, tenant_id=tenant_id, job_id=result.job.id, raw_token=result.job_token
    )
    return result.job.id
