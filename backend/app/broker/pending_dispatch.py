"""Puente process-local, de un solo uso, entre la creacion de un job (Domain
C, `sol_agent_job_service.create_job`, que devuelve el token en claro
EXACTAMENTE una vez) y el momento en que el agente correcto -- ya autenticado
por mTLS -- reclama ese job (Fase 5, Bloque 5.2, Domain D).

Decision de diseno (reportada explicitamente en el informe final de Domain
D): el token en claro NUNCA se serializa hacia el agente en absoluto. El
mensaje `JOB_AVAILABLE` (`protocol.py`) es solo metadata; cuando el agente
llama al protocolo de reclamo (`JOB_CLAIM_REQUEST`), el BROKER -- nunca el
agente -- presenta el token ya en su poder a `sol_agent_job_service.claim_job`
como autoridad exclusiva. Esto satisface la seccion 40 del encargo ("el token
solo puede transportarse como parte del protocolo de reclamo") de la forma
mas estricta posible: el token nunca cruza la red hacia el agente, nunca se
loguea, nunca se persiste mas alla de esta unica entrada en memoria de un
solo uso.

Bounded/evicting, mismo patron ya productivo que
`sol_pairing_verification_throttle.py` (Domain C.1b) y
`connection_registry.py` (Domain D): un `agent_id` sin pairing/dispatch
legitimo nunca puede crecer este diccionario sin cota, y una entrada nunca
sobrevive mas que `_STALE_AFTER_SECONDS` sin ser reclamada."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass

_MAX_ENTRIES = 10_000
# Generosamente por encima de JOB_TTL_SECONDS_DEFAULT (5 min, Domain C) --
# para cuando pasa este tiempo, el job real ya vencio su propio TTL en BD de
# todas formas (`claim_job` lo rechazaria por `expires_at` aunque esta
# entrada siguiera viva).
_STALE_AFTER_SECONDS = 600.0


@dataclass(frozen=True)
class _PendingEntry:
    job_id: uuid.UUID
    tenant_id: uuid.UUID
    raw_token: str
    registered_at_monotonic: float


class PendingDispatchStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_agent: dict[uuid.UUID, _PendingEntry] = {}

    def register(self, *, agent_id: uuid.UUID, tenant_id: uuid.UUID, job_id: uuid.UUID, raw_token: str) -> None:
        """Invocado por el flujo de despacho interno (`dispatch.py`)
        inmediatamente despues de `create_job` -- nunca por el agente ni por
        ningun llamador de red no confiable. Reemplaza cualquier entrada
        previa del mismo agente (a lo sumo 1 job activo por agente ya lo
        garantiza Domain C a nivel de BD -- este reemplazo es defensivo, no
        la barrera principal)."""
        now = time.monotonic()
        with self._lock:
            self._prune_stale_locked(now)
            self._evict_oldest_until_under_cap_locked(exclude=agent_id)
            self._by_agent[agent_id] = _PendingEntry(
                job_id=job_id, tenant_id=tenant_id, raw_token=raw_token, registered_at_monotonic=now
            )

    def peek_metadata(self, *, agent_id: uuid.UUID) -> uuid.UUID | None:
        """Solo el `job_id` -- usado por el endpoint de descubrimiento
        (`JOB_AVAILABLE`) para anunciar metadata sin consumir el token."""
        now = time.monotonic()
        with self._lock:
            self._prune_stale_locked(now)
            entry = self._by_agent.get(agent_id)
            return entry.job_id if entry is not None else None

    def pop_token_for_claim(self, *, agent_id: uuid.UUID, job_id: uuid.UUID) -> str | None:
        """Consume (elimina) la entrada SOLO si `job_id` coincide -- nunca
        entrega el token de un job distinto al que el agente esta
        reclamando. Un solo uso: una segunda llamada tras el pop no
        encuentra nada (el replay real de la operacion de reclamo lo maneja
        `claim_job` de Domain C, que ya rechaza un token reutilizado)."""
        now = time.monotonic()
        with self._lock:
            self._prune_stale_locked(now)
            entry = self._by_agent.get(agent_id)
            if entry is None or entry.job_id != job_id:
                return None
            del self._by_agent[agent_id]
            return entry.raw_token

    def discard(self, *, agent_id: uuid.UUID) -> None:
        """Limpieza explicita (ej. desconexion del agente, revocacion,
        apagado del broker) -- nunca deja un token en claro vivo mas tiempo
        del necesario."""
        with self._lock:
            self._by_agent.pop(agent_id, None)

    def current_cardinality(self) -> int:
        with self._lock:
            return len(self._by_agent)

    def clear(self) -> None:
        with self._lock:
            self._by_agent.clear()

    def _prune_stale_locked(self, now: float) -> None:
        stale = [aid for aid, e in self._by_agent.items() if (now - e.registered_at_monotonic) > _STALE_AFTER_SECONDS]
        for aid in stale:
            del self._by_agent[aid]

    def _evict_oldest_until_under_cap_locked(self, *, exclude: uuid.UUID) -> None:
        while len(self._by_agent) >= _MAX_ENTRIES:
            oldest = min(
                (aid for aid in self._by_agent if aid != exclude),
                key=lambda aid: self._by_agent[aid].registered_at_monotonic,
                default=None,
            )
            if oldest is None:
                return
            del self._by_agent[oldest]
