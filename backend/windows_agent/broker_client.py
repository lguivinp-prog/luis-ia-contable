"""Fase 5, Bloque 5.2, Domain E2 -- cliente IPC productivo del Agente
hacia el broker local (Domain D/E1-B). Reutiliza la filosofia de backoff
ya validada en Spike B (`broker_client.py`: secuencia acotada 1s/2s/4s/8s
+ jitter, solo para fallos de TRANSPORTE, nunca errores de aplicacion) --
DESVIACION DELIBERADA respecto al spike en un punto: el spike envolvia
`credential_a`/`credential_b` con el MISMO backoff generico que el resto
de endpoints; secciones 56-58 del encargo E2 exigen exactamente lo
opuesto para el endpoint de credencial productivo -- **nunca** un
reintento generico, cualquier ambiguedad de transporte falla cerrado. El
spike es evidencia, no vinculante (seccion 4 del encargo)."""

from __future__ import annotations

import json
import random
import ssl
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar

import httpx

_T = TypeVar("_T")

CONNECTION_ID_HEADER = "X-Sol-Connection-Id"
AGENT_PROTOCOL_HEADER = "X-Sol-Agent-Protocol"

# Mismos valores validados en Spike B (seccion 22/32 del SPIKE_REPORT:
# "documentado explicitamente como no definitivo") -- acotado, nunca
# infinito, con jitter para evitar sincronizacion de reintentos.
BACKOFF_SEQUENCE_SECONDS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)
_MAX_JITTER_SECONDS = 0.5

# Mismo limite congelado en Domain E1-A/E1-B (`app.broker.protocol.
# MAX_CREDENTIAL_DELIVERY_RESPONSE_BYTES`) -- duplicado deliberadamente
# como literal aqui (nunca importado desde `app.broker.protocol`) para
# que este paquete productivo pueda distribuirse/ejecutarse de forma
# independiente del checkout completo del backend en el futuro (seccion 6
# del encargo: "coherent product Windows Agent package"); un cambio a ese
# limite en Domain D/E1-B debe replicarse aqui explicitamente, nunca
# asumido implicito via import.
MAX_CREDENTIAL_RESPONSE_BYTES = 1024
_REQUIRED_CREDENTIAL_RESPONSE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}
_CREDENTIAL_RESPONSE_ALLOWED_FIELDS = {"protocol_version", "ruc", "sol_username", "sol_password"}


def _read_bounded_body(response: httpx.Response, max_bytes: int) -> bytes | None:
    """Lee el cuerpo EN STREAMING con tope duro. Devuelve `None` (y deja de
    leer de inmediato) si el cuerpo supera `max_bytes` -- ya sea por
    `Content-Length` declarado o por bytes realmente acumulados. Nunca
    acumula mas de `max_bytes` bytes y nunca registra ni devuelve lo
    recibido en el caso excedido."""
    declared = response.headers.get("Content-Length")
    if declared is not None:
        try:
            if int(declared) > max_bytes:
                return None
        except ValueError:
            return None
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


class BrokerUnreachableError(Exception):
    """Se agoto el backoff acotado -- requiere accion humana. El llamador
    nunca debe asumir que el job sigue vigente ni reenviar automaticamente
    ningun secreto tras esta excepcion."""


class BrokerProtocolError(Exception):
    """La respuesta del broker no cumple el contrato esperado (esquema,
    tamaño, cabeceras) -- nunca incluye el cuerpo crudo de la respuesta en
    el mensaje (seccion 85 del encargo: no ecoar el payload en un error de
    parseo/validacion)."""


class CredentialFetchError(Exception):
    """Fallo ESTABLE y no reintentable automaticamente de la solicitud de
    credencial (seccion 55/57 del encargo) -- `code` es siempre un valor
    generico ya devuelto por el broker (`CREDENTIAL_UNAVAILABLE`,
    `CONNECTION_REPLACED`, etc.) o `TRANSPORT_AMBIGUOUS` para cualquier
    fallo de red/timeout en este endpoint especifico -- nunca se
    reintenta desde aqui."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def call_with_backoff(
    fn: Callable[[], _T],
    *,
    sleep: Callable[[float], None] = time.sleep,
    backoff_sequence: tuple[float, ...] = BACKOFF_SEQUENCE_SECONDS,
) -> _T:
    """Reintenta SOLO `httpx.TransportError`/`TimeoutException` -- nunca
    envuelve `raise_for_status()` (eso corre siempre FUERA de esta
    funcion, en el llamador, para que un 401/409 de aplicacion nunca se
    reintente como si fuera un problema de red transitorio)."""
    last_exc: Exception | None = None
    delays = (0.0, *backoff_sequence)
    for delay in delays:
        if delay:
            sleep(delay + random.uniform(0, _MAX_JITTER_SECONDS))
        try:
            return fn()
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last_exc = exc
            continue
    raise BrokerUnreachableError(
        f"broker inalcanzable tras {len(backoff_sequence)} reintentos con backoff"
    ) from last_exc


def build_trust_only_ssl_context(ca_certificate_pem: bytes) -> ssl.SSLContext:
    """Listener de PAIRING (bootstrap, sin certificado de cliente todavia
    -- el agente aun no tiene identidad)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cadata=ca_certificate_pem.decode("ascii"))
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


@dataclass(frozen=True)
class PairingResult:
    agent_id: uuid.UUID
    certificate_pem: bytes
    certificate_not_after: datetime


def submit_pairing_csr(
    *,
    pairing_base_url: str,
    ca_certificate_pem: bytes,
    pairing_id: uuid.UUID,
    pairing_code: str,
    csr_pem: bytes,
    protocol_version: int,
    timeout: float,
) -> PairingResult:
    """Paso 18 del encargo -- el codigo de pairing se usa aqui UNA VEZ y
    nunca se retiene mas alla de esta llamada (el llamador -- `pairing.py`
    -- es responsable de soltar su propia referencia inmediatamente
    despues)."""
    ctx = build_trust_only_ssl_context(ca_certificate_pem)
    with httpx.Client(verify=ctx, timeout=timeout, follow_redirects=False, trust_env=False) as client:
        response = call_with_backoff(
            lambda: client.post(
                f"{pairing_base_url}/pairing/csr",
                json={
                    "protocol_version": protocol_version,
                    "pairing_id": str(pairing_id),
                    "pairing_code": pairing_code,
                    "csr_pem": csr_pem.decode("ascii"),
                },
                headers={AGENT_PROTOCOL_HEADER: str(protocol_version)},
            )
        )
    response.raise_for_status()
    body = response.json()
    return PairingResult(
        agent_id=uuid.UUID(body["agent_id"]),
        certificate_pem=body["certificate_pem"].encode("ascii"),
        certificate_not_after=datetime.fromisoformat(body["certificate_not_after"]),
    )


class CredentialMaterial:
    """Contenedor MUTABLE deliberadamente (a diferencia de los DTO
    congelados del lado del broker) -- seccion 45 del encargo: permite
    soltar activamente las referencias (`_clear()`) tan pronto como el
    consumidor (`fetch_for_job`) termina, en vez de esperar a que el GC
    recolecte un objeto inmutable. `PYTHON_SECURE_ZEROIZATION_GUARANTEED=
    NO` sigue aplicando (los `str` en si son inmutables en CPython) --
    esto acota la VIDA DE LA REFERENCIA, nunca pretende un borrado fisico
    de memoria."""

    __slots__ = ("ruc", "sol_username", "sol_password")

    def __init__(self, *, ruc: str, sol_username: str, sol_password: str) -> None:
        self.ruc = ruc
        self.sol_username = sol_username
        self.sol_password = sol_password

    def __repr__(self) -> str:
        return (
            "CredentialMaterial(ruc='***REDACTED***', "
            "sol_username='***REDACTED***', sol_password='***REDACTED***')"
        )

    __str__ = __repr__

    def _clear(self) -> None:
        self.ruc = None  # type: ignore[assignment]
        self.sol_username = None  # type: ignore[assignment]
        self.sol_password = None  # type: ignore[assignment]


class OperationalBrokerClient:
    """Canal OPERACIONAL (mTLS estricto, Domain D). Cada metodo aplica
    `call_with_backoff` SOLO alrededor de la llamada de red -- excepto
    `_request_credential_once`, que deliberadamente NUNCA usa backoff
    (seccion 57/58 del encargo: politica de reintento separada para el
    endpoint de credencial, security-sensitive y de un solo uso).

    `trust_env=False` (secciones 87/88/89 del encargo): el broker es
    siempre loopback -- el trafico NUNCA debe enrutarse a traves de un
    proxy HTTP(S) del sistema (`HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY`), que
    `httpx` respetaria por defecto. `follow_redirects=False`: ningun
    endpoint del protocolo redirige jamas -- una redirreccion (a un
    destino potencialmente no-loopback) nunca debe seguirse
    automaticamente."""

    def __init__(
        self,
        *,
        operational_base_url: str,
        ssl_context: ssl.SSLContext,
        protocol_version: int,
        timeout: float,
        backoff_sequence: tuple[float, ...] = BACKOFF_SEQUENCE_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_url = operational_base_url
        self._protocol_version = protocol_version
        self._client = httpx.Client(
            verify=ssl_context, timeout=timeout, follow_redirects=False, trust_env=False
        )
        self._backoff_sequence = backoff_sequence
        self._sleep = sleep

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OperationalBrokerClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _with_backoff(self, fn: Callable[[], httpx.Response]) -> httpx.Response:
        return call_with_backoff(fn, sleep=self._sleep, backoff_sequence=self._backoff_sequence)

    def _connection_headers(self, connection_id: str) -> dict[str, str]:
        return {AGENT_PROTOCOL_HEADER: str(self._protocol_version), CONNECTION_ID_HEADER: connection_id}

    def hello(self) -> str:
        """Devuelve el `connection_id` -- el LLAMADOR es responsable de
        mantenerlo SOLO en memoria (seccion 29 del encargo): nunca disco,
        registro, logs, entorno, ni CLI."""
        response = self._with_backoff(
            lambda: self._client.post(
                f"{self._base_url}/agent/hello",
                json={"protocol_version": self._protocol_version},
                headers={AGENT_PROTOCOL_HEADER: str(self._protocol_version)},
            )
        )
        response.raise_for_status()
        return response.json()["connection_id"]

    def heartbeat(self, *, connection_id: str, holding_job_id: uuid.UUID | None = None) -> dict:
        payload: dict = {"protocol_version": self._protocol_version}
        if holding_job_id is not None:
            payload["holding_job_id"] = str(holding_job_id)
        response = self._with_backoff(
            lambda: self._client.post(
                f"{self._base_url}/agent/heartbeat",
                json=payload,
                headers=self._connection_headers(connection_id),
            )
        )
        response.raise_for_status()
        return response.json()

    def get_next_job(self, *, connection_id: str) -> dict:
        response = self._with_backoff(
            lambda: self._client.get(
                f"{self._base_url}/agent/jobs/next", headers=self._connection_headers(connection_id)
            )
        )
        response.raise_for_status()
        return response.json()

    def claim_job(self, *, connection_id: str, job_id: uuid.UUID) -> bool:
        """Devuelve `accepted` -- el agente NUNCA recibe el token de job
        (seccion 36 del encargo, `JOB_TOKEN_RECEIVED_BY_AGENT=NO`): el
        broker sigue resolviendolo internamente."""
        response = self._with_backoff(
            lambda: self._client.post(
                f"{self._base_url}/agent/jobs/{job_id}/claim",
                json={"protocol_version": self._protocol_version},
                headers=self._connection_headers(connection_id),
            )
        )
        response.raise_for_status()
        body = response.json()
        assert "job_token" not in body  # nunca -- ver el docstring
        return bool(body["accepted"])

    def report_status(
        self,
        *,
        connection_id: str,
        job_id: uuid.UUID,
        status: str,
        reason_code: str | None = None,
        session_id: uuid.UUID | None = None,
        diagnostic_code: str | None = None,
    ) -> None:
        """Resultado semantico (E3.1): `COMPLETED` / `WAITING_FOR_USER` / `FAILED`+`reason_code`
        (lista cerrada validada por el broker). Nunca lleva credenciales, RUC, DOM ni URLs."""
        payload: dict = {"protocol_version": self._protocol_version, "job_id": str(job_id), "status": status}
        if reason_code is not None:
            payload["reason_code"] = reason_code
        if diagnostic_code is not None:
            payload["diagnostic_code"] = diagnostic_code  # codigo estable de una lista cerrada; nunca texto libre
        if session_id is not None:
            payload["session_id"] = str(session_id)
        response = self._with_backoff(
            lambda: self._client.post(
                f"{self._base_url}/agent/jobs/{job_id}/status",
                json=payload,
                headers=self._connection_headers(connection_id),
            )
        )
        response.raise_for_status()

    def report_ficha(
        self, *, connection_id: str, job_id: uuid.UUID, session_id: uuid.UUID | None, payload: dict
    ) -> bool:
        """M2: reporta los campos ESTRUCTURADOS de la Ficha RUC (el backend valida con un esquema estricto). Devuelve
        `accepted`. Nunca HTML, DOM ni credenciales. Idempotente por sesion en el servidor (reintento seguro)."""
        body: dict = {
            "protocol_version": self._protocol_version,
            "job_id": str(job_id),
            "schema_version": int(payload["schema_version"]),
            "fields": payload["fields"],
            "malformed": list(payload.get("malformed", [])),
        }
        if session_id is not None:
            body["session_id"] = str(session_id)
        response = self._with_backoff(
            lambda: self._client.post(
                f"{self._base_url}/agent/jobs/{job_id}/ficha", json=body, headers=self._connection_headers(connection_id)
            )
        )
        response.raise_for_status()
        return bool(response.json().get("accepted"))

    def report_error(self, *, connection_id: str, job_id: uuid.UUID | None, error_code: str) -> None:
        response = self._with_backoff(
            lambda: self._client.post(
                f"{self._base_url}/agent/error",
                json={
                    "protocol_version": self._protocol_version,
                    "job_id": str(job_id) if job_id else None,
                    "error_code": error_code,
                },
                headers=self._connection_headers(connection_id),
            )
        )
        response.raise_for_status()

    # --- capacidad de solicitud de credencial (secciones 39-41 del
    # encargo). NUNCA invocada por el bucle normal de jobs de E2 -- ver
    # `runtime.py`. Consumida por E3. ---

    def _request_credential_once(self, *, connection_id: str, job_id: uuid.UUID) -> CredentialMaterial:
        # E2.1-A: el cuerpo se lee EN STREAMING con tope duro -- nunca se
        # bufferiza una respuesta sin cota (`response.content` ya habria
        # descargado todo). Se aborta al superar MAX_CREDENTIAL_RESPONSE_BYTES
        # sin ecoar ni registrar lo recibido, y sin reintento generico.
        try:
            with self._client.stream(
                "POST",
                f"{self._base_url}/agent/jobs/{job_id}/credential",
                json={"protocol_version": self._protocol_version},
                headers=self._connection_headers(connection_id),
            ) as response:
                status_code = response.status_code
                headers = response.headers
                raw_body = _read_bounded_body(response, MAX_CREDENTIAL_RESPONSE_BYTES)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            # Seccion 56/57: ambiguedad de transporte en ESTE endpoint
            # nunca se reintenta automaticamente -- el gate de entrega
            # pudo o no haberse consumido ya en el backend.
            raise CredentialFetchError("TRANSPORT_AMBIGUOUS") from exc

        if status_code != 200:
            code = "CREDENTIAL_REQUEST_FAILED"
            if raw_body is not None:
                try:
                    error_body = json.loads(raw_body)
                    if isinstance(error_body, dict) and isinstance(error_body.get("error"), str):
                        code = error_body["error"]
                except ValueError:
                    pass  # nunca ecoar el cuerpo crudo (seccion 85)
            raise CredentialFetchError(code)

        if raw_body is None:
            raise BrokerProtocolError("credential response exceeds the maximum allowed size")

        for header, expected in _REQUIRED_CREDENTIAL_RESPONSE_HEADERS.items():
            if headers.get(header) != expected:
                raise BrokerProtocolError("credential response is missing a required security header")

        try:
            body = json.loads(raw_body)
        except ValueError as exc:
            raise BrokerProtocolError("credential response is not valid JSON") from exc

        if not isinstance(body, dict) or (set(body) - _CREDENTIAL_RESPONSE_ALLOWED_FIELDS):
            raise BrokerProtocolError("credential response contains unexpected fields")
        try:
            return CredentialMaterial(
                ruc=body["ruc"], sol_username=body["sol_username"], sol_password=body["sol_password"]
            )
        except KeyError as exc:
            raise BrokerProtocolError("credential response is missing a required field") from exc

    @contextmanager
    def fetch_credential_for_job(
        self, *, connection_id: str, job_id: uuid.UUID
    ) -> Iterator[CredentialMaterial]:
        """Interfaz consumidora estrecha para E3 (seccion 43 del encargo):

            with client.fetch_credential_for_job(connection_id=cid, job_id=job_id) as credential:
                future_browser_operation(credential)

        Ata la vida del secreto a la operacion que lo consume -- nunca se
        devuelve al bucle general del agente ni se asigna a un campo de
        larga vida (seccion 44: nunca `agent.current_credential = ...`)."""
        material = self._request_credential_once(connection_id=connection_id, job_id=job_id)
        try:
            yield material
        finally:
            material._clear()
