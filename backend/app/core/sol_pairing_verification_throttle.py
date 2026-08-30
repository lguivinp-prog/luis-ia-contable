"""Throttle process-local de intentos de VERIFICACION de un pairing ya
creado (Fase 5, Bloque 5.2, Domain C.1a/C.1b -- ver docs/phase5/block5_2/).

Capa SECUNDARIA y EXPLICITAMENTE DISTINTA de las otras dos ya existentes:

1. `sol_agent_pairing_rate_limit_service.py` -- basado en BD, limita la
   CREACION de pairings nuevos por (tenant_id, requested_by_user_id).
   Protege contra "crear pairings sin limite para resetear el presupuesto
   de intentos". NO protege contra una rafaga de intentos de codigo dentro
   de un MISMO pairing ya PENDING -- para eso existe este modulo.
2. `sol_agent_pairings.failed_attempt_count`/`status='LOCKED'` -- control
   DURABLE en PostgreSQL, autoridad final: al quinto fallo verificado, el
   pairing queda LOCKED de forma permanente. Este modulo NUNCA sustituye
   ese control ni lo relaja -- solo añade friccion ANTES de que un intento
   llegue a consumir ese contador durable.

Este modulo limita la VELOCIDAD de los intentos de verificacion contra el
MISMO pairing (`(tenant_id, pairing_id)` como clave -- nunca el codigo, el
verificador, ni `PAIRING_VERIFIER_KEY`). Usa el reloj MONOTONICO del
proceso (`time.monotonic()`), nunca `clock.db_now()`/`clock.utcnow()` --
esta capa no decide expiracion, autorizacion, ni estado terminal (Domain
C.1 Gate 2 sigue intacto: esas decisiones siguen gobernadas por tiempo de
PostgreSQL). Process-local: un reinicio del proceso backend limpia este
estado por completo -- aceptable porque el control durable vive en
PostgreSQL y sobrevive cualquier reinicio (ver `reset_all` mas abajo, solo
para pruebas).

Domain C.1b -- seguridad de recursos: `check_and_register_attempt` SOLO
debe invocarse para un pairing YA VALIDADO como existente/tenant-safe/
PENDING/no-vencido (ver `sol_agent_pairing_service.confirm_pairing`, que
reordeno la comprobacion del lookup ANTES del throttle) -- un `pairing_id`
arbitrario o inexistente nunca llega a registrar una entrada aqui. Ademas,
por si esa disciplina de llamada fallara en el futuro, este modulo NUNCA
depende exclusivamente de eso: purga entradas caducadas de forma perezosa
en cada llamada, y aplica una cota dura de cardinalidad (`_MAX_ENTRIES`)
independiente de cualquier purga."""

from __future__ import annotations

import threading
import time
import uuid

from app.core.config import get_settings

# Cota dura de cardinalidad -- ver `_evict_oldest_until_under_cap_locked`.
# Generoso para cualquier volumen realista de pairings simultaneos
# (recambio de agentes Windows de un estudio contable), pero finito: el
# diccionario NUNCA puede crecer mas alla de este numero de entradas,
# pase lo que pase con la disciplina de llamada de los callers.
_MAX_ENTRIES = 10_000

# Una entrada cuyo ultimo intento permitido fue hace mas de este umbral ya
# no aporta NINGUNA utilidad de throttling -- muy por encima de
# PAIRING_TTL_SECONDS=120 (Domain A.3) y de cualquier
# `pairing_verification_throttle_seconds` razonable: para cuando pasa este
# tiempo, el pairing real (si existia) ya vencio su TTL o ya fue resuelto
# (COMPLETED/LOCKED/CANCELLED). Purga perezosa, sin scheduler.
_STALE_AFTER_SECONDS = 300.0


class PairingVerificationRateLimitedError(Exception):
    """Nunca revela si el codigo presentado era correcto -- se lanza ANTES
    de invocar `verify_pairing_code`, reduciendo el oracle de tiempo/
    resultado durante la ventana de throttle (Domain C.1a, seccion 10)."""

    def __init__(self) -> None:
        super().__init__("SOL_PAIRING_VERIFICATION_RATE_LIMITED")
        self.code = "SOL_PAIRING_VERIFICATION_RATE_LIMITED"


_lock = threading.Lock()
_last_allowed_attempt_monotonic: dict[tuple[uuid.UUID, uuid.UUID], float] = {}


def _prune_stale_locked(now: float) -> None:
    """Debe llamarse ya dentro de `_lock`. Purga perezosa por antiguedad --
    ninguna entrada sobrevive mas de `_STALE_AFTER_SECONDS` sin un intento
    nuevo que la renueve."""
    stale_keys = [
        key for key, ts in _last_allowed_attempt_monotonic.items() if (now - ts) > _STALE_AFTER_SECONDS
    ]
    for key in stale_keys:
        del _last_allowed_attempt_monotonic[key]


def _evict_oldest_until_under_cap_locked() -> None:
    """Debe llamarse ya dentro de `_lock`. Cota dura, independiente de que
    la purga por antiguedad haya sido suficiente o no -- nunca depende
    exclusivamente de que los callers solo registren pairings validos."""
    while len(_last_allowed_attempt_monotonic) >= _MAX_ENTRIES:
        oldest_key = min(_last_allowed_attempt_monotonic, key=_last_allowed_attempt_monotonic.get)
        del _last_allowed_attempt_monotonic[oldest_key]


def check_and_register_attempt(*, tenant_id: uuid.UUID, pairing_id: uuid.UUID) -> None:
    """Lanza `PairingVerificationRateLimitedError` si el ultimo intento
    PERMITIDO para esta `(tenant_id, pairing_id)` ocurrio hace menos de
    `pairing_verification_throttle_seconds`. Una solicitud rechazada aqui
    NUNCA llega a `failed_attempt_count` -- este chequeo se ejecuta ANTES
    de invocar `verify_pairing_code` (Domain C.1a, seccion 9: 'no consumir
    failure al ser throttled'), pero DESPUES de que el llamador confirmo
    que el pairing existe, pertenece al tenant, y esta `PENDING`/no-vencido
    (Domain C.1b, seccion 3/5) -- un `pairing_id` desconocido o de otro
    tenant nunca debe llegar a esta funcion en absoluto.

    Umbral `<= 0` desactiva el throttle por completo (usado por las
    pruebas de concurrencia del quinto fallo, que deben ejercitar
    unicamente la invariante de BD -- Domain C.1a, seccion 12)."""
    cooldown = get_settings().pairing_verification_throttle_seconds
    if cooldown <= 0:
        return
    key = (tenant_id, pairing_id)
    now = time.monotonic()
    with _lock:
        _prune_stale_locked(now)
        last = _last_allowed_attempt_monotonic.get(key)
        if last is not None and (now - last) < cooldown:
            raise PairingVerificationRateLimitedError()
        _evict_oldest_until_under_cap_locked()
        _last_allowed_attempt_monotonic[key] = now


def evict(*, tenant_id: uuid.UUID, pairing_id: uuid.UUID) -> None:
    """Elimina explicitamente la entrada de un pairing que ACABA de volverse
    terminal (`COMPLETED`/`LOCKED`/`CANCELLED`/`EXPIRED`) -- ya no puede
    volver a verificarse, asi que su entrada de throttle es basura
    inmediata (Domain C.1b, seccion 6). No-op si no habia entrada. Llamado
    desde `sol_agent_pairing_service.py` en cada transicion terminal."""
    with _lock:
        _last_allowed_attempt_monotonic.pop((tenant_id, pairing_id), None)


def current_cardinality() -> int:
    """Solo para pruebas/observabilidad -- numero de entradas vivas."""
    with _lock:
        return len(_last_allowed_attempt_monotonic)


def reset_all() -> None:
    """Solo para pruebas -- simula un reinicio del proceso backend (Domain
    C.1a, seccion 13): limpia el throttle local sin tocar PostgreSQL."""
    with _lock:
        _last_allowed_attempt_monotonic.clear()
