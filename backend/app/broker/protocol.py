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
    # M2: job que el agente SOSTIENE ahora (control humano incluido); permite reconciliar sesiones huerfanas tras un
    # reinicio y readoptar el job si cayo el broker. Un agente anterior lo omite (`None`).
    holding_job_id: uuid.UUID | None = None


class JobAvailableResponse(_StrictModel):
    """`None` si no hay job disponible para el agente autenticado -- nunca
    se ofrece un job de otro agente (seccion 32: sin broadcast)."""

    protocol_version: int = PROTOCOL_VERSION
    job_id: uuid.UUID | None
    session_id: uuid.UUID | None
    scope: str | None
    expires_at: datetime | None
    # M2: destino aprobado por el SERVIDOR (enum cerrado) y si debe extraerse la Ficha. Nunca URL/selector.
    destination: str | None = None
    extract_ficha: bool = False
    # M3: la sesion consumio una autorizacion de aceptacion real (el agente la acepta SOLO para contratos RECON_COMPLETE no solo-apertura)
    acceptance: bool = False


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


# M2: codigos de DIAGNOSTICO estructural que el Agente puede adjuntar a un fallo -- lista CERRADA (replica de
# `windows_agent.diagnostics.DIAGNOSTIC_CODES`; una prueba exige que coincidan). Nunca texto libre.
DIAGNOSTIC_CODES = frozenset(
    {
    "AMBIGUOUS_PORTAL_SURFACE",
    "AUTH_SURFACE_IN_FRAME",
    "BLOCKED_BY_INTERMEDIATE_SCREEN",
    "BROWSER_CLOSED_OR_FAILED",
    "CLEANUP_FAILED",
    "CONTACT_VALIDATION_INTERSTITIAL",
    "CREDENTIALS_REJECTED_BY_PORTAL",
    "CREDENTIAL_UNAVAILABLE",
    "DESTINATION_NOT_ALLOWED",
    "ENTRY_LINK_NOT_FOUND",
    "FICHA_CONTENT_NOT_READY",
    "FICHA_DESTINATION_MISMATCH",
    "FICHA_FRAME_NOT_VALIDATED",
    "FICHA_MENU_ENTRY_MISSING_OR_AMBIGUOUS",
    "FICHA_MENU_STRUCTURE_MISMATCH",
    "FICHA_REPORT_REJECTED",
    "FICHA_TAXPAYER_NOT_IDENTIFIED",
    "HUMAN_ACTION_REQUIRED",
    "HUMAN_CHALLENGE_TIMEOUT",
    "HUMAN_CONTROL_REPORT_REJECTED",
    "INFORMATIONAL_MODAL",
    "LANDING_BEFORE_AUTHENTICATION",
    "LANDING_IDENTITY_MISMATCH",
    "LANDING_PAGE_NOT_FOUND",
    "LANDING_TRANSIT_UNRESOLVED",
    "AUTH_NAVIGATION_STALLED",
    "DESTINATION_NOT_VALIDATED",
    "DESTINATION_ENTRY_NOT_FOUND",
    "DESTINATION_MENU_STRUCTURE_MISMATCH",
    "DESTINATION_IDENTITY_MISMATCH",
    "DESTINATION_FRAME_NOT_VALIDATED",
    "DESTINATION_PAGE_NOT_VALIDATED",
    "LOGIN_CHANGED_BEFORE_SUBMIT",
    "LOGIN_CONTROL_MISMATCH",
    "LOGIN_MODE_CONTROLS_MISSING",
    "LOGIN_MODE_STATE_AMBIGUOUS",
    "LOGIN_PATH_MISMATCH",
    "LOGIN_RUC_MODE_ACTIVATION_FAILED",
    "LOGIN_RUC_VISIBILITY_MISMATCH",
    "LOGIN_SUBMIT_NAME_MISMATCH",
    "LOGIN_SURFACE_NOT_REACHED",
    "LOGIN_TITLE_MISMATCH",
    "NAVIGATION_BUDGET_EXHAUSTED",
    "NAVIGATION_TIMEOUT",
    "RETURNED_TO_ENTRY_PAGE",
    "SECOND_SUBMISSION_REFUSED",
    "SHELL_LOGIN_CONTROLS_PRESENT",
    "SHELL_MARKER_MISSING_OR_AMBIGUOUS",
    "SHELL_STRUCTURE_MISMATCH",
    "UNCLASSIFIED_PORTAL_CHANGE",
    "UNEXPECTED_ERROR",
    "UNEXPECTED_EXECUTOR_ERROR",
    "UNKNOWN_PAGE_OR_HOST",
    }
)


class JobStatusReport(_StrictModel):
    """Resultado semantico del navegador, ya validado por el backend antes de
    cualquier transicion (el Agente NUNCA elige un estado de BD):

    - `COMPLETED`: autenticacion hasta el landing lograda (AUTH_SUCCEEDED);
    - `WAITING_FOR_USER`: challenge humano pendiente (no terminal);
    - `PREAUTH_READY` (Domain F): el portal ya fue validado por completo y el agente esta listo para pedir la
      credencial; el broker revalida y, solo entonces, autoriza la entrega (grant de un solo uso). No es un
      estado de sesion ni de job: unicamente esta senal, sin secretos;
    - `HUMAN_CONTROL` (M2): destino validado y automatizacion TERMINADA; el navegador sigue abierto bajo control del
      humano (NO terminal); despues el agente reporta `COMPLETED` cuando el humano cierra el navegador;
    - `FAILED` + `reason_code` (lista cerrada): fallo terminal.

    `session_id` es opcional pero, si se envia, debe coincidir con la sesion del
    job (enlace verificado en servidor). Nunca lleva credenciales, RUC, DOM,
    texto de pagina ni URLs."""

    protocol_version: int
    job_id: uuid.UUID
    status: str = Field(pattern=r"^(COMPLETED|FAILED|WAITING_FOR_USER|PREAUTH_READY|HUMAN_CONTROL)$")
    reason_code: str | None = Field(default=None, pattern=r"^(" + "|".join(AGENT_REPORTABLE_REASON_CODES) + r")$")
    session_id: uuid.UUID | None = None
    diagnostic_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{3,48}$")

    @model_validator(mode="after")
    def _reason_code_only_with_failed(self) -> JobStatusReport:
        if (self.status == "FAILED") != (self.reason_code is not None):
            raise ValueError("reason_code is required for FAILED and forbidden otherwise")
        if self.diagnostic_code is not None and self.status not in {"FAILED", "WAITING_FOR_USER"}:
            raise ValueError("diagnostic_code is only allowed with FAILED or WAITING_FOR_USER")
        return self


class FichaReport(_StrictModel):
    """M2: campos ESTRUCTURADOS de la Ficha RUC extraidos por el Agente (lista blanca de rotulos). El backend los valida
    con un esquema estricto (`FichaRucFieldsV1`) y es la autoridad; aqui solo se acota el tamano/forma del mensaje.
    Nunca HTML, DOM, texto de pagina arbitrario ni credenciales."""

    protocol_version: int
    job_id: uuid.UUID
    session_id: uuid.UUID | None = None
    schema_version: int = Field(ge=1, le=100)
    fields: dict = Field(max_length=40)
    malformed: list[str] = Field(default_factory=list, max_length=20)


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
# La Ficha RUC completa (tributos, representantes, establecimientos) es mayor que un mensaje de control; solo ese endpoint admite mas.
MAX_FICHA_MESSAGE_BYTES = 128 * 1024
MAX_CSR_PEM_BYTES = 8192

# Fase 5, Bloque 5.2, Domain E1-A/E1-B (seccion 19 del documento de
# arquitectura, seccion 70 del encargo de Domain E1-B): cota estricta y
# pequena, deliberadamente MENOR que `MAX_MESSAGE_BYTES` generico del
# broker -- tres campos de texto corto nunca necesitan 16 KB. Ningun
# certificado/documento se agrupa jamas con el payload de credencial.
MAX_CREDENTIAL_DELIVERY_RESPONSE_BYTES = 1024
