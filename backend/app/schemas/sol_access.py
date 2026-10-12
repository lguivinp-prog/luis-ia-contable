"""Fase 5, Bloque 5.2, Domain F: esquemas de la API de usuario del acceso SOL supervisado.

NINGUN esquema contiene credenciales, RUC, usuario/clave SOL, tokens, huellas de certificado, ni texto de
excepciones del agente: solo estado autoritativo y metadatos seguros."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

# Allowlist cerrada de destinos LIVE-VALIDADOS (M2). El frontend solo envia este enum: nunca URL, selector ni script.
SolDestination = Literal["MENU_SOL", "FICHA_RUC"]


class CreateSolAccessSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: UUID
    idempotency_key: UUID
    destination: SolDestination | None = None
    # solo con FICHA_RUC: extrae la Ficha y genera una PROPUESTA pendiente de revision humana (requiere client:write)
    extract_ficha: bool = False


class SolDestinationsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destinations: list[SolDestination]


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
    # M2: codigo de diagnostico estructural del fallo (lista cerrada; nunca texto del portal). Solo en sesiones FAILED.
    diagnostic_code: str | None = None
