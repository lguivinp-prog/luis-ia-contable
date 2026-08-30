"""Segunda capa de defensa en profundidad contra fuerza bruta de pairing
(Fase 5, Bloque 5.2, Domain C.1, Gate 3 -- ver docs/phase5/block5_2/).

El lock durable de 5 fallos en `sol_agent_pairings.failed_attempt_count`
(Domain A/A.1/A.2) protege un REGISTRO de pairing ya creado. No impide, por
si solo, que alguien cree registros NUEVOS sin limite -- cada pairing nuevo
trae su propio presupuesto fresco de 5 intentos. Este modulo limita la
CREACION de pairings por (tenant, usuario solicitante), reutilizando el
MISMO patron ya productivo de `login_attempt_service.py` (ADR-0009): conteo
de filas ya persistidas + backoff exponencial -- sin tabla nueva (reutiliza
`sol_agent_pairings.created_at`/`status` como bitacora), sin Redis, sin
infraestructura distribuida. Esta capa NUNCA sustituye al lock durable de 5
fallos -- es estrictamente adicional."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.repositories import sol_agent_access_repository as repo

settings = get_settings()


@dataclass(frozen=True)
class PairingCreationLockoutStatus:
    is_locked: bool
    locked_until: datetime | None
    recent_count: int


def get_pairing_creation_lockout_status(
    db: Session, *, tenant_id: uuid.UUID, requested_by_user_id: uuid.UUID
) -> PairingCreationLockoutStatus:
    """Puro respecto a la base de datos: solo lee filas ya persistidas,
    nunca escribe (mismo contrato que `login_attempt_service.get_lockout_status`).
    El contador se reinicia tras el ULTIMO pairing `COMPLETED` de este
    (tenant, usuario) -- igual que el conteo de login se reinicia tras el
    ultimo exito."""
    now = clock.db_now(db)
    last_success_at = repo.get_last_completed_pairing_created_at(
        db, tenant_id=tenant_id, requested_by_user_id=requested_by_user_id
    )
    recent_count = repo.count_pairings_created_since(
        db, tenant_id=tenant_id, requested_by_user_id=requested_by_user_id, since=last_success_at
    )

    if recent_count < settings.pairing_creation_max_attempts:
        return PairingCreationLockoutStatus(is_locked=False, locked_until=None, recent_count=recent_count)

    overflow = recent_count - settings.pairing_creation_max_attempts
    cycle = overflow // settings.pairing_creation_max_attempts
    duration_minutes = min(
        settings.pairing_creation_lockout_duration_minutes * (2**cycle),
        settings.pairing_creation_lockout_max_minutes,
    )
    most_recent_created_at = repo.get_most_recent_pairing_created_at(
        db, tenant_id=tenant_id, requested_by_user_id=requested_by_user_id
    )
    assert most_recent_created_at is not None  # recent_count > 0 lo garantiza
    locked_until = most_recent_created_at + timedelta(minutes=duration_minutes)

    if now >= locked_until:
        return PairingCreationLockoutStatus(is_locked=False, locked_until=None, recent_count=recent_count)
    return PairingCreationLockoutStatus(is_locked=True, locked_until=locked_until, recent_count=recent_count)
