"""Reloj inyectable para pruebas (ver docs/DECISIONS/ADR-0008-injectable-clock.md).

Los modulos que necesitan tiempo controlable en tests importan este modulo y
llaman a `clock.utcnow()` (nunca `from app.core.clock import utcnow`), para
que el monkeypatch/override de las pruebas afecte la referencia usada.
"""

from collections.abc import Callable
from datetime import UTC, datetime


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
