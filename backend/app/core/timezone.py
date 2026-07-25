"""Helper centralizado de zona horaria (Fase 2, Bloque 1, Seccion 12 del
plan / ADR-0060). Unico punto que interpreta `settings.timezone` -- ningun
otro servicio debe instanciar `ZoneInfo("America/Lima")` por su cuenta.
Toda conversion de fecha/hora local a instante UTC absoluto (`due_at`,
`starts_at`, limites de balde de agenda) pasa exclusivamente por aqui."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core.config import get_settings

_settings = get_settings()

if _settings.timezone != "America/Lima":
    raise NotImplementedError(
        f"Zona horaria '{_settings.timezone}' no soportada -- solo 'America/Lima' "
        "esta implementada en este bloque (ver app/core/timezone.py)."
    )

LIMA_ZONE = ZoneInfo("America/Lima")


def combine_local_to_utc(d: date, t: time | None) -> tuple[datetime, bool]:
    """Convierte una fecha (+ hora opcional) en zona Lima a un instante UTC
    absoluto. Sin hora explicita -> fin del dia (23:59:59) Lima. Devuelve el
    par `(instante_utc, due_has_time)` -- `due_has_time` es siempre derivado
    de si `t` fue provisto, nunca aceptado directamente del cliente."""
    if t is None:
        return _combine_end_of_day(d), False
    local_dt = datetime(d.year, d.month, d.day, t.hour, t.minute, t.second, tzinfo=LIMA_ZONE)
    return local_dt.astimezone(ZoneInfo("UTC")), True


def _combine_end_of_day(d: date) -> datetime:
    local_dt = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=LIMA_ZONE)
    return local_dt.astimezone(ZoneInfo("UTC"))


def start_of_day_utc(d: date) -> datetime:
    local_dt = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=LIMA_ZONE)
    return local_dt.astimezone(ZoneInfo("UTC"))


def end_of_day_utc(d: date) -> datetime:
    return _combine_end_of_day(d)


def today_lima(now_utc: datetime) -> date:
    return now_utc.astimezone(LIMA_ZONE).date()


def next_n_days_end_utc(d: date, n: int) -> datetime:
    return end_of_day_utc(d + timedelta(days=n))
