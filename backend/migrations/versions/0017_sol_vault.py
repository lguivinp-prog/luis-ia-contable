"""Fase 5, Bloque 5.1: boveda de credenciales SOL (ver ADR-0068 a
ADR-0071). 5 tablas nuevas: sol_vaults, sol_vault_key_slots,
sol_vault_data_keys, sol_vault_invitations, client_sol_credentials.
Jerarquia de 2 capas (vault_root_key envuelta por slot -> credential_dek
envuelta por vault_root_key -> campos de credencial), validada
experimentalmente en el Spike C (rama `spike/phase5-sol-vault-crypto`,
commit `2096a9b`). No crea sol_access_sessions, sunat_mailbox_items,
sunat_mailbox_documents ni client_sol_credential_authorization_documents
-- fuera de alcance de este bloque.

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-31

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VAULT_STATUS_VALUES = ("ACTIVE", "RECOVERY_REQUIRED", "DISABLED")
_SLOT_KIND_VALUES = ("USER", "RECOVERY")
_SLOT_STATUS_VALUES = ("ACTIVE", "REVOKED")
_DATA_KEY_STATUS_VALUES = ("ACTIVE", "RETIRED")
_INVITATION_STATUS_VALUES = ("PENDING", "ACCEPTED", "CANCELLED", "EXPIRED")
_CREDENTIAL_KIND_VALUES = ("SECONDARY", "PRIMARY_OWN_RUC", "PRIMARY_EXCEPTIONAL")
_CREDENTIAL_STATUS_VALUES = (
    "NEEDS_VALIDATION", "ACTIVE", "INVALID", "LOCKED_BY_SUNAT", "PASSWORD_EXPIRED", "DISABLED",
)

_VAULT_DISABLED_COHERENCE_CHECK = (
    "(status IN ('ACTIVE','RECOVERY_REQUIRED') AND disabled_at IS NULL AND disabled_by IS NULL) "
    "OR (status = 'DISABLED' AND disabled_at IS NOT NULL AND disabled_by IS NOT NULL)"
)

_SLOT_KIND_USER_COHERENCE_CHECK = (
    "(kind='USER' AND user_id IS NOT NULL) OR (kind='RECOVERY' AND user_id IS NULL)"
)

_SLOT_REVOCATION_COHERENCE_CHECK = (
    "(status='ACTIVE' AND revoked_at IS NULL AND revoked_by IS NULL) "
    "OR (status='REVOKED' AND revoked_at IS NOT NULL)"
)

_DATA_KEY_RETIRED_COHERENCE_CHECK = (
    "(status='ACTIVE' AND retired_at IS NULL) OR (status='RETIRED' AND retired_at IS NOT NULL)"
)

_INVITATION_STATUS_COHERENCE_CHECK = (
    "(status = 'PENDING' AND accepted_at IS NULL AND closed_at IS NULL) "
    "OR (status = 'ACCEPTED' AND accepted_at IS NOT NULL AND closed_at IS NULL) "
    "OR (status IN ('CANCELLED','EXPIRED') AND closed_at IS NOT NULL AND accepted_at IS NULL)"
)

_CREDENTIAL_DISABLED_COHERENCE_CHECK = (
    "(status = 'DISABLED' AND disabled_at IS NOT NULL AND disabled_by IS NOT NULL) "
    "OR (status <> 'DISABLED' AND disabled_at IS NULL AND disabled_by IS NULL)"
)


def upgrade() -> None:
    # --- sol_vaults ---
    op.create_table(
        "sol_vaults",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("crypto_format_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("initialized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("initialized_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", name="uq_sol_vaults_tenant"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_sol_vaults_id_tenant"),
        sa.CheckConstraint("version >= 1", name="ck_sol_vaults_version_positive"),
        sa.CheckConstraint(f"status IN {_VAULT_STATUS_VALUES}", name="ck_sol_vaults_status"),
        sa.CheckConstraint(_VAULT_DISABLED_COHERENCE_CHECK, name="ck_sol_vaults_disabled_coherence"),
        sa.ForeignKeyConstraint(
            ["initialized_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_sol_vaults_initialized_by_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["disabled_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_sol_vaults_disabled_by_tenant",
        ),
    )

    # --- sol_vault_key_slots ---
    op.create_table(
        "sol_vault_key_slots",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vault_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("kdf_algorithm", sa.String(20), nullable=False, server_default="argon2id"),
        sa.Column("kdf_salt", sa.LargeBinary, nullable=False),
        sa.Column("kdf_time_cost", sa.Integer, nullable=False),
        sa.Column("kdf_memory_cost_kib", sa.Integer, nullable=False),
        sa.Column("kdf_parallelism", sa.Integer, nullable=False),
        sa.Column("kdf_hash_len", sa.Integer, nullable=False, server_default="32"),
        sa.Column("kdf_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("wrapped_vault_root_key", sa.LargeBinary, nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="ACTIVE"),
        sa.Column("failed_attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("backoff_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_unlocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("id", "tenant_id", name="uq_sol_vault_key_slots_id_tenant"),
        sa.CheckConstraint(f"kind IN {_SLOT_KIND_VALUES}", name="ck_sol_vault_key_slots_kind"),
        sa.CheckConstraint(f"status IN {_SLOT_STATUS_VALUES}", name="ck_sol_vault_key_slots_status"),
        sa.CheckConstraint(
            _SLOT_KIND_USER_COHERENCE_CHECK, name="ck_sol_vault_key_slots_kind_user_coherence"
        ),
        sa.CheckConstraint(
            _SLOT_REVOCATION_COHERENCE_CHECK, name="ck_sol_vault_key_slots_revocation_coherence"
        ),
        sa.CheckConstraint(
            "failed_attempts >= 0", name="ck_sol_vault_key_slots_failed_attempts_non_negative"
        ),
        sa.CheckConstraint(
            "kdf_time_cost > 0 AND kdf_memory_cost_kib > 0 AND kdf_parallelism > 0 AND kdf_hash_len > 0",
            name="ck_sol_vault_key_slots_kdf_params_positive",
        ),
        sa.CheckConstraint(
            "kind = 'RECOVERY' OR recovery_confirmed_at IS NULL",
            name="ck_sol_vault_key_slots_recovery_confirmed_only_recovery",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(
            ["vault_id", "tenant_id"], ["sol_vaults.id", "sol_vaults.tenant_id"],
            name="fk_sol_vault_key_slots_vault_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_sol_vault_key_slots_user_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_sol_vault_key_slots_revoked_by_tenant",
        ),
    )
    op.create_index(
        "ux_sol_vault_key_slots_user_active", "sol_vault_key_slots", ["tenant_id", "vault_id", "user_id"],
        unique=True, postgresql_where=sa.text("kind='USER' AND status='ACTIVE'"),
    )
    op.create_index(
        "ux_sol_vault_key_slots_recovery_confirmed_active", "sol_vault_key_slots", ["tenant_id", "vault_id"],
        unique=True,
        postgresql_where=sa.text("kind='RECOVERY' AND status='ACTIVE' AND recovery_confirmed_at IS NOT NULL"),
    )
    op.create_index(
        "ux_sol_vault_key_slots_recovery_pending_active", "sol_vault_key_slots", ["tenant_id", "vault_id"],
        unique=True,
        postgresql_where=sa.text("kind='RECOVERY' AND status='ACTIVE' AND recovery_confirmed_at IS NULL"),
    )
    op.create_index("ix_sol_vault_key_slots_tenant_vault", "sol_vault_key_slots", ["tenant_id", "vault_id"])

    # --- sol_vault_data_keys ---
    op.create_table(
        "sol_vault_data_keys",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vault_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("data_key_version", sa.Integer, nullable=False),
        sa.Column("wrapped_data_key", sa.LargeBinary, nullable=False),
        sa.Column("crypto_format_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.String(10), nullable=False, server_default="ACTIVE"),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "vault_id", "data_key_version", name="uq_sol_vault_data_keys_tenant_vault_version"
        ),
        sa.CheckConstraint("data_key_version >= 1", name="ck_sol_vault_data_keys_version_positive"),
        sa.CheckConstraint(f"status IN {_DATA_KEY_STATUS_VALUES}", name="ck_sol_vault_data_keys_status"),
        sa.CheckConstraint(
            _DATA_KEY_RETIRED_COHERENCE_CHECK, name="ck_sol_vault_data_keys_retired_coherence"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(
            ["vault_id", "tenant_id"], ["sol_vaults.id", "sol_vaults.tenant_id"],
            name="fk_sol_vault_data_keys_vault_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["created_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_sol_vault_data_keys_created_by_tenant",
        ),
    )
    op.create_index(
        "ux_sol_vault_data_keys_active", "sol_vault_data_keys", ["tenant_id", "vault_id"],
        unique=True, postgresql_where=sa.text("status='ACTIVE'"),
    )

    # --- sol_vault_invitations ---
    op.create_table(
        "sol_vault_invitations",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vault_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invited_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="PENDING"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("id", "tenant_id", name="uq_sol_vault_invitations_id_tenant"),
        sa.CheckConstraint(f"status IN {_INVITATION_STATUS_VALUES}", name="ck_sol_vault_invitations_status"),
        sa.CheckConstraint("version >= 1", name="ck_sol_vault_invitations_version_positive"),
        sa.CheckConstraint(
            "invited_by <> target_user_id", name="ck_sol_vault_invitations_inviter_not_target"
        ),
        sa.CheckConstraint(
            _INVITATION_STATUS_COHERENCE_CHECK, name="ck_sol_vault_invitations_status_coherence"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(
            ["vault_id", "tenant_id"], ["sol_vaults.id", "sol_vaults.tenant_id"],
            name="fk_sol_vault_invitations_vault_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_sol_vault_invitations_invited_by_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["target_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_sol_vault_invitations_target_user_tenant",
        ),
    )
    op.create_index(
        "ux_sol_vault_invitations_pending_per_target", "sol_vault_invitations",
        ["tenant_id", "vault_id", "target_user_id"], unique=True,
        postgresql_where=sa.text("status='PENDING'"),
    )

    # --- client_sol_credentials ---
    op.create_table(
        "client_sol_credentials",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vault_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_kind", sa.String(20), nullable=False),
        sa.Column("sol_username_encrypted", sa.LargeBinary, nullable=False),
        sa.Column("sol_password_encrypted", sa.LargeBinary, nullable=False),
        sa.Column("crypto_format_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("data_key_version", sa.Integer, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="NEEDS_VALIDATION"),
        sa.Column("last_validation_result", sa.String(20), nullable=True),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("authorization_basis", sa.String(60), nullable=False),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("authorization_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("authorization_reference", sa.String(255), nullable=True),
        sa.Column("authorized_scope_description", sa.Text, nullable=True),
        sa.Column("authorization_revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_scope_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "id", "tenant_id", "client_id", name="uq_client_sol_credentials_id_tenant_client"
        ),
        sa.CheckConstraint(
            f"credential_kind IN {_CREDENTIAL_KIND_VALUES}", name="ck_client_sol_credentials_kind"
        ),
        sa.CheckConstraint(f"status IN {_CREDENTIAL_STATUS_VALUES}", name="ck_client_sol_credentials_status"),
        sa.CheckConstraint("version >= 1", name="ck_client_sol_credentials_version_positive"),
        sa.CheckConstraint(
            _CREDENTIAL_DISABLED_COHERENCE_CHECK, name="ck_client_sol_credentials_disabled_coherence"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(
            ["client_id", "tenant_id"], ["clients.id", "clients.tenant_id"],
            name="fk_client_sol_credentials_client_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["vault_id", "tenant_id"], ["sol_vaults.id", "sol_vaults.tenant_id"],
            name="fk_client_sol_credentials_vault_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "vault_id", "data_key_version"],
            ["sol_vault_data_keys.tenant_id", "sol_vault_data_keys.vault_id",
             "sol_vault_data_keys.data_key_version"],
            name="fk_client_sol_credentials_data_key_version",
        ),
        sa.ForeignKeyConstraint(
            ["created_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_client_sol_credentials_created_by_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_client_sol_credentials_updated_by_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["disabled_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_client_sol_credentials_disabled_by_tenant",
        ),
    )
    op.create_index(
        "ux_client_sol_credentials_active_per_client", "client_sol_credentials", ["tenant_id", "client_id"],
        unique=True, postgresql_where=sa.text("status <> 'DISABLED'"),
    )
    op.create_index(
        "ix_client_sol_credentials_tenant_vault", "client_sol_credentials", ["tenant_id", "vault_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_client_sol_credentials_tenant_vault", table_name="client_sol_credentials")
    op.drop_index("ux_client_sol_credentials_active_per_client", table_name="client_sol_credentials")
    op.drop_table("client_sol_credentials")

    op.drop_index("ux_sol_vault_invitations_pending_per_target", table_name="sol_vault_invitations")
    op.drop_table("sol_vault_invitations")

    op.drop_index("ux_sol_vault_data_keys_active", table_name="sol_vault_data_keys")
    op.drop_table("sol_vault_data_keys")

    op.drop_index("ix_sol_vault_key_slots_tenant_vault", table_name="sol_vault_key_slots")
    op.drop_index("ux_sol_vault_key_slots_recovery_pending_active", table_name="sol_vault_key_slots")
    op.drop_index("ux_sol_vault_key_slots_recovery_confirmed_active", table_name="sol_vault_key_slots")
    op.drop_index("ux_sol_vault_key_slots_user_active", table_name="sol_vault_key_slots")
    op.drop_table("sol_vault_key_slots")

    op.drop_table("sol_vaults")
