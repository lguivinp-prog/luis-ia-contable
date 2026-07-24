import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr

from app.schemas.common import PasswordStr


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str


class CurrentUserOut(BaseModel):
    """Respuesta de `GET /auth/me` y `POST /auth/login` -- unico lugar donde
    aparece `permissions` calculado (ver ADR-0050). Nunca se reutiliza como
    schema de gestion de usuarios (ver `ManagedUserOut` en `schemas/user.py`)."""

    id: uuid.UUID
    email: str
    full_name: str
    role: str
    permissions: list[str]
    must_change_password: bool

    model_config = {"from_attributes": True}


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str
    new_password: PasswordStr


class SessionOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    ip_address: str | None
    user_agent: str | None
    is_current: bool

    model_config = {"from_attributes": True}


class SessionPolicyOut(BaseModel):
    inactivity_timeout_minutes: int
    inactivity_warning_seconds: int
    session_ttl_hours: int


class KeepAliveOut(BaseModel):
    status: str
    last_seen_at: datetime
