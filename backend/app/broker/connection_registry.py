"""Registro de conexiones autenticadas del broker (Fase 5, Bloque 5.2, Domain
D -- ver docs/phase5/block5_2/). Process-local, en memoria -- un reinicio del
broker pierde este registro por completo, lo cual es ACEPTABLE (seccion 55
del encargo): el agente vuelve a conectar y reautenticar mTLS, y el estado
durable en PostgreSQL (Domain C, `sol_agents`/`sol_agent_jobs`) sigue siendo
la unica autoridad.

Mismo patron de seguridad de recursos ya productivo en
`app/core/sol_pairing_verification_throttle.py` (Domain C.1b): cota dura de
cardinalidad independiente de cualquier otra disciplina de llamada, mas
purga por conexion cerrada explicita. La diferencia deliberada es que AQUI
solo se registra una entrada tras una autenticacion mTLS ya completa (huella
de certificado -> `sol_agents` conocido y ACTIVE) -- una conexion sin
certificado, con certificado desconocido, o de un agente revocado NUNCA
llega a `register()` en absoluto (la capa TLS o `mtls.py` la rechazan antes).
La cota dura de este modulo es entonces defensa en profundidad, no la unica
barrera.

Politica de duplicados (seccion 27 del encargo, decision elegida y
documentada): una nueva conexion mTLS valida del MISMO agente REEMPLAZA la
entrada anterior -- `register()` devuelve el `connection_handle` anterior
(si existia) para que el llamador (`server.py`) cierre esa conexion fisica.
Nunca dos conexiones simultaneas ejecutando trabajo para el mismo agente."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass

# Cota dura, independiente del numero real de agentes ACTIVE -- generosa
# para cualquier volumen realista de un estudio contable, pero finita
# (mismo principio que `_MAX_ENTRIES` de la throttle de verificacion de
# pairing, Domain C.1b).
_MAX_ENTRIES = 10_000


@dataclass
class ConnectionRecord:
    """Contiene UNICAMENTE metadata de transporte -- nunca secretos,
    credenciales SOL, ni datos de la sesion SOL en si (seccion 27:
    "solo agent_id/tenant_id/handle/connected_at/last-activity/
    protocol-version")."""

    agent_id: uuid.UUID
    tenant_id: uuid.UUID
    connection_handle: str
    protocol_version: int
    connected_at: float
    last_activity_at: float
    active_job_id: uuid.UUID | None = None


class ConnectionRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_agent: dict[uuid.UUID, ConnectionRecord] = {}

    def register(
        self, *, agent_id: uuid.UUID, tenant_id: uuid.UUID, connection_handle: str, protocol_version: int
    ) -> str | None:
        """Registra la conexion y devuelve el `connection_handle` PREVIO de
        este agente si existia (para que el llamador lo cierre), o `None` si
        no habia ninguno. Nunca crea una entrada para un agente desconocido
        -- el llamador ya debio validar la identidad mTLS antes de invocar
        esto."""
        now = time.monotonic()
        with self._lock:
            previous = self._by_agent.get(agent_id)
            self._evict_oldest_until_under_cap_locked(exclude=agent_id)
            self._by_agent[agent_id] = ConnectionRecord(
                agent_id=agent_id,
                tenant_id=tenant_id,
                connection_handle=connection_handle,
                protocol_version=protocol_version,
                connected_at=now,
                last_activity_at=now,
            )
            return previous.connection_handle if previous is not None else None

    def touch(self, *, agent_id: uuid.UUID) -> None:
        """Actualiza `last_activity_at` en cada mensaje autenticado
        (heartbeat, claim, status, etc.) -- no-op si el agente no esta
        registrado (ej. la conexion ya fue reemplazada/cerrada)."""
        with self._lock:
            record = self._by_agent.get(agent_id)
            if record is not None:
                record.last_activity_at = time.monotonic()

    def set_active_job(self, *, agent_id: uuid.UUID, job_id: uuid.UUID | None) -> None:
        """Rastrea el job que este agente cree activo -- usado UNICAMENTE
        para decidir si el heartbeat debe incluir un aviso secundario de
        cancelacion (`SessionCancelNotice`, `protocol.py`). Nunca autoritativo
        por si solo: la fila real en `sol_agent_jobs` sigue siendo la unica
        fuente de verdad."""
        with self._lock:
            record = self._by_agent.get(agent_id)
            if record is not None:
                record.active_job_id = job_id

    def validate_and_touch(
        self, *, agent_id: uuid.UUID, connection_id: str, idle_timeout_seconds: float
    ) -> bool:
        """Fase 5, Bloque 5.2, Domain D.1, Gate 2: valida que
        `connection_id` (presentado por el llamador en el header
        `X-Sol-Connection-Id`) coincida EXACTAMENTE con el
        `connection_handle` actual de este agente, Y que la conexion no
        haya excedido `idle_timeout_seconds` desde su ultima actividad --
        ninguna de las dos condiciones sola es suficiente. Devuelve `False`
        de forma generica ante CUALQUIER motivo de rechazo (sin entrada, id
        no coincide, o vencida por inactividad) -- nunca distingue el
        motivo especifico ante el llamador de red (mismo principio de
        no-oracle ya aplicado en Domain C). Una entrada vencida por
        inactividad se elimina inmediatamente (nunca queda "zombie" en el
        registro esperando una purga posterior)."""
        now = time.monotonic()
        with self._lock:
            record = self._by_agent.get(agent_id)
            if record is None:
                return False
            if (now - record.last_activity_at) > idle_timeout_seconds:
                del self._by_agent[agent_id]
                return False
            if record.connection_handle != connection_id:
                return False
            record.last_activity_at = now
            return True

    def unregister(self, *, agent_id: uuid.UUID, connection_handle: str) -> None:
        """Elimina la entrada SOLO si el handle coincide -- evita que un
        `unregister` tardio de una conexion ya reemplazada borre por error la
        entrada de la conexion NUEVA (carrera desconexion-vieja vs
        reemplazo)."""
        with self._lock:
            record = self._by_agent.get(agent_id)
            if record is not None and record.connection_handle == connection_handle:
                del self._by_agent[agent_id]

    def force_remove(self, *, agent_id: uuid.UUID) -> None:
        """Elimina la entrada del agente sin importar el handle -- usado
        cuando la capa mTLS descubre que un agente antes conectado ahora
        esta REVOKED (seccion 54 del encargo: "el siguiente mensaje debe
        revalidar el estado y cerrar"). A diferencia de `unregister`, nunca
        protege contra una carrera con una conexion de reemplazo mas nueva
        -- solo se invoca desde la ruta de revocacion, donde CUALQUIER
        conexion viva de este agente debe terminar."""
        with self._lock:
            self._by_agent.pop(agent_id, None)

    def is_connected(self, *, agent_id: uuid.UUID) -> bool:
        with self._lock:
            return agent_id in self._by_agent

    def get(self, *, agent_id: uuid.UUID) -> ConnectionRecord | None:
        with self._lock:
            return self._by_agent.get(agent_id)

    def current_cardinality(self) -> int:
        with self._lock:
            return len(self._by_agent)

    def all_handles(self) -> list[str]:
        """Usado en apagado ordenado (`server.py`) para cerrar todas las
        conexiones activas antes de limpiar el registro."""
        with self._lock:
            return [record.connection_handle for record in self._by_agent.values()]

    def clear(self) -> None:
        """Apagado ordenado (seccion 55) o reinicio simulado en pruebas --
        nunca muta estado durable, solo el registro process-local."""
        with self._lock:
            self._by_agent.clear()

    def _evict_oldest_until_under_cap_locked(self, *, exclude: uuid.UUID) -> None:
        while len(self._by_agent) >= _MAX_ENTRIES:
            oldest_agent_id = min(
                (aid for aid in self._by_agent if aid != exclude),
                key=lambda aid: self._by_agent[aid].last_activity_at,
                default=None,
            )
            if oldest_agent_id is None:
                return
            del self._by_agent[oldest_agent_id]
