"""Fase 5, Bloque 5.2, Domain F: esquemas de la API de usuario del acceso SOL supervisado.

NINGUN esquema contiene credenciales, RUC, usuario/clave SOL, tokens, huellas de certificado, ni texto de
excepciones del agente: solo estado autoritativo y metadatos seguros."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CreateSolAccessSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: UUID
    idempotency_key: UUID


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
