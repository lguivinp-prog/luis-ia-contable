"""Fase 5, Bloque 5.2, Domain E3.1 -- proyeccion AUTORITATIVA del resultado
semantico del navegador (Agente Windows) sobre `sol_access_sessions`.

Frontera de autoridad: el Agente reporta un evento semantico de lista cerrada
(`COMPLETED` / `WAITING_FOR_USER` / `FAILED`+codigo); este servicio -- en el
BACKEND, dentro de UNA transaccion con los locks de Domain C (sesion antes que
job) -- valida tenant, agente, job, relacion job/sesion, estado actual y
legalidad de la transicion, y SOLO entonces transiciona. Reutiliza las
transiciones ya existentes (`report_waiting_for_user`/`start_progress`/
`report_succeeded`/`report_failed`): no se cambia la maquina de estados ni el
esquema. La conexion viva (`connection_id`) ya la valido el broker antes de
llamar aqui.

Relacion job/sesion (explicita):
- `WAITING_FOR_USER` es un estado de SESION; el job sigue `CLAIMED`.
- el job pasa a `COMPLETED` SOLO junto con la sesion `SUCCEEDED` (misma
  transaccion), y a `FAILED` junto con la sesion `FAILED`;
- `WAITING_FOR_USER -> SUCCEEDED` se materializa con dos saltos LEGALES en la
  misma transaccion (`WAITING_FOR_USER -> IN_PROGRESS` = el humano completo la
  accion, luego `IN_PROGRESS -> SUCCEEDED`);
- cancelacion/expiracion siguen siendo autoridad de `cancel_session`/
  `expire_session`: un resultado tardio sobre una sesion terminal se RECHAZA
  (jamas `CANCELLED/EXPIRED/FAILED -> SUCCEEDED`);
- un resultado identico ya aplicado es idempotente (200 sin mutar); uno
  conflictivo se rechaza.

Razon terminal: `sol_access_sessions.terminal_reason_code` (columna existente) +
metadata de la fila de auditoria `SESSION_FAILED`. Este servicio NO abre la
compuerta de entrega de credencial ni la toca."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction
from app.services import audit_service
from app.services import sol_access_session_service as session_service
from app.services import sol_agent_job_service as job_service


class BrowserResultRejectedError(Exception):
    """Resultado rechazado (estado/transicion ilegal, conflicto, sesion terminal,
    credencial nunca entregada). Mensaje generico: no es un oraculo."""


class BrowserResultNotFoundError(Exception):
    """Job inexistente, de otro agente/tenant o con sesion que no coincide
    (mismo error para todos los casos: sin oraculo)."""


@dataclass(frozen=True)
class BrowserResultOutcome:
    job_id: uuid.UUID
    job_status: str
    session_status: str
    idempotent: bool = False


_NON_TERMINAL_PRE_RESULT = {"WAITING_FOR_AGENT", "IN_PROGRESS", "WAITING_FOR_USER"}


def _promote_to_in_progress(db: Session, tenant_id: uuid.UUID, session_row):
    """`WAITING_FOR_AGENT|WAITING_FOR_USER -> IN_PROGRESS` (ambos legales)."""
    if session_row.status == "IN_PROGRESS":
        return session_row
    return session_service.start_progress(
        db, tenant_id=tenant_id, session_id=session_row.id, expected_version=session_row.version
    )


def apply_browser_result(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    job_id: uuid.UUID,
    claimed_session_id: uuid.UUID | None,
    status: str,
    reason_code: str | None,
) -> BrowserResultOutcome:
    try:
        session_row, job = job_service._lock_session_then_job(db, tenant_id=tenant_id, job_id=job_id)
    except job_service.JobNotFoundError:
        raise BrowserResultNotFoundError() from None
    if job.agent_id != agent_id or (claimed_session_id is not None and claimed_session_id != job.session_id):
        raise BrowserResultNotFoundError()

    sstatus = session_row.status

    if status == "WAITING_FOR_USER":
        if sstatus == "WAITING_FOR_USER" and job.status == "CLAIMED":
            return BrowserResultOutcome(job.id, job.status, sstatus, idempotent=True)
        if job.status != "CLAIMED" or sstatus not in {"WAITING_FOR_AGENT", "IN_PROGRESS"}:
            raise BrowserResultRejectedError()
        session_row = _promote_to_in_progress(db, tenant_id, session_row)
        session_row = session_service.report_waiting_for_user(
            db, tenant_id=tenant_id, session_id=session_row.id, expected_version=session_row.version
        )
        return BrowserResultOutcome(job.id, job.status, session_row.status)

    if status == "COMPLETED":
        if job.status == "COMPLETED" and sstatus == "SUCCEEDED":
            return BrowserResultOutcome(job.id, job.status, sstatus, idempotent=True)
        # Exito solo si la credencial fue realmente entregada a este job: un
        # HTTP 200 del agente nunca basta para declarar la sesion exitosa.
        if job.status != "CLAIMED" or job.credential_delivered_at is None or sstatus not in _NON_TERMINAL_PRE_RESULT:
            raise BrowserResultRejectedError()
        session_row = _promote_to_in_progress(db, tenant_id, session_row)
        session_row = session_service.report_succeeded(
            db, tenant_id=tenant_id, session_id=session_row.id, expected_version=session_row.version
        )
        job = job_service.complete_job(db, tenant_id=tenant_id, job_id=job.id, expected_version=job.version)
        audit_service.record(
            db,
            tenant_id=tenant_id,
            user_id=None,
            action=AuditAction.AUTH_SUCCEEDED,
            entity_type="sol_access_session",
            entity_id=session_row.id,
        )
        return BrowserResultOutcome(job.id, job.status, session_row.status)

    # FAILED (reason_code ya validado contra la lista cerrada por el esquema)
    assert status == "FAILED" and reason_code is not None
    if job.status == "FAILED" and sstatus == "FAILED":
        if session_row.terminal_reason_code == reason_code:
            return BrowserResultOutcome(job.id, job.status, sstatus, idempotent=True)
        raise BrowserResultRejectedError()  # resultado duplicado CONFLICTIVO
    if job.status != "CLAIMED" or sstatus not in _NON_TERMINAL_PRE_RESULT:
        raise BrowserResultRejectedError()
    session_row = session_service.report_failed(
        db, tenant_id=tenant_id, session_id=session_row.id, expected_version=session_row.version,
        terminal_reason_code=reason_code,
    )
    job = job_service.fail_job(db, tenant_id=tenant_id, job_id=job.id, expected_version=job.version)
    return BrowserResultOutcome(job.id, job.status, session_row.status)
