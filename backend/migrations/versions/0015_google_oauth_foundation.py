"""Fase 3, Bloque 3.1: fundamento de integraciones y conexion OAuth Google
de identidad. 2 tablas nuevas (google_oauth_connections,
google_oauth_authorization_attempts): 1 FK simple + 2 FK compuestas + 4
UNIQUE + 7 CHECK en la primera; 1 FK simple + 1 FK compuesta + 1 UNIQUE + 7
CHECK en la segunda (ver ADR-0062/ADR-0063).

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-30

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONNECTION_STATUS_VALUES = ("ACTIVE", "NEEDS_REAUTH", "DISCONNECTED")
_DISCONNECTION_ORIGIN_VALUES = ("MANUAL", "SYSTEM")
_REAUTH_REASON_VALUES = (
    "TOKEN_EXPIRED_NO_REFRESH",
    "TOKEN_REVOKED",
    "ENCRYPTION_KEY_CHANGED",
    "DECRYPTION_FAILED",
)
_CAPABILITY_VALUES = ("IDENTITY", "CALENDAR", "DRIVE")
_ATTEMPT_STATUS_VALUES = ("PENDING", "PROCESSING", "SUCCEEDED", "FAILED")
_ATTEMPT_ERROR_CODE_VALUES = (
    "AUTHORIZATION_DENIED",
    "STATE_EXPIRED",
    "REFRESH_TOKEN_REQUIRED",
    "GOOGLE_ACCOUNT_MISMATCH",
    "ACCOUNT_ALREADY_CONNECTED",
    "INVALID_EMAIL_NOT_VERIFIED",
    "TOKEN_EXCHANGE_FAILED",
    "USERINFO_FAILED",
    "UNRECOVERABLE_ERROR",
)

_CONNECTION_STATUS_COHERENCE_CHECK = """
(
  status = 'ACTIVE' AND access_token_encrypted IS NOT NULL AND refresh_token_encrypted IS NOT NULL
  AND access_token_expires_at IS NOT NULL AND token_key_id IS NOT NULL
  AND reauth_required_at IS NULL AND reauth_reason IS NULL
  AND disconnected_at IS NULL AND disconnected_by IS NULL AND disconnection_origin IS NULL
)
OR
(
  status = 'NEEDS_REAUTH' AND access_token_encrypted IS NULL AND refresh_token_encrypted IS NULL
  AND access_token_expires_at IS NULL AND token_key_id IS NULL
  AND reauth_required_at IS NOT NULL AND reauth_reason IS NOT NULL
  AND disconnected_at IS NULL AND disconnected_by IS NULL AND disconnection_origin IS NULL
)
OR
(
  status = 'DISCONNECTED' AND access_token_encrypted IS NULL AND refresh_token_encrypted IS NULL
  AND access_token_expires_at IS NULL AND token_key_id IS NULL
  AND reauth_required_at IS NULL AND reauth_reason IS NULL
  AND disconnected_at IS NOT NULL AND disconnection_origin IS NOT NULL
  AND (
    (disconnection_origin = 'MANUAL' AND disconnected_by IS NOT NULL)
    OR (disconnection_origin = 'SYSTEM' AND disconnected_by IS NULL)
  )
)
""".strip()

_ATTEMPT_STATUS_COHERENCE_CHECK = """
(
  status = 'PENDING' AND code_verifier_encrypted IS NOT NULL AND credential_key_id IS NOT NULL
  AND consumed_at IS NULL AND completed_at IS NULL AND error_code IS NULL
)
OR
(
  status = 'PROCESSING' AND code_verifier_encrypted IS NULL AND credential_key_id IS NULL
  AND consumed_at IS NOT NULL AND completed_at IS NULL AND error_code IS NULL
)
OR
(
  status = 'SUCCEEDED' AND code_verifier_encrypted IS NULL AND credential_key_id IS NULL
  AND consumed_at IS NOT NULL AND completed_at IS NOT NULL AND error_code IS NULL
)
OR
(
  status = 'FAILED' AND code_verifier_encrypted IS NULL AND credential_key_id IS NULL
  AND completed_at IS NOT NULL AND error_code IS NOT NULL
)
""".strip()


def upgrade() -> None:
    # --- google_oauth_connections ---
    op.create_table(
        "google_oauth_connections",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False, server_default="google"),
        sa.Column("google_account_email", sa.String(255), nullable=False),
        sa.Column("google_account_id", sa.String(255), nullable=False),
        sa.Column("scopes_granted", postgresql.JSONB, nullable=False),
        sa.Column("access_token_encrypted", postgresql.BYTEA, nullable=True),
        sa.Column("refresh_token_encrypted", postgresql.BYTEA, nullable=True),
        sa.Column("token_key_id", sa.String(50), nullable=True),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("first_connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reauth_required_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reauth_reason", sa.String(60), nullable=True),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disconnected_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("disconnection_origin", sa.String(10), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("id", "tenant_id", name="uq_google_oauth_connections_id_tenant"),
        sa.UniqueConstraint(
            "id", "tenant_id", "user_id", name="uq_google_oauth_connections_id_tenant_user"
        ),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_google_oauth_connections_tenant_user"),
        sa.UniqueConstraint(
            "tenant_id", "google_account_id", name="uq_google_oauth_connections_tenant_account"
        ),
        sa.CheckConstraint("provider IN ('google')", name="ck_google_oauth_connections_provider"),
        sa.CheckConstraint(
            f"status IN {_CONNECTION_STATUS_VALUES}", name="ck_google_oauth_connections_status"
        ),
        sa.CheckConstraint(
            f"disconnection_origin IS NULL OR disconnection_origin IN {_DISCONNECTION_ORIGIN_VALUES}",
            name="ck_google_oauth_connections_disconnection_origin",
        ),
        sa.CheckConstraint(
            f"reauth_reason IS NULL OR reauth_reason IN {_REAUTH_REASON_VALUES}",
            name="ck_google_oauth_connections_reauth_reason",
        ),
        sa.CheckConstraint("version >= 1", name="ck_google_oauth_connections_version_positive"),
        sa.CheckConstraint(
            "jsonb_typeof(scopes_granted) = 'array'", name="ck_google_oauth_connections_scopes_array"
        ),
        sa.CheckConstraint(
            _CONNECTION_STATUS_COHERENCE_CHECK, name="ck_google_oauth_connections_status_coherence"
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_google_oauth_connections_user_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["disconnected_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_google_oauth_connections_disconnected_by_tenant",
        ),
    )
    op.create_index(
        "ix_google_oauth_connections_tenant_status", "google_oauth_connections", ["tenant_id", "status"]
    )

    # --- google_oauth_authorization_attempts ---
    op.create_table(
        "google_oauth_authorization_attempts",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("code_verifier_encrypted", postgresql.BYTEA, nullable=True),
        sa.Column("credential_key_id", sa.String(50), nullable=True),
        sa.Column("requested_capability", sa.String(20), nullable=False),
        sa.Column("requested_scopes", postgresql.JSONB, nullable=False),
        sa.Column("return_path", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(60), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("state_hash", name="uq_google_oauth_authorization_attempts_state_hash"),
        sa.CheckConstraint(
            f"requested_capability IN {_CAPABILITY_VALUES}", name="ck_google_oauth_attempts_capability"
        ),
        sa.CheckConstraint(f"status IN {_ATTEMPT_STATUS_VALUES}", name="ck_google_oauth_attempts_status"),
        sa.CheckConstraint(
            "jsonb_typeof(requested_scopes) = 'array'", name="ck_google_oauth_attempts_scopes_array"
        ),
        sa.CheckConstraint(
            "state_hash ~ '^[0-9a-f]{64}$'", name="ck_google_oauth_attempts_state_hash_format"
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_google_oauth_attempts_expiry_after_creation"
        ),
        sa.CheckConstraint(
            f"error_code IS NULL OR error_code IN {_ATTEMPT_ERROR_CODE_VALUES}",
            name="ck_google_oauth_attempts_error_code",
        ),
        sa.CheckConstraint(
            _ATTEMPT_STATUS_COHERENCE_CHECK, name="ck_google_oauth_attempts_status_coherence"
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_google_oauth_authorization_attempts_user_tenant",
        ),
    )
    op.create_index(
        "ix_google_oauth_attempts_tenant_status",
        "google_oauth_authorization_attempts",
        ["tenant_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_google_oauth_attempts_tenant_status", table_name="google_oauth_authorization_attempts"
    )
    op.drop_table("google_oauth_authorization_attempts")

    op.drop_index("ix_google_oauth_connections_tenant_status", table_name="google_oauth_connections")
    op.drop_table("google_oauth_connections")
