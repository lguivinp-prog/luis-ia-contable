"""Broker HTTPS productivo (Fase 5, Bloque 5.2, Domain D -- ver
docs/phase5/block5_2/). Reimplementacion PRODUCTIVA inspirada en la
evidencia de Spike B (`spikes/sol_agent_ipc/container_broker/broker_server.py`,
rama `spike/phase5-sol-agent-ipc`, tag `spike-phase5-sol-agent-ipc-v1` --
nunca mergeada/cherry-picked, solo inspeccionada y reescrita aqui).

Decision de transporte (informe final Domain D, seccion 8): HTTP sobre mTLS
con dos listeners TLS reales, usando `http.server`/`ssl` estandar en vez de
FastAPI/uvicorn -- MISMA razon tecnica ya documentada por el spike: la
extraccion fiable del certificado de cliente real (`connection.getpeercert`)
no es un contrato documentado a traves de la capa ASGI de uvicorn. Se
prefirio evidencia real (lo que el spike validó de verdad) sobre inventar un
canal WebSocket/persistente no validado -- Spike B nunca probó WebSocket.
Cada llamada del agente es una peticion HTTP/1.1 discreta e iniciada por el
agente (nunca el broker inicia una conexion hacia el agente); "conexion" en
este dominio se modela como una entrada de `connection_registry` renovada en
cada peticion autenticada, no como un socket TCP mantenido abierto -- ver
informe final para la justificacion completa.

  - listener de PAIRING (`sol_broker_pairing_port`): TLS servidor-only
    (`CERT_NONE`) -- el agente aun no tiene certificado propio.
  - listener OPERACIONAL (`sol_broker_operational_port`): mTLS estricto
    (`CERT_REQUIRED`) -- cualquier conexion sin certificado de cliente valido
    es rechazada por la capa TLS misma, antes de que la aplicacion vea la
    peticion.

Ambos listeners se publican EXCLUSIVAMENTE en 127.0.0.1 -- `BrokerServer`
rechaza cualquier otro host en el constructor (falla en arranque, nunca en
tiempo de peticion)."""

from __future__ import annotations

import json
import logging
import secrets
import ssl
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.broker import pki
from app.broker.connection_registry import ConnectionBusyError, ConnectionRegistry
from app.broker.errors import (
    AgentRevokedError,
    AgentUnknownError,
    BrokerShuttingDownError,
    BrokerTransportError,
    CertificateInvalidError,
    ClaimRejectedError,
    ConnectionBusyError as TransportConnectionBusyError,
    ConnectionNotEstablishedError,
    ConnectionReplacedError,
    CredentialUnavailableError,
    InternalError,
    InvalidMessageError,
    JobUnavailableError,
    MtlsRequiredError,
    NotFoundError,
    PayloadTooLargeError,
    ProtocolVersionUnsupportedError,
    RequestNotAllowedError,
)
from app.broker.mtls import AuthenticatedAgent, fingerprint_from_der, resolve_agent_identity
from app.broker.pending_dispatch import PendingDispatchStore
from app.broker.protocol import (
    PROTOCOL_VERSION,
    MAX_CREDENTIAL_DELIVERY_RESPONSE_BYTES,
    MAX_FICHA_MESSAGE_BYTES,
    MAX_MESSAGE_BYTES,
    AgentErrorReport,
    CredentialDeliveryRequest,
    CredentialDeliveryResponse,
    FichaReport,
    HeartbeatRequest,
    JobAvailableResponse,
    JobClaimRequest,
    JobClaimResult,
    JobStatusReport,
    PairingCsrSubmitRequest,
    PairingCsrSubmitResponse,
    SessionCancelNotice,
)
from app.broker.secret_delivery_grant import SecretDeliveryGrantRegistry
from app.broker.session_authorization import USABLE_CREDENTIAL_STATUSES, SessionAuthorizationRegistry
from app.core.sol_pairing_verification_throttle import PairingVerificationRateLimitedError
from app.core.sol_vault_registry import vault_unlock_registry
from app.models.user import User
from app.repositories import sol_agent_access_repository as repo
from app.repositories import sol_vault_repository as vault_repo
from app.services import ficha_ruc_service as ficha_service
from app.services import sol_access_session_service as session_service
from app.services import sol_agent_credential_delivery_service as delivery_service
from app.services import sol_browser_result_service as browser_result_service
from app.services import sol_agent_job_service as job_service
from app.services import sol_agent_pairing_service as pairing_service
from app.services import sol_agent_service
from app.services import sol_human_control_reconciler as hc_reconciler

_ALLOWED_HOSTS = {"127.0.0.1", "localhost"}

# Domain D.1, Gate 2: header de transporte que porta el identificador
# logico de conexion (alta entropia, nunca la huella de certificado ni un
# token de job) -- SOLO sobre el canal operacional ya autenticado por mTLS,
# nunca en URL/query/CLI/env/archivo (seccion 8 del encargo).
_CONNECTION_ID_HEADER = "X-Sol-Connection-Id"

# Domain D.1, Gate 3: header no-simple exigido en TODA peticion (ambos
# listeners) -- fuerza preflight CORS ante cualquier intento de navegador y
# distingue trafico de protocolo nativo. Nunca sustituye mTLS/pairing.
_AGENT_PROTOCOL_HEADER = "X-Sol-Agent-Protocol"

_logger = logging.getLogger("app.broker")

# Excepciones de dominio (Domain C / pki) cuyo efecto DB parcial (ej. un
# `PAIRING_LOCKED`/`failed_attempt_count` ya incrementado y flusheado antes
# de lanzar) debe persistir aunque la peticion HTTP termine en error --
# mismo principio ya productivo en `app/routers/auth.py` (commit antes de
# lanzar `HTTPException` en la rama de intento fallido). Un error FUERA de
# este conjunto (bug/no anticipado) provoca rollback.
_COMMIT_WORTHY_EXCEPTIONS = (
    pairing_service.SolAgentPairingServiceError,
    job_service.SolAgentJobServiceError,
    sol_agent_service.SolAgentServiceError,
    pki.PkiError,
    BrokerTransportError,
    # `PairingVerificationRateLimitedError` (Domain C.1a, throttle
    # process-local) NUNCA hereda de `SolAgentPairingServiceError` a
    # proposito (ver `sol_pairing_verification_throttle.py`) -- se agrega
    # aqui explicitamente para que el rechazo por throttle nunca caiga en
    # la rama generica de error inesperado (Domain D.1, Gate 1, prueba D).
    PairingVerificationRateLimitedError,
)

_ERROR_TO_TRANSPORT: dict[type[Exception], type[BrokerTransportError]] = {
    pairing_service.PairingNotFoundError: CertificateInvalidError,
    pairing_service.PairingNotPendingError: CertificateInvalidError,
    pairing_service.PairingExpiredError: CertificateInvalidError,
    pairing_service.PairingLockedError: CertificateInvalidError,
    pairing_service.PairingInvalidCodeError: CertificateInvalidError,
    pairing_service.AgentCertificateFingerprintConflictError: CertificateInvalidError,
    pairing_service.PairingCreationRateLimitedError: CertificateInvalidError,
    PairingVerificationRateLimitedError: CertificateInvalidError,
}


def _log_event(event: str, **fields: object) -> None:
    """Logging estructurado de lista permitida (seccion 65 del encargo):
    SOLO los campos explicitamente pasados por el llamador llegan aqui, y
    cada sitio de llamada de este archivo pasa unicamente
    IDs internos/tipo de evento/version de protocolo/codigo de error estable
    -- nunca un payload crudo, nunca `csr_pem`, `job_token`, `pairing_code`,
    ni material de certificado."""
    try:
        _logger.info("%s %s", event, json.dumps({k: str(v) for k, v in fields.items()}, sort_keys=True))
    except (TypeError, ValueError):
        _logger.info("%s <unserializable-fields>", event)


class _ShuttingDown(Exception):
    pass


class BrokerServer:
    def __init__(
        self,
        *,
        host: str,
        pairing_port: int,
        operational_port: int,
        ca: pki.LocalCa,
        session_factory: sessionmaker,
        connection_registry: ConnectionRegistry | None = None,
        pending_dispatch: PendingDispatchStore | None = None,
        secret_delivery_grants: SecretDeliveryGrantRegistry | None = None,
        session_authorizations: SessionAuthorizationRegistry | None = None,
        max_concurrent_connections: int = 200,
        handshake_timeout_seconds: float = 10.0,
        idle_timeout_seconds: float = 120.0,
        read_timeout_seconds: float = 15.0,
    ) -> None:
        if host not in _ALLOWED_HOSTS:
            raise ValueError(f"SOL_BROKER_REFUSES_NON_LOOPBACK_HOST: {host!r}")

        self.host = host
        self.pairing_port = pairing_port
        self.operational_port = operational_port
        self.ca = ca
        self.session_factory = session_factory
        self.connections = (
            connection_registry
            if connection_registry is not None
            else ConnectionRegistry(secret_lease_timeout_seconds=read_timeout_seconds)
        )
        self.pending_dispatch = pending_dispatch if pending_dispatch is not None else PendingDispatchStore()
        self.secret_delivery_grants = (
            secret_delivery_grants if secret_delivery_grants is not None else SecretDeliveryGrantRegistry()
        )
        # Domain F: contexto humano (user_id, app_session_id) de cada sesion, solo en memoria; ver session_authorization.py
        self.session_authorizations = (
            session_authorizations if session_authorizations is not None else SessionAuthorizationRegistry()
        )
        self.read_timeout_seconds = read_timeout_seconds
        self.idle_timeout_seconds = idle_timeout_seconds

        self._server_leaf = pki.issue_server_leaf_cert(
            ca=ca, common_name="luis-ia-contable-sol-broker", dns_sans=["localhost"], ip_sans=["127.0.0.1"]
        )
        self._connection_semaphore = threading.BoundedSemaphore(max_concurrent_connections)
        self._handshake_timeout_seconds = handshake_timeout_seconds
        self._shutting_down = threading.Event()
        self._pairing_httpd: ThreadingHTTPServer | None = None
        self._operational_httpd: ThreadingHTTPServer | None = None
        self._threads: list[threading.Thread] = []
        self._temp_dirs: list[Path] = []

    # --- ciclo de vida ---

    def start(self) -> None:
        server_key_pem, server_cert_pem = self._server_pem_bytes()

        pairing_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        pairing_ctx.load_cert_chain(*self._write_temp_pem(server_key_pem, server_cert_pem, "pairing"))
        pairing_ctx.verify_mode = ssl.CERT_NONE
        pairing_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        self._pairing_httpd = self._build_httpd(self.pairing_port, pairing_ctx, require_mtls=False)

        op_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        op_ctx.load_cert_chain(*self._write_temp_pem(server_key_pem, server_cert_pem, "operational"))
        op_ctx.load_verify_locations(cadata=self.ca.certificate.public_bytes(_pem_encoding()).decode("ascii"))
        op_ctx.verify_mode = ssl.CERT_REQUIRED
        op_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        self._operational_httpd = self._build_httpd(self.operational_port, op_ctx, require_mtls=True)

        for httpd in (self._pairing_httpd, self._operational_httpd):
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        """Apagado ordenado (seccion 55 del encargo): deja de aceptar
        conexiones nuevas, cierra los listeners, y limpia el registro
        process-local -- NUNCA muta estado durable (ningun job/sesion cambia
        de estado solo porque el broker se apaga)."""
        self._shutting_down.set()
        for httpd in (self._pairing_httpd, self._operational_httpd):
            if httpd is not None:
                httpd.shutdown()
                httpd.server_close()
        for thread in self._threads:
            thread.join(timeout=5.0)
        self.connections.clear()
        self.pending_dispatch.clear()
        self.secret_delivery_grants.clear()
        self.session_authorizations.clear()

        # Barrido de material criptografico efimero (seccion 66 del
        # encargo): las claves/certificados de servidor temporales usados
        # unicamente para los listeners TLS nunca sobreviven al apagado.
        import shutil

        for tmp_dir in self._temp_dirs:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        self._temp_dirs.clear()

    @property
    def pairing_bound_port(self) -> int:
        assert self._pairing_httpd is not None
        return self._pairing_httpd.server_address[1]

    @property
    def operational_bound_port(self) -> int:
        assert self._operational_httpd is not None
        return self._operational_httpd.server_address[1]

    # --- helpers internos ---

    def _server_pem_bytes(self) -> tuple[bytes, bytes]:
        from cryptography.hazmat.primitives import serialization

        key_pem = self._server_leaf.key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        cert_pem = self._server_leaf.certificate.public_bytes(serialization.Encoding.PEM)
        return key_pem, cert_pem

    def _write_temp_pem(self, key_pem: bytes, cert_pem: bytes, tag: str) -> tuple[str, str]:
        import tempfile

        tmp_dir = Path(tempfile.mkdtemp(prefix=f"sol-broker-{tag}-"))
        self._temp_dirs.append(tmp_dir)
        key_path = tmp_dir / "server.key.pem"
        cert_path = tmp_dir / "server.cert.pem"
        key_path.write_bytes(key_pem)
        cert_path.write_bytes(cert_pem)
        try:
            key_path.chmod(0o600)
        except OSError:
            pass
        return str(cert_path), str(key_path)

    def _build_httpd(self, port: int, ctx: ssl.SSLContext, *, require_mtls: bool) -> ThreadingHTTPServer:
        broker = self
        handler_cls = type(
            "_BoundHandler",
            (_BrokerHttpHandler,),
            {"broker": broker, "require_mtls": require_mtls},
        )
        httpd = _LoopbackTLSServer((self.host, port), handler_cls)
        httpd.daemon_threads = True
        httpd.handshake_timeout_seconds = self._handshake_timeout_seconds
        # Domain D.1, Gate 4: el `settimeout()` sobre el socket de ESCUCHA
        # (intento original de Domain D) NO acota de verdad el handshake de
        # una conexion ya aceptada -- verificado empiricamente con un socket
        # real que nunca completa el ClientHello (ver
        # `test_broker_handshake_timeout.py`): un cliente B legitimo
        # esperaba MAS del timeout configurado. Correccion (seccion 24 del
        # encargo, enfoque minimo sugerido -- "timeout de handshake sobre la
        # conexion YA aceptada", sin reescribir el transporte): se
        # desactiva `do_handshake_on_connect` y `_LoopbackTLSServer.
        # get_request()` aplica el timeout EXPLICITAMENTE al socket recien
        # aceptado antes de invocar `do_handshake()` el mismo.
        raw_socket = httpd.socket
        httpd.socket = ctx.wrap_socket(raw_socket, server_side=True, do_handshake_on_connect=False)
        return httpd


class _LoopbackTLSServer(ThreadingHTTPServer):
    """Domain D.1, Gate 4: `get_request()` acota EXPLICITAMENTE el tiempo de
    handshake TLS de cada conexion recien aceptada -- `self.socket` esta
    envuelto con `do_handshake_on_connect=False` (ver `_build_httpd`), asi
    que `accept()` devuelve el nuevo `SSLSocket` ANTES de intentar el
    handshake, dejando a este metodo aplicar `settimeout()` sobre ESE socket
    especifico antes de invocar `do_handshake()` el mismo. Un handshake que
    excede `handshake_timeout_seconds` (o cualquier otro error TLS durante
    el mismo) cierra el socket y propaga un `OSError`/`ssl.SSLError`, que el
    loop de `socketserver.BaseServer._handle_request_noblock()` YA captura y
    descarta sin bloquear la aceptacion de la SIGUIENTE conexion -- nunca se
    necesita logica adicional aqui para que el loop se recupere."""

    handshake_timeout_seconds: float = 10.0

    def get_request(self):
        new_socket, address = self.socket.accept()
        new_socket.settimeout(self.handshake_timeout_seconds)
        try:
            new_socket.do_handshake()
        except BaseException:
            new_socket.close()
            raise
        return new_socket, address


def _pem_encoding():
    from cryptography.hazmat.primitives import serialization

    return serialization.Encoding.PEM


class _BrokerHttpHandler(BaseHTTPRequestHandler):
    broker: BrokerServer
    require_mtls: bool

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # nunca logueamos URL/headers/bodies crudos (seccion 65)

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(self.broker.read_timeout_seconds)

    # --- utilidades de request/response ---

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_code(self, exc: BrokerTransportError) -> None:
        self._send_json(exc.http_status, {"error": exc.code})

    def _read_json_bounded(self, max_bytes: int = MAX_MESSAGE_BYTES) -> dict:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError:
            raise InvalidMessageError() from None
        if length < 0 or length > max_bytes:
            raise PayloadTooLargeError()
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise InvalidMessageError() from None

    def _require_protocol_version(self, body: dict) -> None:
        version = body.get("protocol_version")
        if version != PROTOCOL_VERSION:
            raise ProtocolVersionUnsupportedError()

    def _peer_certificate_der(self) -> bytes | None:
        return self.connection.getpeercert(binary_form=True)

    def _authenticated_agent(self, db: Session) -> AuthenticatedAgent:
        if not self.require_mtls:
            raise MtlsRequiredError()
        der = self._peer_certificate_der()
        try:
            return resolve_agent_identity(db, certificate_der=der)
        except AgentRevokedError:
            # Seccion 54 del encargo: sin push inmediato disponible en un
            # transporte de peticion/respuesta, el SIGUIENTE mensaje del
            # agente revocado revalida su estado (ya lo hizo
            # `resolve_agent_identity`) y "cierra" -- aqui, purgando
            # cualquier entrada de conexion que hubiera quedado de ANTES de
            # la revocacion, para que el registro nunca retenga un agente ya
            # no autorizado.
            if der is not None:
                fingerprint = fingerprint_from_der(der)
                stale_agent = repo.get_agent_by_certificate_fingerprint(db, certificate_fingerprint=fingerprint)
                if stale_agent is not None:
                    self.broker.connections.force_remove(agent_id=stale_agent.id)
                    self.broker.pending_dispatch.discard(agent_id=stale_agent.id)
            raise

    def _authenticated_agent_with_live_connection(self, db: Session) -> AuthenticatedAgent:
        """Usado por TODO endpoint operacional salvo `/agent/hello` (Domain
        D.1, Gate 2): la identidad mTLS por si sola NUNCA es suficiente --
        el agente debe ademas presentar, en el header `X-Sol-Connection-Id`,
        el identificador logico de conexion de alta entropia que
        `/agent/hello` le devolvio, y ese identificador debe coincidir
        EXACTAMENTE con la entrada actual del registro para su `agent_id`
        (nunca solo "algun" registro para ese agente -- discrete HTTP
        requests desde dos procesos distintos pueden compartir el MISMO
        certificado, asi que la huella mTLS sola no basta para probar UNA
        conexion logica viva).

        Sin header -> `ConnectionNotEstablishedError` (nunca establecio
        conexion). Header presente pero no coincide con el registro actual
        (reemplazado por un HELLO mas nuevo, o vencido por inactividad) ->
        `ConnectionReplacedError` -- deliberadamente el MISMO codigo para
        ambos motivos (no-oracle, ver `ConnectionRegistry.validate_and_touch`)."""
        authenticated = self._authenticated_agent(db)
        connection_id = self.headers.get(_CONNECTION_ID_HEADER)
        if not connection_id:
            raise ConnectionNotEstablishedError()
        valid = self.broker.connections.validate_and_touch(
            agent_id=authenticated.agent_id,
            connection_id=connection_id,
            idle_timeout_seconds=self.broker.idle_timeout_seconds,
        )
        if not valid:
            raise ConnectionReplacedError()
        return authenticated

    # --- transaccion por peticion ---

    def _run(self, work) -> None:
        if self.broker._shutting_down.is_set():
            self._send_error_code(BrokerShuttingDownError())
            return
        acquired = self.broker._connection_semaphore.acquire(blocking=False)
        if not acquired:
            self._send_error_code(BrokerShuttingDownError())
            return
        try:
            db = self.broker.session_factory()
            try:
                status, payload = work(db)
                db.commit()
                self._send_json(status, payload)
            except BrokerTransportError as exc:
                if isinstance(exc, _COMMIT_WORTHY_EXCEPTIONS):
                    db.commit()
                else:
                    db.rollback()
                self._send_error_code(exc)
            except _COMMIT_WORTHY_EXCEPTIONS as exc:
                db.commit()
                self._send_error_code(_map_domain_error(exc))
            except ValidationError:
                db.rollback()
                self._send_error_code(InvalidMessageError())
            except Exception:
                db.rollback()
                _log_event("broker_unexpected_error", route=self.path)
                self._send_error_code(InternalError())
            finally:
                db.close()
        finally:
            self.broker._connection_semaphore.release()

    # --- enrutamiento ---

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def _expected_host_values(self) -> set[str]:
        port = self.broker.pairing_bound_port if not self.require_mtls else self.broker.operational_bound_port
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _reject_browser_like_request(self) -> bool:
        """Domain D.1, Gate 3: defensa en profundidad sobre el listener
        loopback -- NUNCA sustituye mTLS ni el codigo de pairing (secciones
        19-20 del encargo), solo reduce la superficie de que trafico de
        JavaScript de navegador (incluso via DNS rebinding hacia
        127.0.0.1) llegue a la logica de pairing/claim. Devuelve `True` (y
        ya envio la respuesta 403) si la peticion debe rechazarse."""
        host = self.headers.get("Host")
        if host not in self._expected_host_values():
            self._send_error_code(RequestNotAllowedError())
            return True

        # `Origin` NUNCA lo envia un cliente HTTP nativo (httpx/agente
        # real) -- solo el navegador lo agrega automaticamente. Su sola
        # presencia ya es suficiente para rechazar (seccion 15).
        if self.headers.get("Origin") is not None:
            self._send_error_code(RequestNotAllowedError())
            return True

        # `Sec-Fetch-Site` es metadata exclusiva de fetch/XHR de navegador
        # -- un cliente nativo nunca la envia, sea cual sea su valor.
        if self.headers.get("Sec-Fetch-Site") is not None:
            self._send_error_code(RequestNotAllowedError())
            return True

        # Header no-simple deliberado (seccion 16): fuerza preflight CORS
        # ante cualquier intento de navegador, y distingue trafico de
        # protocolo nativo. Defensa en profundidad, nunca autenticacion
        # primaria (esa sigue siendo mTLS/codigo de pairing).
        if not self.headers.get(_AGENT_PROTOCOL_HEADER):
            self._send_error_code(RequestNotAllowedError())
            return True

        content_length = self.headers.get("Content-Length", "0")
        if content_length not in ("0", "", None):
            content_type = self.headers.get("Content-Type", "")
            if not content_type.startswith("application/json"):
                self._send_error_code(RequestNotAllowedError())
                return True

        return False

    def _dispatch(self, method: str) -> None:
        import re

        if self._reject_browser_like_request():
            return

        path = self.path

        if not self.require_mtls:
            if method == "POST" and path == "/pairing/csr":
                self._run(self._handle_pairing_csr)
                return
            self._send_error_code(NotFoundError())
            return

        if method == "POST" and path == "/agent/hello":
            self._run(self._handle_hello)
            return
        if method == "POST" and path == "/agent/heartbeat":
            self._run(self._handle_heartbeat)
            return
        if method == "GET" and path == "/agent/jobs/next":
            self._run(self._handle_jobs_next)
            return
        m = re.match(r"^/agent/jobs/([0-9a-fA-F-]{36})/claim$", path)
        if method == "POST" and m:
            self._run(lambda db: self._handle_job_claim(db, uuid.UUID(m.group(1))))
            return
        m = re.match(r"^/agent/jobs/([0-9a-fA-F-]{36})/status$", path)
        if method == "POST" and m:
            self._run(lambda db: self._handle_job_status(db, uuid.UUID(m.group(1))))
            return
        m = re.match(r"^/agent/jobs/([0-9a-fA-F-]{36})/ficha$", path)
        if method == "POST" and m:
            self._run(lambda db: self._handle_job_ficha(db, uuid.UUID(m.group(1))))
            return
        m = re.match(r"^/agent/jobs/([0-9a-fA-F-]{36})/credential$", path)
        if method == "POST" and m:
            self._handle_job_credential_request(uuid.UUID(m.group(1)))
            return
        if method == "POST" and path == "/agent/error":
            self._run(self._handle_agent_error)
            return
        self._send_error_code(NotFoundError())

    # --- handlers: pairing bootstrap ---

    def _handle_pairing_csr(self, db: Session) -> tuple[int, dict]:
        """Domain D.1, Gate 1: la firma real de CA solo ocurre DENTRO del
        callback `_issue_certificate`, que `confirm_pairing` invoca
        UNICAMENTE despues de validar que el codigo de pairing es correcto
        -- nunca antes. `signed_holder` es una variable de closure puramente
        local (nunca un estado persistido/observable externamente) que
        recoge el certificado ya firmado para construir la respuesta HTTP
        DESPUES de que `confirm_pairing` retorne (es decir, despues de que
        el llamador (`_run`) haga `db.commit()`)."""
        body = self._read_json_bounded()
        try:
            request = PairingCsrSubmitRequest.model_validate(body)
        except ValidationError:
            raise InvalidMessageError() from None
        if request.protocol_version != PROTOCOL_VERSION:
            raise ProtocolVersionUnsupportedError()

        signed_holder: dict[str, object] = {}

        def _issue_certificate() -> str:
            try:
                signed_cert = pki.sign_agent_csr(
                    ca=self.broker.ca,
                    csr_pem=request.csr_pem.encode("ascii"),
                    agent_common_name=pki.new_agent_common_name(),
                )
            except pki.CsrInvalidError:
                raise CertificateInvalidError() from None
            signed_holder["cert"] = signed_cert
            return pki.fingerprint_sha256_hex(signed_cert)

        # `_pairing_tenant_id` solo resuelve EN QUE tenant buscar -- nunca
        # una decision de autorizacion. `confirm_pairing` (Domain C) sigue
        # siendo la unica autoridad: vuelve a validar tenant/estado/codigo
        # bajo su propio lock antes de mutar cualquier cosa, y solo entonces
        # invoca `_issue_certificate`.
        tenant_id = _pairing_tenant_id(db, request.pairing_id)

        confirmed_pairing = pairing_service.confirm_pairing(
            db,
            tenant_id=tenant_id,
            pairing_id=request.pairing_id,
            code=request.pairing_code,
            issue_certificate=_issue_certificate,
            actor_user_id=None,
        )

        from cryptography.hazmat.primitives import serialization

        signed_cert = signed_holder["cert"]
        cert_pem = signed_cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
        response = PairingCsrSubmitResponse(
            agent_id=confirmed_pairing.confirmed_agent_id,
            certificate_pem=cert_pem,
            certificate_not_after=signed_cert.not_valid_after_utc,
        )
        _log_event("pairing_csr_confirmed", pairing_id=request.pairing_id, agent_id=confirmed_pairing.confirmed_agent_id)
        return 200, response.model_dump(mode="json")

    # --- handlers: canal operacional (mTLS) ---

    def _handle_hello(self, db: Session) -> tuple[int, dict]:
        """Domain D.1, Gate 2: genera un identificador logico de conexion de
        alta entropia (256 bits, `secrets.token_urlsafe(32)` -- misma
        entropia ya usada para el token de job de Domain C) y lo devuelve
        UNA vez, sobre el canal ya autenticado por mTLS. Nunca se persiste
        en BD, nunca se loguea en claro (ver `_log_event`, que solo pasa
        `agent_id`)."""
        body = self._read_json_bounded()
        self._require_protocol_version(body)
        authenticated = self._authenticated_agent(db)

        connection_id = secrets.token_urlsafe(32)
        try:
            previous_handle = self.broker.connections.register(
                agent_id=authenticated.agent_id,
                tenant_id=authenticated.tenant_id,
                connection_handle=connection_id,
                protocol_version=PROTOCOL_VERSION,
            )
        except ConnectionBusyError:
            # Domain E1-B, seccion 24 del encargo: un HELLO de reemplazo
            # NUNCA sustituye la conexion mientras tiene un lease de
            # entrega de secreto activo -- rechazo estable, nunca
            # bloqueante (la peticion original de entrega sigue su curso
            # normal, acotada por sus propios timeouts ya congelados).
            raise TransportConnectionBusyError() from None
        if previous_handle is not None:
            _log_event("agent_connection_replaced", agent_id=authenticated.agent_id)
        _log_event("agent_connected", agent_id=authenticated.agent_id)
        return 200, {"protocol_version": PROTOCOL_VERSION, "connection_id": connection_id}

    def _handle_heartbeat(self, db: Session) -> tuple[int, dict]:
        body = self._read_json_bounded()
        try:
            heartbeat = HeartbeatRequest.model_validate(body)
        except ValidationError:
            raise InvalidMessageError() from None
        self._require_protocol_version(body)
        authenticated = self._authenticated_agent_with_live_connection(db)

        sol_agent_service.record_heartbeat(db, tenant_id=authenticated.tenant_id, agent_id=authenticated.agent_id)
        hc_reconciler.reconcile_on_heartbeat(
            db,
            tenant_id=authenticated.tenant_id,
            agent_id=authenticated.agent_id,
            holding_job_id=heartbeat.holding_job_id,
        )

        response: dict = {"protocol_version": PROTOCOL_VERSION, "ok": True, "cancel_notice": None}
        record = self.broker.connections.get(agent_id=authenticated.agent_id)
        held = heartbeat.holding_job_id
        if held is not None:
            held_job = repo.get_job(db, tenant_id=authenticated.tenant_id, job_id=held)
            if held_job is not None and held_job.agent_id == authenticated.agent_id:
                if held_job.status == "CLAIMED":
                    # tras reiniciar el broker el registro en memoria perdio el job: se readopta (nunca se resucita
                    # uno terminal), de modo que una cancelacion posterior llegue al agente.
                    if record is not None and record.active_job_id is None:
                        self.broker.connections.set_active_job(agent_id=authenticated.agent_id, job_id=held_job.id)
                        record = self.broker.connections.get(agent_id=authenticated.agent_id)
                elif held_job.status in {"CANCELLED", "EXPIRED", "FAILED"}:
                    response["cancel_notice"] = SessionCancelNotice(job_id=held_job.id).model_dump(mode="json")
                    self.broker.connections.set_active_job(agent_id=authenticated.agent_id, job_id=None)
                    return 200, response
        if record is not None and record.active_job_id is not None:
            job = repo.get_job(db, tenant_id=authenticated.tenant_id, job_id=record.active_job_id)
            if job is not None and job.status == "CANCELLED":
                notice = SessionCancelNotice(job_id=job.id)
                response["cancel_notice"] = notice.model_dump(mode="json")
                self.broker.connections.set_active_job(agent_id=authenticated.agent_id, job_id=None)
        return 200, response

    def _handle_jobs_next(self, db: Session) -> tuple[int, dict]:
        authenticated = self._authenticated_agent_with_live_connection(db)
        job_id = self.broker.pending_dispatch.peek_metadata(agent_id=authenticated.agent_id)
        if job_id is None:
            empty = JobAvailableResponse(job_id=None, session_id=None, scope=None, expires_at=None)
            return 200, empty.model_dump(mode="json")

        job = repo.get_job(db, tenant_id=authenticated.tenant_id, job_id=job_id)
        if job is None or job.agent_id != authenticated.agent_id:
            empty = JobAvailableResponse(job_id=None, session_id=None, scope=None, expires_at=None)
            return 200, empty.model_dump(mode="json")

        session_row = repo.get_session(db, tenant_id=authenticated.tenant_id, session_id=job.session_id)
        scope = session_row.scope if session_row is not None else None
        response = JobAvailableResponse(
            job_id=job.id,
            session_id=job.session_id,
            scope=scope,
            expires_at=job.expires_at,
            destination=session_row.destination if session_row is not None else None,
            extract_ficha=bool(session_row.extract_ficha) if session_row is not None else False,
        )
        return 200, response.model_dump(mode="json")

    def _handle_job_claim(self, db: Session, job_id: uuid.UUID) -> tuple[int, dict]:
        body = self._read_json_bounded()
        try:
            request = JobClaimRequest.model_validate(body)
        except ValidationError:
            raise InvalidMessageError() from None
        if request.protocol_version != PROTOCOL_VERSION:
            raise ProtocolVersionUnsupportedError()
        authenticated = self._authenticated_agent_with_live_connection(db)

        raw_token = self.broker.pending_dispatch.pop_token_for_claim(agent_id=authenticated.agent_id, job_id=job_id)
        if raw_token is None:
            raise JobUnavailableError()

        try:
            job = job_service.claim_job(db, job_token=raw_token)
        except job_service.JobClaimInvalidError:
            raise ClaimRejectedError() from None

        self.broker.connections.set_active_job(agent_id=authenticated.agent_id, job_id=job.id)
        _log_event("job_claimed_over_transport", agent_id=authenticated.agent_id, job_id=job.id)
        result = JobClaimResult(job_id=job.id, accepted=True)
        return 200, result.model_dump(mode="json")

    def _handle_job_status(self, db: Session, job_id: uuid.UUID) -> tuple[int, dict]:
        body = self._read_json_bounded()
        try:
            request = JobStatusReport.model_validate(body)
        except ValidationError:
            raise InvalidMessageError() from None
        if request.protocol_version != PROTOCOL_VERSION:
            raise ProtocolVersionUnsupportedError()
        if request.job_id != job_id:
            raise InvalidMessageError()
        authenticated = self._authenticated_agent_with_live_connection(db)

        if request.status == "PREAUTH_READY":
            return self._handle_preauth_ready(db, job_id, authenticated, request)

        # E3.1: proyeccion AUTORITATIVA en backend (ver sol_browser_result_service).
        try:
            outcome = browser_result_service.apply_browser_result(
                db,
                tenant_id=authenticated.tenant_id,
                agent_id=authenticated.agent_id,
                job_id=job_id,
                claimed_session_id=request.session_id,
                status=request.status,
                reason_code=request.reason_code,
                diagnostic_code=request.diagnostic_code,
            )
        except browser_result_service.BrowserResultNotFoundError:
            raise NotFoundError() from None
        except (
            browser_result_service.BrowserResultRejectedError,
            job_service.SolAgentJobServiceError,
            session_service.SolAccessSessionServiceError,
        ):
            raise ClaimRejectedError() from None

        if outcome.job_status in {"COMPLETED", "FAILED"}:
            self.broker.connections.set_active_job(agent_id=authenticated.agent_id, job_id=None)
        return 200, {
            "protocol_version": PROTOCOL_VERSION,
            "job_id": str(outcome.job_id),
            "status": outcome.job_status,
            "session_status": outcome.session_status,
        }
        return 200, {"protocol_version": PROTOCOL_VERSION, "job_id": str(updated.id), "status": updated.status}

    def _handle_job_ficha(self, db: Session, job_id: uuid.UUID) -> tuple[int, dict]:
        """M2: el Agente reporta los campos ESTRUCTURADOS de la Ficha RUC. El backend valida, compara el RUC con el del
        cliente (WRONG_TAXPAYER_CONTEXT => la sesion falla) y deja una propuesta PENDIENTE de revision humana."""
        body = self._read_json_bounded(MAX_FICHA_MESSAGE_BYTES)
        try:
            request = FichaReport.model_validate(body)
        except ValidationError:
            raise InvalidMessageError() from None
        if request.protocol_version != PROTOCOL_VERSION or request.job_id != job_id:
            raise InvalidMessageError() if request.job_id != job_id else ProtocolVersionUnsupportedError()
        authenticated = self._authenticated_agent_with_live_connection(db)
        try:
            outcome = ficha_service.store_snapshot_from_agent(
                db,
                tenant_id=authenticated.tenant_id,
                agent_id=authenticated.agent_id,
                job_id=job_id,
                claimed_session_id=request.session_id,
                schema_version=request.schema_version,
                raw_fields=request.fields,
                malformed=request.malformed,
            )
        except ficha_service.FichaServiceError as exc:
            if exc.code in {"FICHA_JOB_NOT_FOUND"}:
                raise NotFoundError() from None
            raise ClaimRejectedError() from None
        if outcome.wrong_taxpayer:
            self.broker.connections.set_active_job(agent_id=authenticated.agent_id, job_id=None)
        return 200, {"protocol_version": PROTOCOL_VERSION, "job_id": str(job_id), "accepted": bool(outcome.accepted)}

    def _handle_preauth_ready(self, db: Session, job_id: uuid.UUID, authenticated, request) -> tuple[int, dict]:
        """Domain F: el Agente senala `PREAUTH_READY` (portal validado por COMPLETO, aun SIN credencial). El broker
        revalida agente/conexion (ya hecho por `_authenticated_agent_with_live_connection`), job, sesion,
        usuario solicitante, permisos y boveda -- `authorize_credential_delivery` re-verifica todo en vivo -- y
        SOLO ENTONCES crea el grant de un solo uso (60 s). Respuesta 200 = el agente puede pedir la credencial
        de inmediato; cualquier rechazo => el agente NO la pide. No crea ningun estado de sesion/job."""
        job = repo.get_job(db, tenant_id=authenticated.tenant_id, job_id=job_id)
        if job is None or job.agent_id != authenticated.agent_id:
            raise NotFoundError()
        if request.session_id is not None and request.session_id != job.session_id:
            raise NotFoundError()
        ok_body = {
            "protocol_version": PROTOCOL_VERSION,
            "job_id": str(job.id),
            "status": "PREAUTH_READY",
            "grant_authorized": True,
        }
        if self.broker.secret_delivery_grants.peek(tenant_id=authenticated.tenant_id, job_id=job_id) is not None:
            return 200, ok_body  # autorizacion previa (harnesses de aceptacion): nunca se crea un segundo grant
        session_row = repo.get_session(db, tenant_id=authenticated.tenant_id, session_id=job.session_id)
        if session_row is None or session_row.status != "IN_PROGRESS" or job.status != "CLAIMED":
            raise ClaimRejectedError()
        context = self.broker.session_authorizations.get(tenant_id=authenticated.tenant_id, session_id=job.session_id)
        if context is None:
            raise ClaimRejectedError()  # sin contexto humano en memoria (p. ej. reinicio): falla CERRADO
        actor = db.get(User, context.user_id)
        if actor is None or actor.tenant_id != authenticated.tenant_id or not actor.is_active:
            raise ClaimRejectedError()
        # Revalida el estado de la credencial EN EL MOMENTO JIT (pudo deshabilitarse tras crear la sesion).
        credential = vault_repo.get_credential_by_id(
            db, tenant_id=authenticated.tenant_id, client_id=session_row.client_id, credential_id=session_row.credential_id
        )
        if credential is None or credential.status not in USABLE_CREDENTIAL_STATUSES:
            raise ClaimRejectedError()
        try:
            delivery_service.authorize_credential_delivery(
                self.broker.session_factory,
                grant_registry=self.broker.secret_delivery_grants,
                vault_registry=vault_unlock_registry,
                tenant_id=authenticated.tenant_id,
                session_id=job.session_id,
                job_id=job_id,
                actor=actor,
                app_session_id=context.app_session_id,
            )
        except delivery_service.SolAgentCredentialDeliveryError:
            raise ClaimRejectedError() from None
        return 200, ok_body

    def _handle_job_credential_request(self, job_id: uuid.UUID) -> None:
        """Fase 5, Bloque 5.2, Domain E1-B: entrega de credencial SOL al
        agente autenticado. Deliberadamente NO usa `_run` (secciones 23-25/
        42 del encargo): el lease de conexion (paso 3 del orden exacto)
        debe adquirirse ANTES de la logica de entrega y liberarse en un
        `finally` que envuelve TODA la peticion -- incluida la
        revalidacion final POSTERIOR al commit del gate de BD (paso 13),
        que `_run` no soporta (commit siempre inmediatamente antes de
        enviar, sin hueco para una revalidacion intermedia). Esta es la
        unica ruta del broker con esa forma -- todas las demas siguen
        usando `_run` sin cambios."""
        if self.broker._shutting_down.is_set():
            self._send_error_code(BrokerShuttingDownError())
            return
        if not self.broker._connection_semaphore.acquire(blocking=False):
            self._send_error_code(BrokerShuttingDownError())
            return

        lease_agent_id: uuid.UUID | None = None
        lease_token: str | None = None
        grant_key_tenant_id: uuid.UUID | None = None
        try:
            db = self.broker.session_factory()
            try:
                try:
                    body = self._read_json_bounded()
                    try:
                        request = CredentialDeliveryRequest.model_validate(body)
                    except ValidationError:
                        raise InvalidMessageError() from None
                    if request.protocol_version != PROTOCOL_VERSION:
                        raise ProtocolVersionUnsupportedError()

                    authenticated = self._authenticated_agent(db)
                    connection_id = self.headers.get(_CONNECTION_ID_HEADER)
                    if not connection_id:
                        raise ConnectionNotEstablishedError()

                    lease_token = self.broker.connections.pin(
                        agent_id=authenticated.agent_id,
                        connection_id=connection_id,
                        idle_timeout_seconds=self.broker.idle_timeout_seconds,
                    )
                    if lease_token is None:
                        raise ConnectionReplacedError()
                    lease_agent_id = authenticated.agent_id
                    grant_key_tenant_id = authenticated.tenant_id

                    try:
                        material = delivery_service.deliver_credential(
                            db,
                            grant_registry=self.broker.secret_delivery_grants,
                            vault_registry=vault_unlock_registry,
                            tenant_id=authenticated.tenant_id,
                            agent_id=authenticated.agent_id,
                            job_id=job_id,
                        )
                    except delivery_service.CredentialDeliveryJobNotEligibleError:
                        raise JobUnavailableError() from None
                    except delivery_service.CredentialDeliveryUnavailableError:
                        raise CredentialUnavailableError() from None

                    # Paso 12: commit del gate -- si falla aqui (fallo
                    # transitorio de BD tras un descifrado ya exitoso), el
                    # grant NUNCA vuelve a AUTHORIZED (seccion 43/80 del
                    # encargo): se invalida, no se envia ningun secreto.
                    try:
                        db.commit()
                    except Exception:
                        db.rollback()
                        self.broker.secret_delivery_grants.invalidate(tenant_id=authenticated.tenant_id, job_id=job_id)
                        _log_event("credential_delivery_commit_failed", agent_id=authenticated.agent_id, job_id=job_id)
                        self._send_error_code(InternalError())
                        return

                    # Gate ya irrevocablemente consumido -- el grant se
                    # consume de inmediato, independientemente de si la
                    # escritura de red que sigue tiene exito (seccion 46
                    # del encargo: "gate consumed=YES" incluso si falla la
                    # escritura final).
                    self.broker.secret_delivery_grants.mark_consumed(tenant_id=authenticated.tenant_id, job_id=job_id)

                    # Paso 13: revalidacion final -- conexion todavia
                    # fijada por ESTE lease, y agente todavia ACTIVE.
                    # NUNCA deshace el gate ya consumido (seccion 17 del
                    # encargo: "gate commit wins first: cancel does not
                    # rewind gate" -- mismo principio para cualquier fallo
                    # posterior al commit).
                    lease_still_valid = self.broker.connections.revalidate_lease(
                        agent_id=authenticated.agent_id, lease_token=lease_token
                    )
                    agent_still_active = False
                    if lease_still_valid:
                        fresh_agent = repo.get_agent(db, tenant_id=authenticated.tenant_id, agent_id=authenticated.agent_id)
                        agent_still_active = fresh_agent is not None and fresh_agent.status == "ACTIVE"

                    if not (lease_still_valid and agent_still_active):
                        _log_event(
                            "credential_delivery_stale_at_write", agent_id=authenticated.agent_id, job_id=job_id
                        )
                        self._send_error_code(ConnectionReplacedError())
                        return

                    response = CredentialDeliveryResponse(
                        ruc=material.ruc, sol_username=material.sol_username, sol_password=material.sol_password
                    )
                    payload = response.model_dump(mode="json")
                    body_bytes = json.dumps(payload, default=str).encode("utf-8")
                    if len(body_bytes) > MAX_CREDENTIAL_DELIVERY_RESPONSE_BYTES:
                        # Nunca alcanzable con los limites de campo actuales
                        # de `CredentialDeliveryResponse` -- defensivo, sin
                        # revelar el motivo (seccion 70 del encargo).
                        self._send_error_code(InternalError())
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body_bytes)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(body_bytes)
                    _log_event("credential_delivered_over_transport", agent_id=authenticated.agent_id, job_id=job_id)
                except BrokerTransportError as exc:
                    db.rollback()
                    self._send_error_code(exc)
                except ValidationError:
                    db.rollback()
                    self._send_error_code(InvalidMessageError())
                except Exception:
                    db.rollback()
                    if grant_key_tenant_id is not None:
                        self.broker.secret_delivery_grants.invalidate(tenant_id=grant_key_tenant_id, job_id=job_id)
                    _log_event("broker_unexpected_error", route=self.path)
                    self._send_error_code(InternalError())
            finally:
                db.close()
        finally:
            if lease_token is not None and lease_agent_id is not None:
                self.broker.connections.release_lease(agent_id=lease_agent_id, lease_token=lease_token)
            self.broker._connection_semaphore.release()

    def _handle_agent_error(self, db: Session) -> tuple[int, dict]:
        body = self._read_json_bounded()
        try:
            request = AgentErrorReport.model_validate(body)
        except ValidationError:
            raise InvalidMessageError() from None
        if request.protocol_version != PROTOCOL_VERSION:
            raise ProtocolVersionUnsupportedError()
        authenticated = self._authenticated_agent_with_live_connection(db)

        _log_event(
            "agent_error_report", agent_id=authenticated.agent_id, job_id=request.job_id, error_code=request.error_code
        )
        return 200, {"protocol_version": PROTOCOL_VERSION, "ok": True}


def _pairing_tenant_id(db: Session, pairing_id: uuid.UUID) -> uuid.UUID:
    """Resuelve `tenant_id` a partir del `pairing_id` para poder invocar
    `confirm_pairing` (que exige `tenant_id` explicito por diseno,
    Domain C). El listener de pairing es pre-autenticacion (`CERT_NONE`) por
    definicion -- este lookup NO es una decision de autorizacion: solo le
    dice a `confirm_pairing` en que tenant buscar, y `confirm_pairing` sigue
    validando tenant/estado/codigo con su propio lock antes de mutar
    cualquier cosa."""
    from sqlalchemy import select

    from app.models.sol_agent_access import SolAgentPairing

    row = db.execute(select(SolAgentPairing.tenant_id).where(SolAgentPairing.id == pairing_id)).first()
    if row is None:
        raise CertificateInvalidError()
    return row[0]


def _map_domain_error(exc: Exception) -> BrokerTransportError:
    """Los codigos de `pairing_service`/`pki` (asociados a `/pairing/csr`)
    usan `CertificateInvalidError` como generico; cualquier otra excepcion de
    dominio no anticipada explicitamente (ej. una carrera defensiva en
    `sol_agent_service` que en la practica ya es inalcanzable porque
    `_authenticated_agent` valida ACTIVE antes) usa un generico distinto,
    nunca el mismo codigo de "certificado invalido" que no aplicaria."""
    mapped = _ERROR_TO_TRANSPORT.get(type(exc))
    if mapped is not None:
        return mapped()
    if isinstance(exc, (pki.PkiError, pairing_service.SolAgentPairingServiceError)):
        return CertificateInvalidError()
    return ClaimRejectedError()
