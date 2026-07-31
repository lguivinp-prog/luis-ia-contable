"""Schemas de integracion Google OAuth (Fase 3, Bloque 3.1). `capability`
solo admite `IDENTITY` en este bloque -- CALENDAR/DRIVE se agregaran al
Literal en los bloques 3.2/3.3, nunca antes (ver Seccion 5 del plan v3)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Allowlist de rutas relativas de retorno -- nunca una URL absoluta ni un
# host/esquema provisto por el cliente (previene open redirect).
_ALLOWED_RETURN_PATHS = frozenset({"/settings/integrations", "/settings/integrations/google"})


class GoogleAuthorizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability: Literal["IDENTITY"]
    return_path: str

    @field_validator("return_path")
    @classmethod
    def _validate_return_path(cls, value: str) -> str:
        if value not in _ALLOWED_RETURN_PATHS:
            raise ValueError("return_path no esta en la lista permitida de rutas relativas")
        return value


class GoogleAuthorizeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    authorization_url: str
    attempt_id: UUID


class GoogleConnectionStatusOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    configured: bool
    connected: bool
    status: str | None
    google_account_email: str | None
    identity_authorized: bool
    calendar_authorized: bool
    drive_authorized: bool
    authorized_at: datetime | None
    reauth_reason: str | None


class GoogleDisconnectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    disconnected: bool
    revocation_succeeded: bool


class GoogleCallbackErrorOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error_code: str = Field(..., description="Codigo normalizado de la taxonomia de errores")
