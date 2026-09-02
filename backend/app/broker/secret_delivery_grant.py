"""Registro process-local de autorizaciones de entrega de secreto SOL (Fase
5, Bloque 5.2, Domain E1-B -- ver
docs/phase5/block5_2/BLOCK5_2_SECRET_DELIVERY_ARCHITECTURE.md secciones
5/5.4/5.5, congeladas en Domain E1-A/E1-A.1). Mismo patron de seguridad de
recursos ya productivo en `app/core/sol_pairing_verification_throttle.py`
(Domain C.1b) y `app/broker/connection_registry.py`/`pending_dispatch.py`
(Domain D): cota dura de cardinalidad, purga perezosa de entradas vencidas,
evicciones explicitas en transiciones terminales -- nunca persistido en
PostgreSQL, Redis, ni archivo alguno.

PostgreSQL (`credential_delivered_at`, Domain C) sigue siendo la autoridad
final para "a lo sumo una entrega por sesion" -- este registro es defensa en
profundidad UNICAMENTE: reduce descifrados desperdiciados bajo carreras,
nunca sustituye el gate de BD."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, replace

_MAX_ENTRIES = 10_000

# SECRET_DELIVERY_GRANT_TTL_SECONDS, congelado en Domain E1-A (sección 5.2
# del documento de arquitectura) -- limite superior; el TTL EFECTIVO real
# tambien esta acotado por la expiracion de sesion/job (aplicado por el
# llamador, `sol_agent_credential_delivery_service.py`, nunca aqui).
DEFAULT_GRANT_TTL_SECONDS = 60.0


class GrantState:
    """`CONSUMED`/`INVALIDATED` son terminales y NUNCA se almacenan como
    valor de `state` -- alcanzarlos elimina la entrada del registro de
    inmediato (mismo principio que el resto de los registros process-local
    de este paquete: terminal = ausente, nunca un tombstone)."""

    AUTHORIZED = "AUTHORIZED"
    IN_FLIGHT = "IN_FLIGHT"


@dataclass(frozen=True)
class SecretDeliveryGrant:
    """Metadata NO-secreta unicamente (seccion 5 del documento de
    arquitectura): nunca `sol_username`/`sol_password`/`ruc`/
    `vault_root_key`/`credential_dek`/material criptografico alguno."""

    tenant_id: uuid.UUID
    session_id: uuid.UUID
    job_id: uuid.UUID
    agent_id: uuid.UUID
    user_id: uuid.UUID
    app_session_id: uuid.UUID
    credential_id: uuid.UUID
    state: str
    created_at: float
    expires_at: float


class SecretDeliveryGrantRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_key: dict[tuple[uuid.UUID, uuid.UUID], SecretDeliveryGrant] = {}

    def authorize(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        job_id: uuid.UUID,
        agent_id: uuid.UUID,
        user_id: uuid.UUID,
        app_session_id: uuid.UUID,
        credential_id: uuid.UUID,
        ttl_seconds: float = DEFAULT_GRANT_TTL_SECONDS,
    ) -> None:
        """Crea (o REEMPLAZA, nunca duplica) el grant `AUTHORIZED` para
        `(tenant_id, job_id)` -- SOLO debe invocarse despues de que el
        registro DURABLE de auditoria (`CREDENTIAL_DELIVERY_AUTHORIZED`)
        ya comprometio en PostgreSQL (seccion 20.1 del documento de
        arquitectura, `AUDIT_AND_PROCESS_LOCAL_GRANT_ATOMIC_TRANSACTION=
        NO` -- la disciplina de orden es responsabilidad exclusiva del
        llamador, este metodo no la impone ni puede imponerla)."""
        now = time.monotonic()
        key = (tenant_id, job_id)
        with self._lock:
            self._prune_stale_locked(now)
            self._evict_oldest_until_under_cap_locked(exclude=key)
            self._by_key[key] = SecretDeliveryGrant(
                tenant_id=tenant_id,
                session_id=session_id,
                job_id=job_id,
                agent_id=agent_id,
                user_id=user_id,
                app_session_id=app_session_id,
                credential_id=credential_id,
                state=GrantState.AUTHORIZED,
                created_at=now,
                expires_at=now + ttl_seconds,
            )

    def try_acquire_in_flight(
        self, *, tenant_id: uuid.UUID, job_id: uuid.UUID, agent_id: uuid.UUID
    ) -> SecretDeliveryGrant | None:
        """Adquisicion atomica `AUTHORIZED -> IN_FLIGHT` (seccion 5.4,
        Gate 3 de Domain E1-A.1): compare-and-swap bajo el mismo lock que
        protege el resto del registro. Devuelve una COPIA inmutable de los
        datos del grant si la adquisicion tuvo exito, o `None` ante
        CUALQUIER motivo de rechazo (sin entrada, vencido, ya
        `IN_FLIGHT`, o `agent_id` no coincide) -- nunca distingue el
        motivo (no-oracle, mismo principio que `JobClaimInvalidError` de
        Domain C). Un rechazo por `agent_id` incorrecto NUNCA muta ni
        elimina la entrada -- el grant legitimo de otro agente permanece
        intacto (`WRONG_AGENT_CAN_INVALIDATE_LEGITIMATE_GRANT=NO`, seccion
        7 del encargo de Domain E1-A.1). Una clave desconocida NUNCA crea
        estado."""
        now = time.monotonic()
        key = (tenant_id, job_id)
        with self._lock:
            grant = self._by_key.get(key)
            if grant is None:
                return None
            if grant.expires_at <= now:
                del self._by_key[key]
                return None
            if grant.state != GrantState.AUTHORIZED:
                return None
            if grant.agent_id != agent_id:
                return None
            updated = replace(grant, state=GrantState.IN_FLIGHT)
            self._by_key[key] = updated
            return updated

    def mark_consumed(self, *, tenant_id: uuid.UUID, job_id: uuid.UUID) -> None:
        """`IN_FLIGHT -> CONSUMED`: SOLO debe invocarse tras un commit
        exitoso del gate de BD de Domain C (seccion 5.4) -- eliminacion
        irrevocable, nunca un retorno a `AUTHORIZED`
        (`FAILED_IN_FLIGHT_GRANT_RETURNS_TO_AUTHORIZED=NO`)."""
        with self._lock:
            self._by_key.pop((tenant_id, job_id), None)

    def invalidate(self, *, tenant_id: uuid.UUID, job_id: uuid.UUID) -> None:
        """`IN_FLIGHT -> INVALIDATED` (o eliminacion directa de un
        `AUTHORIZED` ante un fallo de seguridad terminal, ej. agente
        revocado) -- eliminacion irrevocable, nunca un retorno a
        `AUTHORIZED`."""
        with self._lock:
            self._by_key.pop((tenant_id, job_id), None)

    def peek(self, *, tenant_id: uuid.UUID, job_id: uuid.UUID) -> SecretDeliveryGrant | None:
        """Solo para pruebas/observabilidad -- NUNCA usado por la logica
        real de autorizacion (que debe pasar siempre por
        `try_acquire_in_flight`, la unica via de lectura-y-mutacion
        atomica)."""
        with self._lock:
            return self._by_key.get((tenant_id, job_id))

    def current_cardinality(self) -> int:
        with self._lock:
            return len(self._by_key)

    def clear(self) -> None:
        """Apagado/reinicio del broker (seccion 6 del documento de
        arquitectura: process-local, desaparece por completo) o pruebas."""
        with self._lock:
            self._by_key.clear()

    def _prune_stale_locked(self, now: float) -> None:
        stale = [key for key, grant in self._by_key.items() if grant.expires_at <= now]
        for key in stale:
            del self._by_key[key]

    def _evict_oldest_until_under_cap_locked(self, *, exclude: tuple[uuid.UUID, uuid.UUID]) -> None:
        while len(self._by_key) >= _MAX_ENTRIES:
            oldest_key = min(
                (key for key in self._by_key if key != exclude),
                key=lambda key: self._by_key[key].created_at,
                default=None,
            )
            if oldest_key is None:
                return
            del self._by_key[oldest_key]
