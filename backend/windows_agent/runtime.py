"""Fase 5, Bloque 5.2, Domain E2 -- maquina de estados y bucle de jobs del
Agente en ejecucion (`run`). Estados locales minimos (seccion 67 del
encargo) -- NUNCA un enum/migracion de BD: el backend sigue siendo la
unica autoridad de `sol_agents.status` (seccion 68, ver Domain C); estos
estados son puramente locales/en memoria, para que el proceso sepa que
hacer a continuacion.

INVARIANTE CRITICO (secciones 1/39 del encargo,
`AGENT_AUTO_REQUESTS_SOL_CREDENTIAL_AFTER_CLAIM=NO`): `poll_and_claim_once`
NUNCA llama a `fetch_credential_for_job` -- la capacidad existe en
`broker_client.py` para que E3 la invoque explicitamente cuando el
navegador supervisado este listo para consumirla, nunca automaticamente
tras un claim exitoso. No hay ninguna ruta de codigo en este archivo que
conecte "job reclamado" con "solicitar credencial"."""

from __future__ import annotations

import ssl
import time
import uuid
from collections.abc import Callable
from enum import Enum
from logging import Logger, getLogger

import httpx

from windows_agent.broker_client import BrokerUnreachableError, OperationalBrokerClient
from windows_agent.browser_executor import SupervisedAuthExecutor, run_authentication
from windows_agent.portal_contract import AGENT_REPORTABLE_REASON_CODES, AuthOutcome, AuthResult
from windows_agent.config import AgentConfig
from windows_agent.identity import AgentIdentity
from windows_agent.logging_setup import log_event


class AgentState(str, Enum):
    UNPAIRED = "UNPAIRED"
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    REVOKED = "REVOKED"
    STOPPING = "STOPPING"


# E2.1-B: espera ENTRE intentos completos de (re)conexion cuando el broker no
# esta disponible. Cada intento ya incluye el backoff acotado interno de
# `OperationalBrokerClient` (1s/2s/4s/8s); esta escala evita un bucle activo y
# queda acotada en su ultimo valor (nunca crece sin limite).
RECONNECT_WAIT_SECONDS: tuple[float, ...] = (2.0, 4.0, 8.0, 16.0, 30.0)
_SLEEP_SLICE_SECONDS = 0.5


class AgentRevokedError(Exception):
    """El broker rechazo la conexion con `401 AGENT_REVOKED` -- el agente
    transiciona a `REVOKED` y NUNCA vuelve a emparejarse automaticamente
    (seccion 59 del encargo)."""


class AgentRuntime:
    """Bucle de jobs expresado como pasos discretos y deterministas (nunca
    un unico metodo bloqueante monolitico) -- para que las pruebas puedan
    manejar cada transicion sin depender de temporizacion real. `run_forever`
    (usado por el modo CLI `run`) simplemente encadena estos mismos pasos."""

    def __init__(
        self,
        *,
        identity: AgentIdentity,
        config: AgentConfig,
        ssl_context_factory: Callable[[], ssl.SSLContext],
        client_factory: Callable[[ssl.SSLContext], OperationalBrokerClient] | None = None,
        logger: Logger | None = None,
        auth_executor_factory: Callable[..., SupervisedAuthExecutor] | None = None,
    ) -> None:
        self._identity = identity
        self._config = config
        self._ssl_context_factory = ssl_context_factory
        self._client_factory = client_factory or self._default_client_factory
        self._logger = logger or getLogger("windows_agent")

        self.state = AgentState.DISCONNECTED
        self._connection_id: str | None = None
        self._active_job_id: uuid.UUID | None = None
        self._client: OperationalBrokerClient | None = None
        self._auth_executor_factory = auth_executor_factory
        self._cancelled_jobs: set[uuid.UUID] = set()
        self._job_sessions: dict[uuid.UUID, uuid.UUID] = {}

    def _default_client_factory(self, ssl_context: ssl.SSLContext) -> OperationalBrokerClient:
        return OperationalBrokerClient(
            operational_base_url=self._config.operational_base_url,
            ssl_context=ssl_context,
            protocol_version=self._config.protocol_version,
            timeout=self._config.read_timeout_seconds,
        )

    @property
    def connection_id(self) -> str | None:
        """Solo para inspeccion en pruebas -- NUNCA llamado desde
        `logging_setup.log_event` en ningun sitio de este archivo (seccion
        29/47: memoria unicamente, jamas logueado)."""
        return self._connection_id

    def connect(self) -> None:
        """HELLO -- paso 1 de reconexion (seccion 29-30/34 del encargo).
        `connection_id` se guarda SOLO en memoria de proceso; se pierde
        por completo al reiniciar el agente, y queda invalido de inmediato
        si el broker reinicia (ninguna recuperacion persistida)."""
        self.state = AgentState.CONNECTING
        self._client = self._client_factory(self._ssl_context_factory())
        try:
            self._connection_id = self._client.hello()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                self.state = AgentState.REVOKED
                raise AgentRevokedError() from exc
            self.state = AgentState.DISCONNECTED
            raise
        except BrokerUnreachableError:
            self.state = AgentState.DISCONNECTED
            raise
        self.state = AgentState.CONNECTED
        log_event(self._logger, "agent_connected", agent_id=str(self._identity.agent_id))

    def heartbeat_once(self) -> dict:
        assert self._client is not None and self._connection_id is not None, "connect() debe llamarse primero"
        try:
            result = self._client.heartbeat(connection_id=self._connection_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                self.state = AgentState.REVOKED
                raise AgentRevokedError() from exc
            if exc.response.status_code == 409:
                # conexion reemplazada/vencida -- Domain D.1 -- requiere
                # un nuevo HELLO, nunca una reconstruccion silenciosa.
                self.state = AgentState.DISCONNECTED
                self._connection_id = None
            raise
        log_event(self._logger, "heartbeat_ok", agent_id=str(self._identity.agent_id))
        notice = result.get("cancel_notice") if isinstance(result, dict) else None
        if notice:
            # Contrato Domain C/D: la cancelacion es autoritativa en BD y el
            # agente NO envia confirmacion (`SessionCancelNotice`); "reportar"
            # = observarla y soltar el job localmente, dejando el evento
            # estructurado `job_cancellation_observed`. Nunca se solicita
            # credencial para ese job.
            try:
                self.report_cancellation_observed(job_id=uuid.UUID(str(notice["job_id"])))
            except (KeyError, ValueError):
                log_event(self._logger, "cancel_notice_malformed")
        return result

    def _drop_connection(self) -> None:
        """Descarta por completo la conexion logica actual (cliente HTTP +
        `connection_id`): nunca se reutiliza un lease/id obsoleto -- el
        siguiente `connect()` construye cliente y contexto TLS nuevos y un
        HELLO nuevo."""
        self._connection_id = None
        self._active_job_id = None
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None
        self.state = AgentState.DISCONNECTED

    def _wait(self, seconds: float, should_stop: Callable[[], bool], sleep: Callable[[float], None]) -> None:
        """Espera interrumpible: duerme en rebanadas acotadas para que una
        senal de parada se atienda con rapidez (nunca un `sleep` largo
        monolitico)."""
        remaining = seconds
        while remaining > 0 and not should_stop():
            step = min(_SLEEP_SLICE_SECONDS, remaining)
            sleep(step)
            remaining -= step

    def run_forever(
        self,
        *,
        should_stop: Callable[[], bool],
        loop_interval_seconds: float | None = None,
        reconnect_wait_seconds: tuple[float, ...] = RECONNECT_WAIT_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Bucle productivo (`python -m windows_agent run`): conecta, hace
        heartbeat y reclama jobs. Si el broker no esta disponible al
        arrancar, o se pierde/reinicia, el proceso SOBREVIVE: descarta la
        conexion obsoleta, espera con escala acotada (sin bucle activo) y
        reconecta con un HELLO NUEVO. `AgentRevokedError` si propaga (el
        agente nunca se re-empareja ni reintenta tras una revocacion)."""
        interval = loop_interval_seconds
        if interval is None:
            interval = self._config.read_timeout_seconds / 3
        failures = 0
        while not should_stop():
            if self._connection_id is None:
                try:
                    self.connect()
                except AgentRevokedError:
                    raise
                except (BrokerUnreachableError, httpx.HTTPError, OSError) as exc:
                    self._drop_connection()
                    wait = reconnect_wait_seconds[min(failures, len(reconnect_wait_seconds) - 1)]
                    failures += 1
                    log_event(
                        self._logger,
                        "broker_unavailable",
                        attempt=failures,
                        wait_seconds=wait,
                        error=type(exc).__name__,
                    )
                    self._wait(wait, should_stop, sleep)
                    continue
            try:
                self.heartbeat_once()
                claimed = self.poll_and_claim_once()
                if claimed is not None and self._auth_executor_factory is not None:
                    self._authenticate_safely(claimed)
            except AgentRevokedError:
                raise
            except (BrokerUnreachableError, httpx.HTTPError, OSError) as exc:
                # perdida/reinicio del broker o conexion reemplazada/vencida:
                # nunca se reutiliza el connection_id anterior.
                self._drop_connection()
                wait = reconnect_wait_seconds[min(failures, len(reconnect_wait_seconds) - 1)]
                failures += 1
                log_event(self._logger, "connection_lost", error=type(exc).__name__, wait_seconds=wait)
                self._wait(wait, should_stop, sleep)
                continue
            failures = 0  # un ciclo completo exitoso restablece la escala
            self._wait(interval, should_stop, sleep)

    def poll_and_claim_once(self) -> uuid.UUID | None:
        """Descubre y reclama a lo sumo un job -- NUNCA solicita la
        credencial (ver el docstring del modulo). Devuelve el `job_id` si
        se reclamo con exito, o `None` si no habia job disponible o el
        reclamo fue rechazado."""
        assert self._client is not None and self._connection_id is not None, "connect() debe llamarse primero"
        job_info = self._client.get_next_job(connection_id=self._connection_id)
        job_id_raw = job_info.get("job_id")
        if job_id_raw is None:
            return None
        job_id = uuid.UUID(job_id_raw)

        accepted = self._client.claim_job(connection_id=self._connection_id, job_id=job_id)
        if not accepted:
            return None
        self._active_job_id = job_id
        session_raw = job_info.get("session_id")
        if session_raw is not None:
            self._job_sessions[job_id] = uuid.UUID(str(session_raw))
        log_event(self._logger, "job_claimed", job_id=str(job_id))
        # DELIBERADAMENTE nada mas aqui -- ver el docstring del modulo.
        return job_id

    def credential_client(self) -> OperationalBrokerClient:
        """Expone la capacidad de solicitud de credencial para un
        consumidor EXTERNO explicito (E3) -- ver
        `broker_client.OperationalBrokerClient.fetch_credential_for_job`.
        El bucle de este runtime nunca invoca el metodo resultante por su
        cuenta."""
        assert self._client is not None, "connect() debe llamarse primero"
        return self._client

    # --- E3: autenticacion supervisada (AUTHENTICATE_TO_LANDING_ONLY) ---

    def _authenticate_safely(self, job_id: uuid.UUID) -> None:
        try:
            self.authenticate_claimed_job(job_id)
        except AgentRevokedError:
            raise
        except Exception as exc:  # noqa: BLE001 -- nunca mata el bucle; nunca registra contenido
            log_event(self._logger, "auth_unexpected_error", error=type(exc).__name__)

    def authenticate_claimed_job(self, job_id: uuid.UUID, *, heartbeat_every_seconds: float = 3.0) -> AuthResult:
        """Ejecuta la autenticacion supervisada de un job YA reclamado. La
        credencial se pide a traves del endpoint existente (E1-B) y solo
        cuando el login esta identificado; jamas se reintenta. Mientras el
        navegador espera (challenge humano) se siguen enviando heartbeats, lo
        que tambien entrega `cancel_notice`."""
        assert self._client is not None and self._connection_id is not None, "connect() debe llamarse primero"
        assert self._auth_executor_factory is not None
        client, last_beat = self._client, [time.monotonic()]

        def _is_cancelled() -> bool:
            return job_id in self._cancelled_jobs

        def _tick() -> None:
            if time.monotonic() - last_beat[0] < heartbeat_every_seconds or self._connection_id is None:
                return
            last_beat[0] = time.monotonic()
            try:
                self.heartbeat_once()
            except AgentRevokedError:
                raise
            except (BrokerUnreachableError, httpx.HTTPError, OSError):
                log_event(self._logger, "auth_heartbeat_failed")

        def _on_state(state: AuthOutcome) -> None:
            log_event(self._logger, "auth_state", state=state.value, job_id=str(job_id))
            if state is AuthOutcome.WAITING_FOR_USER:
                self._send_status(job_id, "WAITING_FOR_USER")  # el backend valida y proyecta a la sesion

        executor = self._auth_executor_factory(is_cancelled=_is_cancelled, tick=_tick, on_state=_on_state)
        result = run_authentication(
            executor,
            lambda: client.fetch_credential_for_job(connection_id=self._connection_id or "", job_id=job_id),
        )
        log_event(
            self._logger,
            "auth_result",
            job_id=str(job_id),
            outcome=result.outcome.value,
            terminal_reason_code=result.terminal_reason_code,
            credential_submissions=result.credential_submissions,
            post_landing_clicks=result.post_landing_clicks,
        )
        self._report_auth_result(job_id, result)
        return result

    def _send_status(self, job_id: uuid.UUID, status: str, reason_code: str | None = None) -> bool:
        """Un reporte semantico por el contrato existente (`/status` extendido). Nunca lanza."""
        if self._client is None or self._connection_id is None:
            return False
        try:
            self._client.report_status(
                connection_id=self._connection_id,
                job_id=job_id,
                status=status,
                reason_code=reason_code,
                session_id=self._job_sessions.get(job_id),
            )
            return True
        except (BrokerUnreachableError, httpx.HTTPError, OSError):
            log_event(self._logger, "auth_report_failed", job_id=str(job_id), status=status)
            return False

    def _report_auth_result(self, job_id: uuid.UUID, result: AuthResult) -> None:
        """Reporta el resultado SEMANTICO terminal; el backend valida y transiciona la sesion (el
        agente nunca elige un estado de BD). La cancelacion ya es autoritativa en BD: no se reporta."""
        if result.outcome in {AuthOutcome.CANCELLED, AuthOutcome.WAITING_FOR_USER}:
            return
        if result.outcome is AuthOutcome.AUTH_SUCCEEDED:
            self._send_status(job_id, "COMPLETED")
        else:
            code = result.terminal_reason_code
            if code not in AGENT_REPORTABLE_REASON_CODES:
                code = "INTERNAL_ERROR"
            self._send_status(job_id, "FAILED", code)
        self._active_job_id = None

    def report_cancellation_observed(self, *, job_id: uuid.UUID) -> None:
        """Actualiza el estado EN MEMORIA del job activo tras observar una
        notificacion de cancelacion en un heartbeat (seccion 38 del
        encargo) -- nunca persiste nada que pudiera resucitar el job."""
        self._cancelled_jobs.add(job_id)  # E3: la autenticacion en curso se interrumpe y no se vuelve a pedir credencial
        if self._active_job_id == job_id:
            self._active_job_id = None
        log_event(self._logger, "job_cancellation_observed", job_id=str(job_id))

    def stop(self) -> None:
        """Apagado limpio (seccion 64 del encargo): suelta `connection_id`
        y el estado de job en memoria, cierra el cliente HTTP. Un
        apagado ABRUPTO (kill -9 / cierre de sesion) simplemente nunca
        llama a esto -- la seguridad no depende de que este metodo se
        ejecute (seccion 65): ningun secreto vive mas alla del bloque
        `with` de `fetch_credential_for_job` en primer lugar."""
        self.state = AgentState.STOPPING
        self._cancelled_jobs.clear()
        self._connection_id = None
        self._active_job_id = None
        if self._client is not None:
            self._client.close()
            self._client = None
        self.state = AgentState.DISCONNECTED
