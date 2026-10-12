"""M2: reconciliacion de sesiones SOL huerfanas o estancadas (agente/backend caido o reiniciado, ejecutor que fallo sin informar).

Un job `CLAIMED` mantiene reservado al agente. Si el agente pierde de verdad esa sesion (proceso reiniciado, navegador cerrado, error
del ejecutor sin reporte) el job quedaria reservado hasta que venza la sesion (30 min) y la UI seguiria mostrando "en curso". Aqui se
libera de forma acotada y sin tocar sesiones humanas legitimas:

- Camino por HEARTBEAT: el agente declara que job sostiene (`holding_job_id`). Si esta VIVO (heartbeat reciente) y NO sostiene el job de
  una sesion `IN_PROGRESS|WAITING_FOR_USER|HUMAN_CONTROL`, la sesion es huerfana (reinicio del proceso del agente).
- Camino por AUSENCIA: si el agente lleva sin heartbeat mas de `OFFLINE_GRACE_SECONDS` (lease acotado) la sesion es huerfana.
- Camino por ESTANCAMIENTO: una sesion `IN_PROGRESS` (automatizacion en curso, sin esperar al humano) que no registra ninguna
  transicion durante `IN_PROGRESS_STALL_SECONDS` se cierra, aunque el agente siga latiendo (p. ej. un ejecutor que murio sin reportar
  y quedo "sosteniendo" el job). `WAITING_FOR_USER` y `HUMAN_CONTROL` NO se consideran estancadas: ahi manda el humano.

Resultado: sesion `FAILED` (`AGENT_CRASHED` / `JOB_EXPIRED`) y job `FAILED` (no se reclama ni se vuelve a entregar la credencial).
Nunca se cancela una sesion cuyo agente declara sostenerla y avanza."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.core import clock
from app.models.sol_agent_access import SolAccessTerminalReasonCode
from app.repositories import sol_agent_access_repository as repo
from app.services import sol_access_session_service as session_service
from app.services import sol_agent_job_service as job_service

# El agente declara su job en cada heartbeat (cada ~3 s): este margen solo cubre la carrera entre el claim/HUMAN_CONTROL y el
# primer heartbeat posterior.
HC_HOLDING_GRACE_SECONDS = 15
HOLDING_GRACE_SECONDS = HC_HOLDING_GRACE_SECONDS
# Sin heartbeat durante este lease acotado (> umbral "online" del agente) la sesion se da por perdida.
HC_OFFLINE_GRACE_SECONDS = 120
OFFLINE_GRACE_SECONDS = HC_OFFLINE_GRACE_SECONDS
# Una automatizacion sin esperar al humano deberia avanzar en segundos; sin ninguna transicion en este tiempo esta estancada.
IN_PROGRESS_STALL_SECONDS = 180

_ACTIVE_SESSION_STATES = frozenset({"IN_PROGRESS", "WAITING_FOR_USER", "HUMAN_CONTROL"})
_ORPHAN_REASON = SolAccessTerminalReasonCode.AGENT_CRASHED.value
_STALLED_REASON = SolAccessTerminalReasonCode.JOB_EXPIRED.value


def _orphan_candidate(db: Session, *, tenant_id: uuid.UUID, agent_id: uuid.UUID):
    job = repo.get_active_job_for_agent(db, tenant_id=tenant_id, agent_id=agent_id)
    if job is None or job.status != "CLAIMED":
        return None, None
    session_row = repo.get_session(db, tenant_id=tenant_id, session_id=job.session_id)
    if session_row is None or session_row.status not in _ACTIVE_SESSION_STATES:
        return None, None
    return job, session_row


def _reference_time(job, session_row) -> datetime:
    if session_row.status == "HUMAN_CONTROL":
        return session_row.human_control_at or session_row.created_at
    return job.claimed_at or session_row.updated_at or session_row.created_at


def _release(db: Session, *, job, session_row, reason: str) -> bool:
    """Falla sesion y job en una subtransaccion; `False` si cambiaron concurrentemente (no se toca nada)."""
    try:
        with db.begin_nested():
            session_service.report_failed(
                db,
                tenant_id=session_row.tenant_id,
                session_id=session_row.id,
                expected_version=session_row.version,
                terminal_reason_code=reason,
            )
            job_service.fail_job(db, tenant_id=job.tenant_id, job_id=job.id, expected_version=job.version)
    except (
        session_service.SolAccessSessionServiceError,
        job_service.SolAgentJobServiceError,
    ):
        return False
    return True


def _stalled(session_row, now: datetime) -> bool:
    return (
        session_row.status == "IN_PROGRESS"
        and session_row.updated_at is not None
        and (now - session_row.updated_at).total_seconds() >= IN_PROGRESS_STALL_SECONDS
    )


def reconcile_on_heartbeat(
    db: Session, *, tenant_id: uuid.UUID, agent_id: uuid.UUID, holding_job_id: uuid.UUID | None, now: datetime | None = None
) -> bool:
    """El agente esta vivo (acaba de emitir heartbeat). `True` si libero una sesion huerfana o estancada."""
    job, session_row = _orphan_candidate(db, tenant_id=tenant_id, agent_id=agent_id)
    if job is None or session_row is None:
        return False
    now = now or clock.db_now(db)
    if holding_job_id != job.id:
        if (now - _reference_time(job, session_row)).total_seconds() < HOLDING_GRACE_SECONDS:
            return False
        return _release(db, job=job, session_row=session_row, reason=_ORPHAN_REASON)
    if _stalled(session_row, now):
        return _release(db, job=job, session_row=session_row, reason=_STALLED_REASON)
    return False


def reconcile_absent_agent(db: Session, *, tenant_id: uuid.UUID, agent, now: datetime | None = None) -> bool:
    """El agente no emite heartbeat. `True` si libero una sesion huerfana (lease acotado vencido)."""
    now = now or clock.db_now(db)
    if agent.last_seen_at is not None and (now - agent.last_seen_at).total_seconds() < OFFLINE_GRACE_SECONDS:
        return False
    job, session_row = _orphan_candidate(db, tenant_id=tenant_id, agent_id=agent.id)
    if job is None or session_row is None:
        return False
    return _release(db, job=job, session_row=session_row, reason=_ORPHAN_REASON)


def reconcile_stalled_session(db: Session, *, tenant_id: uuid.UUID, session_id: uuid.UUID, now: datetime | None = None) -> bool:
    """Lectura de una sesion (la UI consulta): una automatizacion `IN_PROGRESS` estancada se cierra aunque el agente siga latiendo."""
    session_row = repo.get_session(db, tenant_id=tenant_id, session_id=session_id)
    if session_row is None or session_row.status != "IN_PROGRESS":
        return False
    job = repo.get_active_job_for_session(db, tenant_id=tenant_id, session_id=session_id)
    if job is None or job.status != "CLAIMED":
        return False
    now = now or clock.db_now(db)
    if not _stalled(session_row, now):
        return False
    return _release(db, job=job, session_row=session_row, reason=_STALLED_REASON)
