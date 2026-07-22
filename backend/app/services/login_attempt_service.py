"""Bloqueo por intentos fallidos de login (ver ADR-0009).

get_lockout_status() es puro respecto a la base de datos: solo lee filas ya
persistidas, nunca escribe. record_attempt() solo debe llamarse cuando el
identificador NO esta bloqueado (un intento bloqueado no cuenta para el
calculo, ver app/routers/auth.py).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.repositories import login_attempt_repository

settings = get_settings()


@dataclass(frozen=True)
class LockoutStatus:
    is_locked: bool
    locked_until: datetime | None
    consecutive_failures: int


def get_lockout_status(db: Session, identifier: str) -> LockoutStatus:
    now = clock.utcnow()
    last_success_at = login_attempt_repository.get_last_success_at(db, identifier)
    failures = login_attempt_repository.get_failures_since(db, identifier, last_success_at)
    consecutive_failures = len(failures)

    if consecutive_failures < settings.login_max_attempts:
        return LockoutStatus(is_locked=False, locked_until=None, consecutive_failures=consecutive_failures)

    overflow = consecutive_failures - settings.login_max_attempts
    cycle = overflow // settings.login_max_attempts
    duration_minutes = min(
        settings.login_lockout_duration_minutes * (2**cycle),
        settings.login_lockout_max_minutes,
    )
    last_failure_at = failures[-1].attempted_at
    locked_until = last_failure_at + timedelta(minutes=duration_minutes)

    if now >= locked_until:
        return LockoutStatus(is_locked=False, locked_until=None, consecutive_failures=consecutive_failures)

    return LockoutStatus(is_locked=True, locked_until=locked_until, consecutive_failures=consecutive_failures)


def record_attempt(
    db: Session,
    *,
    identifier: str,
    success: bool,
    tenant_id: uuid.UUID | None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    login_attempt_repository.record(
        db,
        tenant_id=tenant_id,
        identifier=identifier,
        success=success,
        attempted_at=clock.utcnow(),
        ip_address=ip_address,
        user_agent=user_agent,
    )
