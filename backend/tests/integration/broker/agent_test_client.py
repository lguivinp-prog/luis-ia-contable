"""ARNES DE PRUEBA UNICAMENTE (Fase 5, Bloque 5.2, Domain D -- ver
docs/phase5/block5_2/, encargo seccion 68: "crear SOLO un arnes de
prueba... nunca el Agente Windows productivo"). Simula lo que hara el futuro
Agente Windows real: genera su propia clave privada (que NUNCA sale de este
proceso Python de prueba, mucho menos hacia el backend/broker), construye un
CSR real, se empareja, conecta por mTLS, envia heartbeat, descubre/reclama
un job sintetico, reporta estado, y se desconecta.

Deliberadamente SIN: `.exe`, instalador, servicio de Windows, tarea
programada, PowerShell, Playwright, ni ningun acceso a Chromium/SUNAT."""

from __future__ import annotations

import shutil
import ssl
import tempfile
import uuid
import weakref
from pathlib import Path

import httpx

from app.broker import pki
from app.broker.protocol import PROTOCOL_VERSION

# Mismos nombres de header que espera `server.py` -- un agente real
# siempre los envia; se centralizan aqui para que las pruebas de Gate 3
# (Host/Origin/Sec-Fetch-Site/header de protocolo) puedan omitirlos o
# sustituirlos deliberadamente sin duplicar el nombre literal en cada test.
CONNECTION_ID_HEADER = "X-Sol-Connection-Id"
AGENT_PROTOCOL_HEADER = "X-Sol-Agent-Protocol"


class AgentTestClient:
    def __init__(self, *, ca_certificate_pem: bytes, pairing_base_url: str, operational_base_url: str) -> None:
        self._ca_cert_pem = ca_certificate_pem
        self.pairing_base_url = pairing_base_url
        self.operational_base_url = operational_base_url
        self._key = None
        self._csr_pem: bytes | None = None
        self._client_cert_pem: bytes | None = None
        self._client_key_pem: bytes | None = None
        self._client_cert_path: Path | None = None
        self._client_key_path: Path | None = None
        self.agent_id: uuid.UUID | None = None
        self.connection_id: str | None = None
        self._tmp_dir = Path(tempfile.mkdtemp(prefix="sol-agent-test-client-"))
        # Barrido de material criptografico efimero (seccion 66 del
        # encargo): la clave/certificado de CLIENTE de este arnes de prueba
        # nunca sobrevive al objeto que los uso, sin depender de que cada
        # prueba recuerde llamar un `close()` explicito.
        weakref.finalize(self, shutil.rmtree, str(self._tmp_dir), True)

    # --- generacion de clave/CSR (nunca sale del proceso de prueba) ---

    def generate_key_and_csr(self, *, common_name: str | None = None) -> bytes:
        self._key, self._csr_pem = pki.build_agent_csr(common_name=common_name or f"test-agent-{uuid.uuid4().hex[:8]}")
        return self._csr_pem

    # --- bootstrap (listener de pairing, sin mTLS) ---

    def submit_pairing_csr(
        self, *, pairing_id: uuid.UUID, pairing_code: str, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        if self._csr_pem is None:
            self.generate_key_and_csr()
        ctx = self._trust_only_ssl_context()
        with httpx.Client(verify=ctx, timeout=10.0) as client:
            response = client.post(
                f"{self.pairing_base_url}/pairing/csr",
                json={
                    "protocol_version": PROTOCOL_VERSION,
                    "pairing_id": str(pairing_id),
                    "pairing_code": pairing_code,
                    "csr_pem": self._csr_pem.decode("ascii"),
                },
                headers=self._native_headers(headers),
            )
        if response.status_code == 200:
            body = response.json()
            self.agent_id = uuid.UUID(body["agent_id"])
            self._client_cert_pem = body["certificate_pem"].encode("ascii")
            self._store_client_identity()
        return response

    def _native_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Cabeceras que un agente NATIVO real siempre envia -- nunca
        `Origin` (eso lo agrega el navegador, no un cliente HTTP nativo).
        Las pruebas de Gate 3 pasan `headers={}`/valores deliberadamente
        distintos para simular trafico tipo-navegador."""
        base = {AGENT_PROTOCOL_HEADER: str(PROTOCOL_VERSION)}
        if extra is not None:
            base.update(extra)
        return base

    def _connection_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = self._native_headers(extra)
        if self.connection_id is not None and CONNECTION_ID_HEADER not in headers:
            headers[CONNECTION_ID_HEADER] = self.connection_id
        return headers

    def _store_client_identity(self) -> None:
        from cryptography.hazmat.primitives import serialization

        self._client_key_pem = self._key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        # Domain E1-B, hallazgo de la prueba de concurrencia real (K/X/Y):
        # el certificado/clave de cliente se escriben a disco UNA SOLA VEZ
        # aqui, en un momento single-threaded (justo tras el pairing) --
        # `_mtls_ssl_context()` antes los reescribia en CADA llamada, lo
        # que corrompia el PEM (`ssl.SSLError: [SSL] PEM lib`) cuando dos
        # peticiones concurrentes del MISMO agente (mismo objeto
        # `AgentTestClient`, dos hilos) llamaban `request_credential` a la
        # vez -- una carrera de escritura de archivo real, no relacionada
        # con el broker en si. Una vez escritos, las lecturas concurrentes
        # de un archivo ya completo son seguras.
        cert_path = self._tmp_dir / "client.cert.pem"
        key_path = self._tmp_dir / "client.key.pem"
        cert_path.write_bytes(self._client_cert_pem)
        key_path.write_bytes(self._client_key_pem)
        self._client_cert_path = cert_path
        self._client_key_path = key_path

    # --- construccion de contextos TLS ---

    def _trust_only_ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(cadata=self._ca_cert_pem.decode("ascii"))
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        return ctx

    def _mtls_ssl_context(self) -> ssl.SSLContext:
        """Hallazgo real ya documentado por el spike (`pki_lib.py`,
        `build_client_ssl_context`): construir el `ssl.SSLContext` explicito
        cargando CA + cadena de cliente en el MISMO objeto es lo unico que
        presenta el certificado de cliente de forma fiable bajo TLS 1.3 en
        este entorno -- pasar `verify=`/`cert=` por separado a `httpx.Client`
        no lo hace. SOLO lee los archivos (ya escritos una vez por
        `_store_client_identity`) -- nunca los reescribe, para ser seguro
        ante llamadas concurrentes desde multiples hilos (Domain E1-B)."""
        assert self._client_cert_path is not None and self._client_key_path is not None
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(cadata=self._ca_cert_pem.decode("ascii"))
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile=str(self._client_cert_path), keyfile=str(self._client_key_path))
        return ctx

    # --- canal operacional (mTLS) ---

    def _operational_client(self, *, ssl_context: ssl.SSLContext | None = None) -> httpx.Client:
        ctx = ssl_context if ssl_context is not None else self._mtls_ssl_context()
        return httpx.Client(verify=ctx, timeout=10.0)

    def hello(
        self, *, ssl_context: ssl.SSLContext | None = None, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        with self._operational_client(ssl_context=ssl_context) as client:
            response = client.post(
                f"{self.operational_base_url}/agent/hello",
                json={"protocol_version": PROTOCOL_VERSION},
                headers=self._native_headers(headers),
            )
        if response.status_code == 200:
            self.connection_id = response.json().get("connection_id")
        return response

    def heartbeat(
        self, *, ssl_context: ssl.SSLContext | None = None, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        with self._operational_client(ssl_context=ssl_context) as client:
            return client.post(
                f"{self.operational_base_url}/agent/heartbeat",
                json={"protocol_version": PROTOCOL_VERSION},
                headers=self._connection_headers(headers),
            )

    def jobs_next(
        self, *, ssl_context: ssl.SSLContext | None = None, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        with self._operational_client(ssl_context=ssl_context) as client:
            return client.get(
                f"{self.operational_base_url}/agent/jobs/next", headers=self._connection_headers(headers)
            )

    def claim(
        self,
        job_id: uuid.UUID,
        *,
        ssl_context: ssl.SSLContext | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        with self._operational_client(ssl_context=ssl_context) as client:
            return client.post(
                f"{self.operational_base_url}/agent/jobs/{job_id}/claim",
                json={"protocol_version": PROTOCOL_VERSION},
                headers=self._connection_headers(headers),
            )

    def request_credential(
        self,
        job_id: uuid.UUID,
        *,
        ssl_context: ssl.SSLContext | None = None,
        headers: dict[str, str] | None = None,
        extra_body: dict | None = None,
    ) -> httpx.Response:
        """Domain E1-B -- arnes de prueba UNICAMENTE, nunca el Agente
        Windows productivo (mismo principio ya congelado en Domain D).
        `extra_body` es SOLO para probar el esquema estricto (prueba AC del
        plan: `extra="forbid"` de `CredentialDeliveryRequest` debe rechazar
        cualquier campo no reconocido, ej. un intento de inyectar `ruc`) --
        el Agente Windows real nunca construiria un payload asi."""
        body = {"protocol_version": PROTOCOL_VERSION}
        if extra_body:
            body.update(extra_body)
        with self._operational_client(ssl_context=ssl_context) as client:
            return client.post(
                f"{self.operational_base_url}/agent/jobs/{job_id}/credential",
                json=body,
                headers=self._connection_headers(headers),
            )

    def report_status(
        self, job_id: uuid.UUID, status: str, reason_code: str | None = None, session_id: uuid.UUID | None = None
    ) -> httpx.Response:
        payload: dict = {"protocol_version": PROTOCOL_VERSION, "job_id": str(job_id), "status": status}
        if reason_code is not None:
            payload["reason_code"] = reason_code
        if session_id is not None:
            payload["session_id"] = str(session_id)
        with self._operational_client() as client:
            return client.post(
                f"{self.operational_base_url}/agent/jobs/{job_id}/status",
                json=payload,
                headers=self._connection_headers(),
            )

    def report_error(self, *, job_id: uuid.UUID | None, error_code: str) -> httpx.Response:
        with self._operational_client() as client:
            return client.post(
                f"{self.operational_base_url}/agent/error",
                json={
                    "protocol_version": PROTOCOL_VERSION,
                    "job_id": str(job_id) if job_id else None,
                    "error_code": error_code,
                },
                headers=self._connection_headers(),
            )

    # --- utilidades de prueba (nunca en el agente real) ---

    def raw_csr_pem(self) -> bytes:
        assert self._csr_pem is not None
        return self._csr_pem

    def client_cert_pem(self) -> bytes:
        assert self._client_cert_pem is not None
        return self._client_cert_pem
