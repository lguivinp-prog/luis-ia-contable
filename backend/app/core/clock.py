"""Reloj inyectable para pruebas (ver docs/DECISIONS/ADR-0008-injectable-clock.md).

Los modulos que necesitan tiempo controlable en tests importan este modulo y
llaman a `clock.utcnow()` (nunca `from app.core.clock import utcnow`), para
que el monkeypatch/override de las pruebas afecte la referencia usada.

`db_now()` (Domain C.1, Gate 2 -- ver docs/phase5/block5_2/) es deliberadamente
DISTINTO: nunca es sesgable por `set_clock()`, porque consulta el reloj real
de PostgreSQL dentro de la transaccion en curso (`SELECT now()`), no el
proceso Python. Para decisiones de expiracion/seguridad congeladas como
"tiempo de base de datos autoritativo" (Domain A.1), usar SIEMPRE `db_now(db)`
en vez de `utcnow()` -- `utcnow()` sigue siendo valido para timestamps
puramente informativos/no criticos donde el patron productivo ya existente
del repo (`sol_vault_service.py`, `tax_payment_service.py`, etc.) lo usa.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _system_now() -> datetime:
    return datetime.now(UTC)


_current: Callable[[], datetime] = _system_now


def utcnow() -> datetime:
    return _current()


def set_clock(fn: Callable[[], datetime]) -> None:
    global _current
    _current = fn


def reset_clock() -> None:
    global _current
    _current = _system_now


def db_now(db: "Session") -> datetime:
    """Tiempo autoritativo de PostgreSQL (`SELECT now()`) dentro de la
    transaccion actual de `db` -- nunca el reloj del proceso backend, del
    agente Windows, ni del frontend. Nunca afectado por `set_clock()`.

    Usar para toda decision de expiracion/seguridad critica sobre
    `sol_access_sessions`/`sol_agent_jobs`/`sol_agent_pairings` (Domain C.1,
    Gate 2): expiracion de pairing/sesion/job, validacion de vencimiento en
    claim, confirmacion de pairing, y entrega de credencial."""
    from sqlalchemy import func, select

    return db.execute(select(func.now())).scalar_one()
