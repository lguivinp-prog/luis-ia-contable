"""Boveda de credenciales SOL (Fase 5, Bloque 5.1, ver ADR-0068 a
ADR-0071). Jerarquia de claves de dos capas: `vault_root_key` (envuelta
individualmente por cada slot en `sol_vault_key_slots`) -> `credential_dek`
(envuelta por `vault_root_key`, versionada en `sol_vault_data_keys`) ->
campos de `client_sol_credentials`. Validado experimentalmente en el
Spike C (rama `spike/phase5-sol-vault-crypto`, commit `2096a9b`)."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SolVaultStatus(str, enum.Enum):
    """Estados PERSISTIDOS unicamente -- `NOT_INITIALIZED` (ausencia de
    fila) y `LOCKED` (ausencia de un unlock vigente en
    `VaultUnlockRegistry`) son estados DERIVADOS, nunca almacenados aqui
    (ver ADR-0068)."""

    ACTIVE = "ACTIVE"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    DISABLED = "DISABLED"


class SolVaultKeySlotKind(str, enum.Enum):
    USER = "USER"
    RECOVERY = "RECOVERY"


class SolVaultKeySlotStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class SolVaultDataKeyStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class SolVaultInvitationStatus(str, enum.Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class SolVault(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "sol_vaults"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_sol_vaults_tenant"),
        UniqueConstraint("id", "tenant_id", name="uq_sol_vaults_id_tenant"),
        CheckConstraint("version >= 1", name="ck_sol_vaults_version_positive"),
        CheckConstraint(
            "status IN ('ACTIVE','RECOVERY_REQUIRED','DISABLED')", name="ck_sol_vaults_status"
        ),
        CheckConstraint(
            "(status IN ('ACTIVE','RECOVERY_REQUIRED') AND disabled_at IS NULL AND disabled_by IS NULL) "
            "OR (status = 'DISABLED' AND disabled_at IS NOT NULL AND disabled_by IS NOT NULL)",
            name="ck_sol_vaults_disabled_coherence",
        ),
        ForeignKeyConstraint(
            ["initialized_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_sol_vaults_initialized_by_tenant",
        ),
        ForeignKeyConstraint(
            ["disabled_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_sol_vaults_disabled_by_tenant",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=SolVaultStatus.ACTIVE.value)
    crypto_format_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # ADR-0069: recovery_confirmed_at vive UNICAMENTE en el slot RECOVERY
    # (SolVaultKeySlot.recovery_confirmed_at) -- nunca duplicado aqui. La
    # version ACTIVE de credential_dek tampoco se duplica aqui -- unica
    # fuente de verdad es la fila ACTIVE de sol_vault_data_keys.
    initialized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    initialized_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disabled_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class SolVaultKeySlot(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "sol_vault_key_slots"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        ForeignKeyConstraint(
            ["vault_id", "tenant_id"], ["sol_vaults.id", "sol_vaults.tenant_id"],
            name="fk_sol_vault_key_slots_vault_tenant",
        ),
        ForeignKeyConstraint(
            ["user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_sol_vault_key_slots_user_tenant",
        ),
        ForeignKeyConstraint(
            ["revoked_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_sol_vault_key_slots_revoked_by_tenant",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_sol_vault_key_slots_id_tenant"),
        CheckConstraint("kind IN ('USER','RECOVERY')", name="ck_sol_vault_key_slots_kind"),
        CheckConstraint("status IN ('ACTIVE','REVOKED')", name="ck_sol_vault_key_slots_status"),
        CheckConstraint(
            "(kind='USER' AND user_id IS NOT NULL) OR (kind='RECOVERY' AND user_id IS NULL)",
            name="ck_sol_vault_key_slots_kind_user_coherence",
        ),
        CheckConstraint(
            "(status='ACTIVE' AND revoked_at IS NULL AND revoked_by IS NULL) "
            "OR (status='REVOKED' AND revoked_at IS NOT NULL)",
            name="ck_sol_vault_key_slots_revocation_coherence",
        ),
        CheckConstraint("failed_attempts >= 0", name="ck_sol_vault_key_slots_failed_attempts_non_negative"),
        CheckConstraint(
            "kdf_time_cost > 0 AND kdf_memory_cost_kib > 0 AND kdf_parallelism > 0 AND kdf_hash_len > 0",
            name="ck_sol_vault_key_slots_kdf_params_positive",
        ),
        CheckConstraint(
            "kind = 'RECOVERY' OR recovery_confirmed_at IS NULL",
            name="ck_sol_vault_key_slots_recovery_confirmed_only_recovery",
        ),
        # Un unico slot USER ACTIVE por usuario y boveda.
        Index(
            "ux_sol_vault_key_slots_user_active", "tenant_id", "vault_id", "user_id",
            unique=True, postgresql_where=sa.text("kind='USER' AND status='ACTIVE'"),
        ),
        # Maximo un RECOVERY ACTIVE confirmado por boveda.
        Index(
            "ux_sol_vault_key_slots_recovery_confirmed_active", "tenant_id", "vault_id",
            unique=True,
            postgresql_where=sa.text(
                "kind='RECOVERY' AND status='ACTIVE' AND recovery_confirmed_at IS NOT NULL"
            ),
        ),
        # Maximo un RECOVERY ACTIVE pendiente por boveda.
        Index(
            "ux_sol_vault_key_slots_recovery_pending_active", "tenant_id", "vault_id",
            unique=True,
            postgresql_where=sa.text(
                "kind='RECOVERY' AND status='ACTIVE' AND recovery_confirmed_at IS NULL"
            ),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    vault_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    kdf_algorithm: Mapped[str] = mapped_column(String(20), nullable=False, default="argon2id")
    kdf_salt: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    kdf_time_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    kdf_memory_cost_kib: Mapped[int] = mapped_column(Integer, nullable=False)
    kdf_parallelism: Mapped[int] = mapped_column(Integer, nullable=False)
    kdf_hash_len: Mapped[int] = mapped_column(Integer, nullable=False, default=32)
    kdf_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    wrapped_vault_root_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default=SolVaultKeySlotStatus.ACTIVE.value
    )
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    backoff_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_unlocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovery_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class SolVaultDataKey(Base, UUIDPrimaryKeyMixin):
    """Sin `updated_at`: fila append-only, solo transiciona
    ACTIVE -> RETIRED una vez (`retired_at`)."""

    __tablename__ = "sol_vault_data_keys"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        ForeignKeyConstraint(
            ["vault_id", "tenant_id"], ["sol_vaults.id", "sol_vaults.tenant_id"],
            name="fk_sol_vault_data_keys_vault_tenant",
        ),
        ForeignKeyConstraint(
            ["created_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_sol_vault_data_keys_created_by_tenant",
        ),
        # Fe de erratas A.1 (Spike C): orden fijo (tenant_id, vault_id,
        # data_key_version) en toda referencia a esta clave compuesta.
        UniqueConstraint(
            "tenant_id", "vault_id", "data_key_version", name="uq_sol_vault_data_keys_tenant_vault_version"
        ),
        CheckConstraint("data_key_version >= 1", name="ck_sol_vault_data_keys_version_positive"),
        CheckConstraint("status IN ('ACTIVE','RETIRED')", name="ck_sol_vault_data_keys_status"),
        CheckConstraint(
            "(status='ACTIVE' AND retired_at IS NULL) OR (status='RETIRED' AND retired_at IS NOT NULL)",
            name="ck_sol_vault_data_keys_retired_coherence",
        ),
        Index(
            "ux_sol_vault_data_keys_active", "tenant_id", "vault_id",
            unique=True, postgresql_where=sa.text("status='ACTIVE'"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    vault_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    data_key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    wrapped_data_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    crypto_format_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default=SolVaultDataKeyStatus.ACTIVE.value
    )
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SolVaultInvitation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Nunca contiene contrasenas, claves, KEKs, salts ni handles del
    registro de desbloqueo -- solo metadatos de la invitacion."""

    __tablename__ = "sol_vault_invitations"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        ForeignKeyConstraint(
            ["vault_id", "tenant_id"], ["sol_vaults.id", "sol_vaults.tenant_id"],
            name="fk_sol_vault_invitations_vault_tenant",
        ),
        ForeignKeyConstraint(
            ["invited_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_sol_vault_invitations_invited_by_tenant",
        ),
        ForeignKeyConstraint(
            ["target_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_sol_vault_invitations_target_user_tenant",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_sol_vault_invitations_id_tenant"),
        CheckConstraint(
            "status IN ('PENDING','ACCEPTED','CANCELLED','EXPIRED')", name="ck_sol_vault_invitations_status"
        ),
        CheckConstraint("version >= 1", name="ck_sol_vault_invitations_version_positive"),
        CheckConstraint(
            "invited_by <> target_user_id", name="ck_sol_vault_invitations_inviter_not_target"
        ),
        CheckConstraint(
            "(status = 'PENDING' AND accepted_at IS NULL AND closed_at IS NULL) "
            "OR (status = 'ACCEPTED' AND accepted_at IS NOT NULL AND closed_at IS NULL) "
            "OR (status IN ('CANCELLED','EXPIRED') AND closed_at IS NOT NULL AND accepted_at IS NULL)",
            name="ck_sol_vault_invitations_status_coherence",
        ),
        Index(
            "ux_sol_vault_invitations_pending_per_target", "tenant_id", "vault_id", "target_user_id",
            unique=True, postgresql_where=sa.text("status='PENDING'"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    vault_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    invited_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default=SolVaultInvitationStatus.PENDING.value
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
