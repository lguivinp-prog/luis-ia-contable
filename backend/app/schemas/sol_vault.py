"""Esquemas de la boveda de credenciales SOL (Fase 5, Bloque 5.1, ver
ADR-0068). Ninguna respuesta de este modulo incluye una contrasena en
claro, un ciphertext, un salt, una clave envuelta, ni el handle interno de
`VaultUnlockRegistry`."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

_MIN_VAULT_PASSWORD_LENGTH = 14
_MAX_VAULT_PASSWORD_LENGTH = 256


def _validate_vault_password(value: str) -> str:
    """Politica de contrasena personal de boveda (Seccion 19): minimo 14
    caracteres, maximo 256, se permiten frases (sin exigir simbolos
    arbitrarios). Deliberadamente distinta de la politica de contrasena de
    login -- nunca se reutiliza esta contrasena automaticamente desde el
    login."""
    if len(value) < _MIN_VAULT_PASSWORD_LENGTH:
        raise ValueError(
            f"la contrasena de boveda debe tener al menos {_MIN_VAULT_PASSWORD_LENGTH} caracteres"
        )
    if len(value) > _MAX_VAULT_PASSWORD_LENGTH:
        raise ValueError(f"la contrasena de boveda no puede exceder {_MAX_VAULT_PASSWORD_LENGTH} caracteres")
    return value


class InitializeVaultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    personal_vault_password: str

    @field_validator("personal_vault_password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        return _validate_vault_password(value)


class ConfirmRecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recovery_code: str


class ExpectedVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int


class RecoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recovery_code: str
    new_personal_vault_password: str

    @field_validator("new_personal_vault_password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        return _validate_vault_password(value)


class UnlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    personal_vault_password: str


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_personal_vault_password: str

    @field_validator("new_personal_vault_password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        return _validate_vault_password(value)


class CreateInvitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_user_id: UUID


class AcceptInvitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_personal_vault_password: str

    @field_validator("new_personal_vault_password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        return _validate_vault_password(value)


class RevokeSlotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int


class CreateSolCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credential_kind: str
    # Limites = columnas reales (`client_sol_credentials`): un valor mas largo se rechaza con 422 ANTES de tocar la boveda (jamas un 500 de base de datos).
    profile_name: str | None = Field(default=None, max_length=60)  # nombre INTERNO del perfil (M3); por defecto segun el tipo
    sol_username: str = Field(max_length=200)
    sol_password: str = Field(max_length=200)
    authorization_basis: str = Field(max_length=60)  # motivo BREVE; la explicacion larga va en `authorized_scope_description`
    authorization_expires_at: datetime | None = None
    authorization_reference: str | None = Field(default=None, max_length=255)
    authorized_scope_description: str | None = Field(default=None, max_length=2000)

    @field_validator("sol_username", "sol_password", "authorization_basis")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("no puede estar vacio")
        return value


class UpdateSolCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int
    profile_name: str | None = Field(default=None, max_length=60)
    sol_username: str | None = Field(default=None, max_length=200)
    sol_password: str | None = Field(default=None, max_length=200)
    authorization_basis: str | None = Field(default=None, max_length=60)
    authorization_expires_at: datetime | None = None
    authorization_reference: str | None = Field(default=None, max_length=255)
    authorized_scope_description: str | None = Field(default=None, max_length=2000)


class DisableSolCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int


# --- respuestas -- nunca contrasenas, ciphertext, salts, claves envueltas, handles ---


class SolVaultStatusOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    initialized: bool
    vault_id: UUID | None = None
    status: str  # NOT_INITIALIZED | ACTIVE | RECOVERY_REQUIRED | DISABLED (persistidos) -- ver ADR-0068
    unlocked: bool  # derivado de VaultUnlockRegistry, nunca persistido
    recovery_confirmed: bool
    version: int | None = None


class InitializeVaultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vault_id: UUID
    status: str
    recovery_code: str  # unica vez que se devuelve -- nunca se puede recuperar despues
    message: str = "Guarda el código de recuperación fuera de esta computadora"


class RecoveryReplaceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_recovery_code: str
    message: str = "Guarda el código de recuperación fuera de esta computadora"


class SimpleMessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str


class SlotOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    kind: str
    user_id: UUID | None
    status: str
    recovery_confirmed_at: datetime | None
    last_unlocked_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class SlotsListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[SlotOut]


class InvitationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    vault_id: UUID
    invited_by: UUID
    target_user_id: UUID
    status: str
    expires_at: datetime
    accepted_at: datetime | None
    version: int
    created_at: datetime


class RotateDataKeyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_data_key_version: int
    message: str = "Data key rotada correctamente"


class SolCredentialStatusOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    client_id: UUID
    credential_kind: str
    profile_name: str = ""
    status: str
    last_validation_result: str | None
    last_validated_at: datetime | None
    last_used_at: datetime | None
    authorization_basis: str
    authorized_at: datetime
    authorization_expires_at: datetime | None
    authorization_reference: str | None
    authorized_scope_description: str | None
    version: int
    created_at: datetime
    updated_at: datetime
