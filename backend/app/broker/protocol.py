"""Esquemas de protocolo del broker (Fase 5, Bloque 5.2, Domain D -- ver
docs/phase5/block5_2/). `PROTOCOL_VERSION=1` -- una version desconocida
falla cerrado (`ProtocolVersionUnsupportedError`), nunca parseo best-effort
(seccion 7 del encargo). Todos los modelos usan `extra="forbid"`: un campo
no declarado en el esquema rechaza el mensaje completo en vez de ignorarlo
silenciosamente -- ningun campo libre arbitrario (seccion 7).

Ningun modelo de aqui transporta PII ni secretos: metadata de job limitada a
`job_id`/`session_id`/`scope`/`expires_at`/`protocol_version` (seccion 32 del
encargo) -- nunca username/password/cookies/documentos. El token de job
NUNCA aparece en ningun modelo de este archivo (se resuelve internamente en
el broker, ver `pending_dispatch.py` -- nunca cruza la red hacia el agente,
ver informe final Domain D)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROTOCOL_VERSION = 1


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- bootstrap (listener de pairing, sin mTLS) ---


class PairingCsrSubmitRequest(_StrictModel):
    """El agente presenta su CSR real (clave privada generada y retenida
    localmente -- este esquema NUNCA tiene un campo para ella) junto con el
    codigo de pairing de 6 digitos ya mostrado por un humano (Domain C,
    codigo inalterado)."""

    protocol_version: int
    pairing_id: uuid.UUID
    pairing_code: str = Field(min_length=6, max_length=6)
    csr_pem: str = Field(min_length=1, max_length=8192)


class PairingCsrSubmitResponse(_StrictModel):
    protocol_version: int = PROTOCOL_VERSION
    agent_id: uuid.UUID
    certificate_pem: str
    certificate_not_after: datetime


# --- canal operacional (mTLS obligatorio) ---


class AgentHello(_StrictModel):
    protocol_version: int


class HeartbeatRequest(_StrictModel):
    protocol_version: int


class JobAvailableResponse(_StrictModel):
    """`None` si no hay job disponible para el agente autenticado -- nunca
    se ofrece un job de otro agente (seccion 32: sin broadcast)."""

    protocol_version: int = PROTOCOL_VERSION
    job_id: uuid.UUID | None
    session_id: uuid.UUID | None
    scope: str | None
    expires_at: datetime | None


class JobClaimRequest(_StrictModel):
    """Deliberadamente SIN `agent_id`: la identidad del agente viene
    exclusivamente del certificado mTLS de la conexion (seccion 35 del
    encargo) -- incluirlo aqui seria redundante y abriria la puerta a que un
    agente reclame en nombre de otro."""

    protocol_version: int


class JobClaimResult(_StrictModel):
    """Resultado generico -- nunca distingue el motivo especifico de
    rechazo ante el agente (seccion 42, mismo principio que
    `JobClaimInvalidError` en Domain C). NUNCA incluye el job_token en
    claro: el broker lo resuelve y lo presenta a `claim_job` internamente,
    sin serializarlo hacia el agente (ver informe final, decision de
    diseno)."""

    protocol_version: int = PROTOCOL_VERSION
    job_id: uuid.UUID
    accepted: bool


# Fase 5, Bloque 5.2, Domain E3.1: codigos de razon que el Agente puede reportar --
# lista CERRADA (nunca una cadena arbitraria persistida a ciegas). Subconjunto de
# `SolAccessTerminalReasonCode`: se excluyen los que el backend decide por su
# cuenta (cancelacion/expiracion/vault/agente/job) y `WRONG_TAXPAYER_CONTEXT`.
AGENT_REPORTABLE_REASON_CODES = (
    "AUTH_FAILED",
    "PORTAL_CHANGED",
    "AUTH_HOST_CHANGED",
    "WRONG_LOGIN_MODE",
    "HUMAN_CHALLENGE_TIMEOUT",
    "BROWSER_CLOSED",
    "CLEANUP_FAILED",
    "CREDENTIAL_UNAVAILABLE",
    "INTERNAL_ERROR",
)


class JobStatusReport(_StrictModel):
    """Resultado semantico del navegador, ya validado por el backend antes de
    cualquier transicion (el Agente NUNCA elige un estado de BD):

    - `COMPLETED`: autenticacion hasta el landing lograda (AUTH_SUCCEEDED);
    - `WAITING_FOR_USER`: challenge humano pendiente (no terminal);
    - `PREAUTH_READY` (Domain F): el portal ya fue validado por completo y el agente esta listo para pedir la
      credencial; el broker revalida y, solo entonces, autoriza la entrega (grant de un solo uso). No es un
      estado de sesion ni de job: unicamente esta senal, sin secretos;
    - `FAILED` + `reason_code` (lista cerrada): fallo terminal.

    `session_id` es opcional pero, si se envia, debe coincidir con la sesion del
    job (enlace verificado en servidor). Nunca lleva credenciales, RUC, DOM,
    texto de pagina ni URLs."""

    protocol_version: int
    job_id: uuid.UUID
    status: str = Field(pattern=r"^(COMPLETED|FAILED|WAITING_FOR_USER|PREAUTH_READY)$")
    reason_code: str | None = Field(default=None, pattern=r"^(" + "|".join(AGENT_REPORTABLE_REASON_CODES) + r")$")
    session_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _reason_code_only_with_failed(self) -> JobStatusReport:
        if (self.status == "FAILED") != (self.reason_code is not None):
            raise ValueError("reason_code is required for FAILED and forbidden otherwise")
        return self


class SessionCancelNotice(_StrictModel):
    """Push secundario e informativo unicamente -- el estado durable en BD
    ya es autoritativo antes de que este mensaje exista (seccion 39). El
    agente no necesita confirmar recepcion para que la cancelacion sea
    valida."""

    protocol_version: int = PROTOCOL_VERSION
    job_id: uuid.UUID
    reason_code: str = "CANCELLED_BY_OPERATOR"


class AgentErrorReport(_StrictModel):
    protocol_version: int
    job_id: uuid.UUID | None
    error_code: str = Field(min_length=1, max_length=64)


# --- Fase 5, Bloque 5.2, Domain E1-B: entrega de credencial SOL (ver
# docs/phase5/block5_2/BLOCK5_2_SECRET_DELIVERY_ARCHITECTURE.md, secciones
# 7/15/23-24 del encargo de Domain E1-B) ---


class CredentialDeliveryRequest(_StrictModel):
    """Deliberadamente SIN ningun otro campo (seccion 31 del encargo): sin
    token de grant, sin `credential_id`, sin `ruc`, sin
    `sol_username`/`sol_password`, sin `agent_id`, sin `tenant_id`, sin
    `user_id`, sin `app_session_id`. Toda relacion de confianza viene de
    la identidad mTLS + `job_id` de la URL + estado de backend -- nunca de
    este cuerpo."""

    protocol_version: int


class CredentialDeliveryResponse(_StrictModel):
    """Contrato de secreto minimo (seccion 36 del encargo,
    `MINIMUM_AGENT_LOGIN_PAYLOAD` congelado en Domain E1-A.1). `__repr__`/
    `__str__` redactados deliberadamente -- ningun log/traceback/`pytest
    -l` puede exponer el texto plano por accidente (mismo principio ya
    productivo en `JobCreateResult`/`PairingCreateResult` de Domain C).
    `frozen=True` (heredado de `_StrictModel`) NO implica redaccion por si
    solo -- Pydantic seria expondria los valores en su `__repr__` por
    defecto si no se sobrescribiera aqui explicitamente."""

    protocol_version: int = PROTOCOL_VERSION
    ruc: str = Field(min_length=11, max_length=11)
    sol_username: str = Field(min_length=1, max_length=256)
    sol_password: str = Field(min_length=1, max_length=256)

    def __repr__(self) -> str:
        return "CredentialDeliveryResponse(ruc='***REDACTED***', sol_username='***REDACTED***', sol_password='***REDACTED***')"

    def __str__(self) -> str:
        return self.__repr__()


# --- limites de tamano (seccion 62 del encargo: acotar antes de trabajo caro) ---

MAX_MESSAGE_BYTES = 16_384
MAX_CSR_PEM_BYTES = 8192

# Fase 5, Bloque 5.2, Domain E1-A/E1-B (seccion 19 del documento de
# arquitectura, seccion 70 del encargo de Domain E1-B): cota estricta y
# pequena, deliberadamente MENOR que `MAX_MESSAGE_BYTES` generico del
# broker -- tres campos de texto corto nunca necesitan 16 KB. Ningun
# certificado/documento se agrupa jamas con el payload de credencial.
MAX_CREDENTIAL_DELIVERY_RESPONSE_BYTES = 1024
