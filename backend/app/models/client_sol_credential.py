"""Credenciales SOL por cliente (Fase 5, Bloque 5.1, ver ADR-0068). En este
bloque toda alta/actualizacion aterriza siempre en `NEEDS_VALIDATION` --
el cambio a `ACTIVE` requiere una validacion real contra SUNAT
(acceso supervisado, Playwright, Buzon) que este bloque NO implementa
(ver ADR-0068, alcance excluido)."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SolCredentialKind(str, enum.Enum):
    SECONDARY = "SECONDARY"
    PRIMARY_OWN_RUC = "PRIMARY_OWN_RUC"
    PRIMARY_EXCEPTIONAL = "PRIMARY_EXCEPTIONAL"


class SolCredentialStatus(str, enum.Enum):
    """En 5.1 solo son alcanzables: alta/actualizacion -> NEEDS_VALIDATION;
    cualquier no-DISABLED -> DISABLED. Los demas valores existen en el
    catalogo porque describen el ciclo de vida completo del dato (que un
    bloque futuro de validacion real activara), pero ningun codigo de este
    bloque los asigna."""

    NEEDS_VALIDATION = "NEEDS_VALIDATION"
    ACTIVE = "ACTIVE"
    INVALID = "INVALID"
    LOCKED_BY_SUNAT = "LOCKED_BY_SUNAT"
    PASSWORD_EXPIRED = "PASSWORD_EXPIRED"
    DISABLED = "DISABLED"


_CREDENTIAL_KIND_VALUES = tuple(k.value for k in SolCredentialKind)
_CREDENTIAL_STATUS_VALUES = tuple(s.value for s in SolCredentialStatus)


class ClientSolCredential(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "client_sol_credentials"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        ForeignKeyConstraint(
            ["client_id", "tenant_id"], ["clients.id", "clients.tenant_id"],
            name="fk_client_sol_credentials_client_tenant",
        ),
        ForeignKeyConstraint(
            ["vault_id", "tenant_id"], ["sol_vaults.id", "sol_vaults.tenant_id"],
            name="fk_client_sol_credentials_vault_tenant",
        ),
        # FK exacta pedida (Seccion 11): (tenant_id, vault_id,
        # data_key_version) -> sol_vault_data_keys(tenant_id, vault_id,
        # data_key_version) -- mismo orden de columnas que la UNIQUE de esa
        # tabla (fe de erratas A.1 del Spike C).
        ForeignKeyConstraint(
            ["tenant_id", "vault_id", "data_key_version"],
            ["sol_vault_data_keys.tenant_id", "sol_vault_data_keys.vault_id",
             "sol_vault_data_keys.data_key_version"],
            name="fk_client_sol_credentials_data_key_version",
        ),
        ForeignKeyConstraint(
            ["created_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_client_sol_credentials_created_by_tenant",
        ),
        ForeignKeyConstraint(
            ["updated_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_client_sol_credentials_updated_by_tenant",
        ),
        ForeignKeyConstraint(
            ["disabled_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_client_sol_credentials_disabled_by_tenant",
        ),
        UniqueConstraint(
            "id", "tenant_id", "client_id", name="uq_client_sol_credentials_id_tenant_client"
        ),
        CheckConstraint(
            f"credential_kind IN {_CREDENTIAL_KIND_VALUES}", name="ck_client_sol_credentials_kind"
        ),
        CheckConstraint(f"status IN {_CREDENTIAL_STATUS_VALUES}", name="ck_client_sol_credentials_status"),
        CheckConstraint("version >= 1", name="ck_client_sol_credentials_version_positive"),
        CheckConstraint(
            "(status = 'DISABLED' AND disabled_at IS NOT NULL AND disabled_by IS NOT NULL) "
            "OR (status <> 'DISABLED' AND disabled_at IS NULL AND disabled_by IS NULL)",
            name="ck_client_sol_credentials_disabled_coherence",
        ),
        CheckConstraint("length(btrim(profile_name)) > 0", name="ck_client_sol_credentials_profile_name_not_blank"),
        # M3 (migracion 0023): varios perfiles por cliente. Nombre de perfil unico entre los no-DISABLED y a lo sumo UNA credencial principal.
        Index(
            "ux_client_sol_credentials_active_profile", "tenant_id", "client_id", sa.text("lower(profile_name)"),
            unique=True, postgresql_where=sa.text("status <> 'DISABLED'"),
        ),
        Index(
            "ux_client_sol_credentials_one_primary", "tenant_id", "client_id",
            unique=True, postgresql_where=sa.text("status <> 'DISABLED' AND credential_kind <> 'SECONDARY'"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    vault_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    credential_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # nombre INTERNO del perfil (no es secreto). Por defecto segun el tipo (mismo criterio que la migracion 0023) si el codigo no lo informa.
    profile_name: Mapped[str] = mapped_column(
        String(60), nullable=False,
        default=lambda ctx: "Credencial secundaria" if ctx.get_current_parameters().get("credential_kind") == "SECONDARY" else "Credencial principal",
    )
    sol_username_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sol_password_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    crypto_format_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    data_key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SolCredentialStatus.NEEDS_VALIDATION.value
    )
    last_validation_result: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    authorization_basis: Mapped[str] = mapped_column(String(60), nullable=False)
    authorized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    authorization_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    authorization_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    authorized_scope_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    authorization_revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_scope_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disabled_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
