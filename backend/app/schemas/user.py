import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr

from app.schemas.common import PasswordStr, ReasonStr

Role = Literal["admin", "contador", "asistente"]


class ManagedUserOut(BaseModel):
    """Respuesta de los endpoints de `/api/v1/users` -- deliberadamente
    distinto de `CurrentUserOut` (schemas/auth.py): sin `permissions`
    calculado, porque no hay necesidad funcional de mostrar los permisos
    efectivos de cada fila en la pantalla de gestión de usuarios."""

    id: uuid.UUID
    email: str
    full_name: str
    role: str
    is_active: bool
    must_change_password: bool
    deactivated_at: datetime | None
    last_login_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    full_name: str
    role: Role
    password: PasswordStr


class ChangeRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role
    reason: ReasonStr


class DeactivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: ReasonStr


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_password: PasswordStr
    reason: ReasonStr
