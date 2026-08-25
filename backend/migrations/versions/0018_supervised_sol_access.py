"""Fase 5, Bloque 5.2: acceso SOL supervisado -- foundation de persistencia
(ver docs/phase5/block5_2/, Domain A/A.1/A.2/A.3, freeze committed en
8749bce). 4 tablas nuevas: sol_agents, sol_access_sessions, sol_agent_jobs,
sol_agent_pairings. Alcance inicial exclusivamente
`AUTHENTICATE_TO_LANDING_ONLY` -- no crea infraestructura de Buzon
(sol_access_audit_events, sol_inbox_items, etc.), no toca
client_sol_credentials (su UNIQUE de soporte ya existe desde 0017). Solo
DDL: sin servicios, sin endpoints, sin runtime de broker/agente/pairing.

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-25

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SESSION_STATUS_VALUES = (
    "CREATED", "WAITING_FOR_AGENT", "WAITING_FOR_USER", "IN_PROGRESS",
    "SUCCEEDED", "PARTIAL_FAILURE", "FAILED", "CANCELLED", "EXPIRED",
)
_SESSION_TERMINAL_STATUSES = (
    "SUCCEEDED", "PARTIAL_FAILURE", "FAILED", "CANCELLED", "EXPIRED",
)
_TERMINAL_REASON_CODE_VALUES = (
    "AGENT_UNAVAILABLE", "PAIRING_REQUIRED", "AGENT_REVOKED", "JOB_EXPIRED",
    "SESSION_EXPIRED", "JOB_REPLAY_DETECTED", "CREDENTIAL_UNAVAILABLE",
    "VAULT_LOCKED", "VAULT_RECOVERY_REQUIRED", "AUTH_HOST_CHANGED",
    "WRONG_LOGIN_MODE", "AUTH_FAILED", "HUMAN_CHALLENGE_TIMEOUT",
    "WRONG_TAXPAYER_CONTEXT", "PORTAL_CHANGED", "BROWSER_CLOSED",
    "CANCELLED_BY_USER", "CLEANUP_FAILED", "BROKER_DISCONNECTED",
    "AGENT_CRASHED", "INTERNAL_ERROR",
)
_AGENT_STATUS_VALUES = ("ACTIVE", "REVOKED")
_JOB_STATUS_VALUES = ("PENDING", "CLAIMED", "COMPLETED", "FAILED", "CANCELLED", "EXPIRED")
_JOB_ACTIVE_STATUSES = ("PENDING", "CLAIMED")
_PAIRING_STATUS_VALUES = ("PENDING", "COMPLETED", "LOCKED", "EXPIRED", "CANCELLED")

_AGENT_REVOKED_COHERENCE_CHECK = (
    "(status <> 'REVOKED' AND revoked_at IS NULL) "
    "OR (status = 'REVOKED' AND revoked_at IS NOT NULL)"
)
_PAIRING_VERIFIER_COHERENCE_CHECK = (
    "(status = 'PENDING' AND pairing_code_verifier IS NOT NULL) "
    "OR (status <> 'PENDING' AND pairing_code_verifier IS NULL)"
)


def upgrade() -> None:
    # --- sol_agents ---
    op.create_table(
        "sol_agents",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("certificate_fingerprint", sa.String(128), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="ACTIVE"),
        sa.Column("paired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("id", "tenant_id", name="uq_sol_agents_id_tenant"),
        sa.UniqueConstraint(
            "certificate_fingerprint", name="uq_sol_agents_certificate_fingerprint"
        ),
        sa.CheckConstraint(f"status IN {_AGENT_STATUS_VALUES}", name="ck_sol_agents_status"),
        sa.CheckConstraint("version >= 1", name="ck_sol_agents_version_positive"),
        sa.CheckConstraint(_AGENT_REVOKED_COHERENCE_CHECK, name="ck_sol_agents_revoked_coherence"),
    )
    op.create_index("ix_sol_agents_tenant_status", "sol_agents", ["tenant_id", "status"])
    op.create_index("ix_sol_agents_last_seen_at", "sol_agents", ["last_seen_at"])

    # --- sol_access_sessions ---
    op.create_table(
        "sol_access_sessions",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scope", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="CREATED"),
        sa.Column("operation_result", sa.String(30), nullable=True),
        sa.Column("terminal_reason_code", sa.String(40), nullable=True),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("agent_assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("waiting_for_user_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("id", "tenant_id", name="uq_sol_access_sessions_id_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_sol_access_sessions_idempotency_key"
        ),
        # `scope`/`operation_result` tienen un unico valor valido hoy -- se
        # comparan por igualdad explicita (un `IN` de un solo elemento
        # produciria una coma final invalida en SQL).
        sa.CheckConstraint(
            "scope = 'AUTHENTICATE_TO_LANDING_ONLY'", name="ck_sol_access_sessions_scope"
        ),
        sa.CheckConstraint(f"status IN {_SESSION_STATUS_VALUES}", name="ck_sol_access_sessions_status"),
        sa.CheckConstraint(
            "operation_result IS NULL OR operation_result = 'LANDING_REACHED'",
            name="ck_sol_access_sessions_operation_result",
        ),
        sa.CheckConstraint(
            f"terminal_reason_code IS NULL OR terminal_reason_code IN {_TERMINAL_REASON_CODE_VALUES}",
            name="ck_sol_access_sessions_terminal_reason_code",
        ),
        sa.CheckConstraint("version >= 1", name="ck_sol_access_sessions_version_positive"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(
            ["client_id", "tenant_id"], ["clients.id", "clients.tenant_id"],
            name="fk_sol_access_sessions_client_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["credential_id", "tenant_id", "client_id"],
            [
                "client_sol_credentials.id",
                "client_sol_credentials.tenant_id",
                "client_sol_credentials.client_id",
            ],
            name="fk_sol_access_sessions_credential_client_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_sol_access_sessions_requester_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_agent_id", "tenant_id"], ["sol_agents.id", "sol_agents.tenant_id"],
            name="fk_sol_access_sessions_agent_tenant",
        ),
    )
    op.create_index("ix_sol_access_sessions_tenant_status", "sol_access_sessions", ["tenant_id", "status"])
    op.create_index("ix_sol_access_sessions_expires_at", "sol_access_sessions", ["expires_at"])
    op.create_index(
        "ux_sol_access_sessions_active_per_credential", "sol_access_sessions",
        ["tenant_id", "client_id", "credential_id"], unique=True,
        postgresql_where=sa.text("status NOT IN " + str(_SESSION_TERMINAL_STATUSES)),
    )

    # --- sol_agent_jobs ---
    op.create_table(
        "sol_agent_jobs",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="PENDING"),
        sa.Column("job_token_hash", sa.String(64), nullable=False),
        sa.Column("credential_delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("job_token_hash", name="uq_sol_agent_jobs_job_token_hash"),
        sa.CheckConstraint(f"status IN {_JOB_STATUS_VALUES}", name="ck_sol_agent_jobs_status"),
        sa.CheckConstraint("version >= 1", name="ck_sol_agent_jobs_version_positive"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["sol_access_sessions.id", "sol_access_sessions.tenant_id"],
            name="fk_sol_agent_jobs_session_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id", "tenant_id"], ["sol_agents.id", "sol_agents.tenant_id"],
            name="fk_sol_agent_jobs_agent_tenant",
        ),
    )
    op.create_index("ix_sol_agent_jobs_session_status", "sol_agent_jobs", ["session_id", "status"])
    op.create_index("ix_sol_agent_jobs_agent_status", "sol_agent_jobs", ["agent_id", "status"])
    op.create_index("ix_sol_agent_jobs_expires_at", "sol_agent_jobs", ["expires_at"])
    op.create_index(
        "ux_sol_agent_jobs_active_per_session", "sol_agent_jobs", ["session_id"],
        unique=True, postgresql_where=sa.text("status IN " + str(_JOB_ACTIVE_STATUSES)),
    )
    op.create_index(
        "ux_sol_agent_jobs_active_per_agent", "sol_agent_jobs", ["agent_id"],
        unique=True, postgresql_where=sa.text("status IN " + str(_JOB_ACTIVE_STATUSES)),
    )
    op.create_index(
        "ux_sol_agent_jobs_one_delivery_per_session", "sol_agent_jobs", ["session_id"],
        unique=True, postgresql_where=sa.text("credential_delivered_at IS NOT NULL"),
    )

    # --- sol_agent_pairings ---
    op.create_table(
        "sol_agent_pairings",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_display_name_candidate", sa.String(255), nullable=False),
        sa.Column("pairing_code_verifier", sa.String(64), nullable=True),
        sa.Column("csr_fingerprint", sa.String(128), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="PENDING"),
        sa.Column("failed_attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.CheckConstraint(f"status IN {_PAIRING_STATUS_VALUES}", name="ck_sol_agent_pairings_status"),
        sa.CheckConstraint("version >= 1", name="ck_sol_agent_pairings_version_positive"),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_sol_agent_pairings_expires_after_created"
        ),
        sa.CheckConstraint(
            "failed_attempt_count >= 0",
            name="ck_sol_agent_pairings_failed_attempt_count_non_negative",
        ),
        sa.CheckConstraint(
            _PAIRING_VERIFIER_COHERENCE_CHECK, name="ck_sol_agent_pairings_verifier_coherence"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_sol_agent_pairings_requester_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_agent_id", "tenant_id"], ["sol_agents.id", "sol_agents.tenant_id"],
            name="fk_sol_agent_pairings_confirmed_agent_tenant",
        ),
    )
    op.create_index("ix_sol_agent_pairings_tenant_status", "sol_agent_pairings", ["tenant_id", "status"])
    op.create_index("ix_sol_agent_pairings_expires_at", "sol_agent_pairings", ["expires_at"])
    op.create_index(
        "ux_sol_agent_pairings_pending_per_requester", "sol_agent_pairings",
        ["tenant_id", "requested_by_user_id"], unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_index("ux_sol_agent_pairings_pending_per_requester", table_name="sol_agent_pairings")
    op.drop_index("ix_sol_agent_pairings_expires_at", table_name="sol_agent_pairings")
    op.drop_index("ix_sol_agent_pairings_tenant_status", table_name="sol_agent_pairings")
    op.drop_table("sol_agent_pairings")

    op.drop_index("ux_sol_agent_jobs_one_delivery_per_session", table_name="sol_agent_jobs")
    op.drop_index("ux_sol_agent_jobs_active_per_agent", table_name="sol_agent_jobs")
    op.drop_index("ux_sol_agent_jobs_active_per_session", table_name="sol_agent_jobs")
    op.drop_index("ix_sol_agent_jobs_expires_at", table_name="sol_agent_jobs")
    op.drop_index("ix_sol_agent_jobs_agent_status", table_name="sol_agent_jobs")
    op.drop_index("ix_sol_agent_jobs_session_status", table_name="sol_agent_jobs")
    op.drop_table("sol_agent_jobs")

    op.drop_index("ux_sol_access_sessions_active_per_credential", table_name="sol_access_sessions")
    op.drop_index("ix_sol_access_sessions_expires_at", table_name="sol_access_sessions")
    op.drop_index("ix_sol_access_sessions_tenant_status", table_name="sol_access_sessions")
    op.drop_table("sol_access_sessions")

    op.drop_index("ix_sol_agents_last_seen_at", table_name="sol_agents")
    op.drop_index("ix_sol_agents_tenant_status", table_name="sol_agents")
    op.drop_table("sol_agents")
