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

# Domain E1-B.1, Gate B (cierre de `SECRET_LEASE_TIMEOUT_FREEZE_GAP`):
# limite superior REAL y acotado del lease de entrega de secreto,
# independiente de que el poseedor llegue o no a su bloque `finally`. Se
# ratifica reutilizando el MISMO valor ya congelado y verificado
# empiricamente en Domain D.1 para el timeout de lectura/escritura de
# socket del broker (`get_settings().sol_broker_read_timeout_seconds`,
# `app/core/config.py`, default 15.0s) -- `BrokerServer` pasa ese valor
# explicitamente al construir su `ConnectionRegistry` (ver
# `app/broker/server.py`). Esta constante es SOLO el default de respaldo
# para instancias de `ConnectionRegistry` creadas sin ese parametro
# explicito (ej. codigo antiguo/pruebas aisladas) -- nunca un segundo
# numero independiente inventado.
DEFAULT_SECRET_LEASE_TIMEOUT_SECONDS = 15.0


class ConnectionBusyError(Exception):
    """Fase 5, Bloque 5.2, Domain E1-B (ver
    docs/phase5/block5_2/BLOCK5_2_SECRET_DELIVERY_ARCHITECTURE.md seccion
    8; refinamiento operativo del encargo de Domain E1-B, secciones
    23-27): un `HELLO` de reemplazo NUNCA puede sustituir la conexion de
    un agente mientras esta tiene un lease de entrega de secreto activo
    -- se rechaza con un codigo estable en vez de la sustitucion
    silenciosa `NEW_REPLACES_OLD` normal, que sigue vigente fuera de esta
    ventana critica y acotada."""


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
    # Domain E1-B: contador de leases de entrega de secreto ACTUALMENTE en
    # vuelo sobre esta conexion logica (seccion 8 del documento de
    # arquitectura). Deliberadamente un CONTADOR, no un token unico
    # sobreescribible -- dos peticiones HTTP concurrentes y legitimas
    # pueden compartir la MISMA conexion logica (mismo `connection_handle`,
    # dos sockets fisicos distintos), y un token unico sobreescrito por la
    # segunda haria fallar la revalidacion final de la PRIMERA aunque
    # ganara la carrera del grant -- un rechazo fantasma. La identidad que
    # se revalida al final es el propio `connection_handle` (ya rotado
    # atomicamente por `register()` ante un HELLO de reemplazo real), no
    # un valor aparte.
    secret_lease_count: int = 0
    # Domain E1-B.1, Gate B: momento monotonico limite hasta el cual el
    # lease actual (secret_lease_count > 0) se considera legitimamente en
    # vuelo -- `None` cuando no hay lease activo. Se REFRESCA (nunca se
    # acumula) en cada `pin()` exitoso, representando "la razon conocida
    # mas reciente para seguir considerando el lease vivo". Si este plazo
    # se vence sin una liberacion normal (`release_lease` nunca llamado --
    # ej. el hilo poseedor se atasca antes de su `finally`), el lease se
    # autopurga de forma perezosa en el proximo acceso (mismo patron ya
    # productivo que la purga de inactividad de `_validate_and_touch_
    # locked`) -- nunca depende UNICAMENTE de que el poseedor original
    # llegue a liberar.
    secret_lease_deadline: float | None = None


class ConnectionRegistry:
    def __init__(self, *, secret_lease_timeout_seconds: float = DEFAULT_SECRET_LEASE_TIMEOUT_SECONDS) -> None:
        self._lock = threading.Lock()
        self._by_agent: dict[uuid.UUID, ConnectionRecord] = {}
        self._secret_lease_timeout_seconds = secret_lease_timeout_seconds

    def register(
        self, *, agent_id: uuid.UUID, tenant_id: uuid.UUID, connection_handle: str, protocol_version: int
    ) -> str | None:
        """Registra la conexion y devuelve el `connection_handle` PREVIO de
        este agente si existia (para que el llamador lo cierre), o `None` si
        no habia ninguno. Nunca crea una entrada para un agente desconocido
        -- el llamador ya debio validar la identidad mTLS antes de invocar
        esto.

        Domain E1-B: si la conexion previa tiene uno o mas leases de
        entrega de secreto activos (`secret_lease_count > 0`), el
        reemplazo se RECHAZA (`ConnectionBusyError`) en vez de
        completarse -- la politica general `NEW_REPLACES_OLD` (Domain
        D.1) queda en pausa UNICAMENTE durante esa ventana acotada y
        critica; fuera de ella, el comportamiento es identico al ya
        congelado.

        Domain E1-B.1, Gate B: un lease ABANDONADO (su poseedor nunca
        llego a `release_lease`, ej. un hilo atascado) NUNCA bloquea un
        HELLO para siempre -- `_lease_active_locked` purga por su cuenta
        cualquier lease cuyo plazo ya vencio antes de decidir si este
        `register()` debe rechazarse."""
        now = time.monotonic()
        with self._lock:
            previous = self._by_agent.get(agent_id)
            if previous is not None and self._lease_active_locked(previous, now):
                raise ConnectionBusyError()
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

    def _validate_and_touch_locked(
        self, *, agent_id: uuid.UUID, connection_id: str, idle_timeout_seconds: float, now: float
    ) -> ConnectionRecord | None:
        """Debe llamarse ya bajo `self._lock`. Compartida por
        `validate_and_touch` y `pin` (Domain E1-B) para nunca duplicar esta
        logica -- ver el docstring de `validate_and_touch` para la
        semantica completa."""
        record = self._by_agent.get(agent_id)
        if record is None:
            return None
        if (now - record.last_activity_at) > idle_timeout_seconds:
            del self._by_agent[agent_id]
            return None
        if record.connection_handle != connection_id:
            return None
        record.last_activity_at = now
        return record

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
            return self._validate_and_touch_locked(
                agent_id=agent_id, connection_id=connection_id, idle_timeout_seconds=idle_timeout_seconds, now=now
            ) is not None

    def pin(
        self, *, agent_id: uuid.UUID, connection_id: str, idle_timeout_seconds: float
    ) -> str | None:
        """Domain E1-B (seccion 8 del documento de arquitectura, secciones
        23-25 del encargo): fija ("pin") la conexion logica actual para la
        ventana critica de entrega de secreto. Reutiliza la MISMA
        validacion que `validate_and_touch` (nunca la duplica) y, si es
        valida, incrementa el contador de leases en vuelo y devuelve el
        `connection_handle` ACTUAL como identidad del lease -- no un token
        aparte generado por peticion. Devuelve `None` si la conexion no es
        valida en este instante (mismo criterio no-oracle).

        Deliberadamente sin token propio (correccion de diseno tras
        revision de la prueba de concurrencia real, Domain E1-B): dos
        peticiones HTTP concurrentes y legitimas del MISMO agente pueden
        compartir la MISMA conexion logica (dos sockets fisicos, un solo
        `connection_handle`). Un token unico por peticion, sobreescrito
        por la segunda `pin()`, haria que la revalidacion final de la
        PRIMERA fallara aunque hubiera ganado limpiamente la carrera del
        grant -- un rechazo fantasma que viola X/Y del plan de pruebas.
        El `connection_handle` en si ya es de alta entropia (Domain D) y
        SOLO cambia cuando `register()` crea una entrada nueva (HELLO de
        reemplazo real) -- exactamente la unica condicion que debe
        invalidar un lease.

        Limite de tiempo del lease (seccion 26 del encargo E1-B,
        `SECRET_LEASE_TIMEOUT_FREEZE_GAP` -- CERRADO en Domain E1-B.1,
        Gate B): el documento de arquitectura solo congelo una cota
        CUALITATIVA ("corto, del orden de segundos, nunca el TTL completo
        del grant de 60s"). Domain E1-B razono (incorrectamente, hallazgo
        de E1-B.1) que el timeout de lectura/escritura de socket
        (`sol_broker_read_timeout_seconds`) bastaba como limite PRACTICO
        porque "el lease siempre se libera en un finally" -- eso protege
        el I/O del socket, pero NUNCA un atasco posterior al read (ej. un
        deadlock de BD/crypto, o cualquier excepcion que por algun motivo
        rompiera el propio `finally`). Ahora el lease tiene un plazo
        (`secret_lease_deadline`) EXPLICITO, ratificado en el mismo valor
        (`ConnectionRegistry._secret_lease_timeout_seconds`, poblado por
        `BrokerServer` desde `sol_broker_read_timeout_seconds`), pero
        ENFORZADO independientemente de que el poseedor original llegue o
        no a `release_lease`."""
        now = time.monotonic()
        with self._lock:
            record = self._validate_and_touch_locked(
                agent_id=agent_id, connection_id=connection_id, idle_timeout_seconds=idle_timeout_seconds, now=now
            )
            if record is None:
                return None
            record.secret_lease_count += 1
            record.secret_lease_deadline = now + self._secret_lease_timeout_seconds
            return record.connection_handle

    def _lease_active_locked(self, record: ConnectionRecord, now: float) -> bool:
        """Debe llamarse ya bajo `self._lock`. Domain E1-B.1, Gate B:
        UNICO punto de verdad sobre si un lease sigue vigente -- purga por
        su cuenta (autopurga perezosa, mismo patron que el vencimiento por
        inactividad de `_validate_and_touch_locked`) cualquier lease cuyo
        plazo ya paso, sin depender de que nadie mas lo libere
        explicitamente."""
        if record.secret_lease_count <= 0:
            return False
        if record.secret_lease_deadline is not None and record.secret_lease_deadline <= now:
            record.secret_lease_count = 0
            record.secret_lease_deadline = None
            return False
        return True

    def revalidate_lease(self, *, agent_id: uuid.UUID, lease_token: str) -> bool:
        """Domain E1-B, paso 13 del orden exacto de entrega (revalidacion
        final antes de escribir la respuesta): confirma que la conexion
        logica presentada al pin sigue siendo la vigente -- compara contra
        el `connection_handle` ACTUAL de la entrada, que solo cambia
        cuando `register()` crea una entrada nueva. Si un `HELLO` nunca
        pudo reemplazar la conexion mientras el lease estaba activo
        (`ConnectionBusyError` en `register`), la unica forma de que esto
        devuelva `False` es que la entrada haya sido removida/reemplazada
        por otra via (ej. `force_remove` por revocacion, vencimiento por
        inactividad, o apagado del broker) -- exactamente los casos que
        deben impedir la escritura del secreto.

        Domain E1-B.1, Gate B: TAMBIEN devuelve `False` si el plazo del
        lease ya se vencio (`EXPIRED_SECRET_LEASE_CAN_WRITE_SECRET=NO`) --
        una peticion que sobrevivio a su propio lease NUNCA debe poder
        escribir el secreto, incluso si por cualquier motivo ninguna otra
        conexion llego a reemplazarla mientras tanto. Falla cerrado."""
        now = time.monotonic()
        with self._lock:
            record = self._by_agent.get(agent_id)
            if record is None:
                return False
            if record.connection_handle != lease_token:
                return False
            if record.secret_lease_deadline is not None and record.secret_lease_deadline <= now:
                return False
            return True

    def release_lease(self, *, agent_id: uuid.UUID, lease_token: str) -> None:
        """Libera el lease -- SIEMPRE invocado desde un `finally` en
        `server.py`, exito o error (seccion 25 del encargo: "Release
        lease in finally"). Decrementa el contador solo si la entrada
        sigue siendo la MISMA conexion logica que emitio el lease
        (`connection_handle == lease_token`); no-op en cualquier otro caso
        -- evita que una liberacion tardia de una conexion ya reemplazada
        toque por error el contador de la conexion NUEVA (mismo principio
        que `unregister`). Idempotente/seguro incluso si ya llego a 0 (no
        decrementa por debajo de cero). Este `finally` sigue siendo la via
        NORMAL de liberacion (rapida, sin esperar el plazo completo) -- el
        plazo de `pin()` es solo el respaldo para el caso ABANDONADO
        (Gate B), nunca un reemplazo de esta liberacion inmediata."""
        with self._lock:
            record = self._by_agent.get(agent_id)
            if record is not None and record.connection_handle == lease_token and record.secret_lease_count > 0:
                record.secret_lease_count -= 1
                if record.secret_lease_count == 0:
                    record.secret_lease_deadline = None

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
