"""Fase 5, Bloque 5.2, Domain F: esquemas de la API de usuario del acceso SOL supervisado.

NINGUN esquema contiene credenciales, RUC, usuario/clave SOL, tokens, huellas de certificado, ni texto de
excepciones del agente: solo estado autoritativo y metadatos seguros."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

# Destinos CONOCIDOS (M3). El frontend solo envia este enum: nunca URL, selector ni script. Que sea conocido NO lo habilita: lo decide
# el catalogo del backend (`app.core.sunat_destinations`); un destino deshabilitado se rechaza con SOL_DESTINATION_DISABLED.
SolDestination = Literal[
    "MENU_SOL", "FICHA_RUC", "SIRE", "SIRE_RVIE", "SIRE_RCE", "SIRE_GENERACION_REGISTROS", "DECLARACIONES_Y_PAGOS", "DECLARA_FACIL", "TRAMITES_Y_CONSULTAS"
]


class CreateSolAccessSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: UUID
    idempotency_key: UUID
    destination: SolDestination | None = None
    # M3: perfil de credencial a usar. OBLIGATORIO cuando el cliente tiene mas de un perfil (jamas se elige uno por el usuario ni se reintenta con otro).
    credential_id: UUID | None = None
    # solo con FICHA_RUC: extrae la Ficha y genera una PROPUESTA pendiente de revision humana (requiere client:write)
    extract_ficha: bool = False
    # Contexto operativo (solo auditoria): periodo del workspace mensual desde el que se abrio el acceso. NO cambia estados de cumplimiento.
    origin_tax_period_id: UUID | None = None


class SolDestinationInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: SolDestination
    label: str
    group: str
    validation: str  # LIVE_VALIDATED | NOT_VALIDATED
    enabled: bool
    evidence: str = "NONE"  # COMPLETE | ARRIVAL_ONLY | ALIAS | CONTAINED | NONE
    alias_of: SolDestination | None = None
    contained_in: SolDestination | None = None
    open_only: bool = False
    requires_profile_proof: bool = False  # estructura validada, pero cada perfil/cliente necesita su propia prueba real
    acceptance_eligible: bool = False  # puede probarse en vivo con una autorizacion de aceptacion (RECON_COMPLETE, no solo-apertura, aun no habilitado)
    offered: bool = True  # tarjeta propia en la UI (False: alias, contenido en otro destino o identificador historico)


class AcceptanceGrantRequest(BaseModel):
    """Autorizacion de UNA prueba real: propietario + cliente + perfil + destino. Nunca URL, selector ni credencial."""

    model_config = ConfigDict(extra="forbid")
    client_id: UUID
    credential_id: UUID
    destination: SolDestination
    # Confirmacion explicita de que el propietario autoriza el acceso real (no hay autorizacion implicita)
    confirm_real_access: Literal[True]


class AcceptanceResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result: Literal["PASS", "FAIL"]
    note: str | None = None


class AcceptanceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    client_id: UUID
    credential_id: UUID
    destination: SolDestination
    status: Literal["AUTHORIZED", "CONSUMED", "REVOKED", "EXPIRED"]
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None
    session_id: UUID | None = None
    result: Literal["PASS", "FAIL"] | None = None
    result_note: str | None = None
    # Derivado por el SERVIDOR desde la sesion real (autorizacion / consumo / fallo / llegada real / confirmacion humana)
    phase: Literal[
        "AUTHORIZED", "REVOKED", "EXPIRED", "CONSUMED_RUNNING", "SESSION_FAILED", "ARRIVAL_AWAITING_CONFIRMATION", "CONFIRMED_PASS", "CONFIRMED_FAIL"
    ] = "AUTHORIZED"
    session_status: str | None = None
    session_operation_result: str | None = None
    diagnostic_code: str | None = None  # lista cerrada; nunca texto del portal
    result_source: Literal["OWNER", "SYSTEM"] | None = None


class AcceptanceListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acceptances: list[AcceptanceOut]


class SolDestinationsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destinations: list[SolDestination]  # solo los HABILITADOS (compatibilidad M2)
    catalog: list[SolDestinationInfo] = []  # todos los CONOCIDOS con su estado de validacion


class SolAgentOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    display_name: str
    status: str
    paired_at: datetime
    last_seen_at: datetime | None
    # Disponible = agente ACTIVE con conexion viva ahora en el broker y sin job activo.
    available: bool


class SolAgentsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    broker_enabled: bool
    agents: list[SolAgentOut]


class SolAccessSessionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    client_id: UUID
    scope: str
    status: str
    terminal_reason_code: str | None
    operation_result: str | None
    created_at: datetime
    started_at: datetime | None
    waiting_for_user_at: datetime | None
    completed_at: datetime | None
    expires_at: datetime
    version: int
    is_terminal: bool
    destination: str | None = None
    extract_ficha: bool = False
    human_control_at: datetime | None = None
    credential_id: UUID | None = None  # M3: perfil con el que se abrio el acceso (solo el id; el nombre se resuelve con la lista de perfiles)
    acceptance_test: bool = False  # M3: prueba real controlada (destino aun no habilitado globalmente)
    # M2: codigo de diagnostico estructural del fallo (lista cerrada; nunca texto del portal). Solo en sesiones FAILED.
    diagnostic_code: str | None = None


# --- M3: cuatro estados que NUNCA son equivalentes -------------------------------------------------------------------
# reconocido estructuralmente (`recognized`) != habilitado en el sistema (`enabled`) != permitido para ESTA credencial (`permitted`, observado por el
# portal en sesiones reales, nunca inferido del nombre del perfil) != acceso realmente completado por el agente (`completed`).
class SolDestinationPermissionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination: SolDestination
    recognized: str  # COMPLETE | ARRIVAL_ONLY | ALIAS | CONTAINED | NONE  (evidencia estructural del reconocimiento)
    enabled: bool
    permitted: Literal["UNKNOWN", "OBSERVED_ALLOWED", "OBSERVED_NOT_IN_MENU"]
    completed: bool
    last_session_id: UUID | None = None
    proven: bool = False  # este perfil (de este cliente) supero la aceptacion real del destino
    available: bool = False  # habilitado Y (sin prueba exigida o ya probado por este perfil): se puede ofrecer el boton de apertura


class SolCredentialPermissionsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credential_id: UUID
    profile_name: str
    credential_kind: str
    status: str
    destinations: list[SolDestinationPermissionOut]


class SolCredentialDestinationsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credentials: list[SolCredentialPermissionsOut]
