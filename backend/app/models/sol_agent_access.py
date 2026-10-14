"""Fase 5, Bloque 5.2: acceso SOL supervisado (ver docs/phase5/block5_2/,
Domain A/A.1/A.2/A.3 -- freeze committed en 8749bce). 4 tablas nuevas:
sol_agents, sol_access_sessions, sol_agent_jobs, sol_agent_pairings.
Foundation de persistencia unicamente -- este modulo no implementa
servicios, transiciones de estado, ni el runtime de pairing/broker/agente
(ver BLOCK5_2_IMPLEMENTATION_PLAN.md seccion 4, Domain C en adelante)."""

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
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SolAccessScope(str, enum.Enum):
    AUTHENTICATE_TO_LANDING_ONLY = "AUTHENTICATE_TO_LANDING_ONLY"
    # M2: navegacion a un destino validado (enum cerrado `SolAccessDestination`) y traspaso al humano.
    NAVIGATE_TO_DESTINATION = "NAVIGATE_TO_DESTINATION"


class SolAccessDestination(str, enum.Enum):
    """Catalogo de destinos CONOCIDOS (M3). Que un destino exista aqui NO lo habilita: la habilitacion la decide
    `app.core.sunat_destinations` (solo LIVE_VALIDATED). Debe coincidir con el CHECK de la BD (migracion 0021)."""

    MENU_SOL = "MENU_SOL"
    FICHA_RUC = "FICHA_RUC"
    SIRE = "SIRE"
    SIRE_RVIE = "SIRE_RVIE"
    SIRE_RCE = "SIRE_RCE"
    SIRE_GENERACION_REGISTROS = "SIRE_GENERACION_REGISTROS"
    DECLARACIONES_Y_PAGOS = "DECLARACIONES_Y_PAGOS"
    DECLARA_FACIL = "DECLARA_FACIL"
    TRAMITES_Y_CONSULTAS = "TRAMITES_Y_CONSULTAS"


class SolAccessSessionStatus(str, enum.Enum):
    CREATED = "CREATED"
    WAITING_FOR_AGENT = "WAITING_FOR_AGENT"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    IN_PROGRESS = "IN_PROGRESS"
    # M2: destino validado, la automatizacion TERMINO y el navegador sigue abierto bajo control del humano.
    # NO es terminal: la sesion termina (SUCCEEDED/CANCELLED/EXPIRED) cuando el control humano acaba.
    HUMAN_CONTROL = "HUMAN_CONTROL"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class SolAccessOperationResult(str, enum.Enum):
    LANDING_REACHED = "LANDING_REACHED"
    DESTINATION_REACHED = "DESTINATION_REACHED"


class SolAccessTerminalReasonCode(str, enum.Enum):
    """Ver BLOCK5_2_STATE_MACHINE.md seccion 10 -- 20 valores, ninguno
    referencia PII."""

    AGENT_UNAVAILABLE = "AGENT_UNAVAILABLE"
    PAIRING_REQUIRED = "PAIRING_REQUIRED"
    AGENT_REVOKED = "AGENT_REVOKED"
    JOB_EXPIRED = "JOB_EXPIRED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    JOB_REPLAY_DETECTED = "JOB_REPLAY_DETECTED"
    CREDENTIAL_UNAVAILABLE = "CREDENTIAL_UNAVAILABLE"
    VAULT_LOCKED = "VAULT_LOCKED"
    VAULT_RECOVERY_REQUIRED = "VAULT_RECOVERY_REQUIRED"
    AUTH_HOST_CHANGED = "AUTH_HOST_CHANGED"
    WRONG_LOGIN_MODE = "WRONG_LOGIN_MODE"
    AUTH_FAILED = "AUTH_FAILED"
    HUMAN_CHALLENGE_TIMEOUT = "HUMAN_CHALLENGE_TIMEOUT"
    WRONG_TAXPAYER_CONTEXT = "WRONG_TAXPAYER_CONTEXT"
    PORTAL_CHANGED = "PORTAL_CHANGED"
    BROWSER_CLOSED = "BROWSER_CLOSED"
    CANCELLED_BY_USER = "CANCELLED_BY_USER"
    CLEANUP_FAILED = "CLEANUP_FAILED"
    BROKER_DISCONNECTED = "BROKER_DISCONNECTED"
    AGENT_CRASHED = "AGENT_CRASHED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class SolAgentStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class SolAgentJobStatus(str, enum.Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class SolAgentPairingStatus(str, enum.Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    LOCKED = "LOCKED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


_SESSION_STATUS_VALUES = tuple(s.value for s in SolAccessSessionStatus)
_SCOPE_VALUES = tuple(s.value for s in SolAccessScope)
_DESTINATION_VALUES = tuple(d.value for d in SolAccessDestination)
_OPERATION_RESULT_VALUES = tuple(r.value for r in SolAccessOperationResult)
_TERMINAL_REASON_CODE_VALUES = tuple(c.value for c in SolAccessTerminalReasonCode)
_AGENT_STATUS_VALUES = tuple(s.value for s in SolAgentStatus)
_JOB_STATUS_VALUES = tuple(s.value for s in SolAgentJobStatus)
_PAIRING_STATUS_VALUES = tuple(s.value for s in SolAgentPairingStatus)

# Estados terminales de sol_access_sessions.status -- ver
# BLOCK5_2_STATE_MACHINE.md seccion 1. Usado por el indice unico parcial
# "maximo 1 sesion no-terminal por credencial" (seccion 6 del modelo de
# datos, Domain A.1 seccion 33).
SESSION_TERMINAL_STATUSES = (
    SolAccessSessionStatus.SUCCEEDED.value,
    SolAccessSessionStatus.PARTIAL_FAILURE.value,
    SolAccessSessionStatus.FAILED.value,
    SolAccessSessionStatus.CANCELLED.value,
    SolAccessSessionStatus.EXPIRED.value,
)

# Estados activos de sol_agent_jobs.status -- ver BLOCK5_2_DATA_MODEL_DRAFT.md
# seccion 3. Usado por los dos indices unicos parciales "maximo 1 job
# activo por sesion/agente" (Domain A.1 secciones 21, 34).
JOB_ACTIVE_STATUSES = (SolAgentJobStatus.PENDING.value, SolAgentJobStatus.CLAIMED.value)


class SolAgent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Agente Windows emparejado (ver BLOCK5_2_DATA_MODEL_DRAFT.md seccion 2).
    `certificate_fingerprint` es identidad criptografica -- UNIQUE global,
    nunca solo por tenant. `ONLINE`/`OFFLINE` nunca se persiste (se deriva
    de `last_seen_at` + conexion mTLS viva, fuera de alcance de este
    foundation)."""

    __tablename__ = "sol_agents"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        UniqueConstraint("id", "tenant_id", name="uq_sol_agents_id_tenant"),
        UniqueConstraint(
            "certificate_fingerprint", name="uq_sol_agents_certificate_fingerprint"
        ),
        CheckConstraint(f"status IN {_AGENT_STATUS_VALUES}", name="ck_sol_agents_status"),
        CheckConstraint("version >= 1", name="ck_sol_agents_version_positive"),
        CheckConstraint(
            "(status <> 'REVOKED' AND revoked_at IS NULL) "
            "OR (status = 'REVOKED' AND revoked_at IS NOT NULL)",
            name="ck_sol_agents_revoked_coherence",
        ),
        Index("ix_sol_agents_tenant_status", "tenant_id", "status"),
        Index("ix_sol_agents_last_seen_at", "last_seen_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    certificate_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default=SolAgentStatus.ACTIVE.value
    )
    paired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class SolAccessSession(Base, UUIDPrimaryKeyMixin):
    """Sesion SOL supervisada (ver BLOCK5_2_DATA_MODEL_DRAFT.md seccion 1,
    BLOCK5_2_STATE_MACHINE.md). 20 columnas -- ver Domain A.2 seccion 11
    del freeze. `credential_id` usa una FK compuesta de 3 columnas contra
    la UNIQUE ya productiva de Bloque 5.1
    (`uq_client_sol_credentials_id_tenant_client`, orden exacto
    id/tenant_id/client_id) -- garantiza a nivel de BD que la credencial
    referenciada pertenece al mismo tenant Y al mismo cliente que la
    sesion (Domain A.2, Gate 1)."""

    __tablename__ = "sol_access_sessions"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        ForeignKeyConstraint(
            ["client_id", "tenant_id"],
            ["clients.id", "clients.tenant_id"],
            name="fk_sol_access_sessions_client_tenant",
        ),
        ForeignKeyConstraint(
            ["credential_id", "tenant_id", "client_id"],
            [
                "client_sol_credentials.id",
                "client_sol_credentials.tenant_id",
                "client_sol_credentials.client_id",
            ],
            name="fk_sol_access_sessions_credential_client_tenant",
        ),
        ForeignKeyConstraint(
            ["requested_by_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_sol_access_sessions_requester_tenant",
        ),
        ForeignKeyConstraint(
            ["assigned_agent_id", "tenant_id"],
            ["sol_agents.id", "sol_agents.tenant_id"],
            name="fk_sol_access_sessions_agent_tenant",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_sol_access_sessions_id_tenant"),
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_sol_access_sessions_idempotency_key"
        ),
        # `scope`/`operation_result` tienen un unico valor valido hoy --
        # `f"IN {(x,)}"` produciria una coma final invalida en SQL
        # (`IN ('X',)`), asi que se comparan por igualdad explicita en vez
        # de reutilizar el idioma `IN {tuple}` de los demas enums (que sí
        # tienen 2+ valores).
        CheckConstraint(f"scope IN {_SCOPE_VALUES}", name="ck_sol_access_sessions_scope"),
        CheckConstraint(
            f"destination IS NULL OR destination IN {_DESTINATION_VALUES}",
            name="ck_sol_access_sessions_destination",
        ),
        CheckConstraint(
            "(scope = 'NAVIGATE_TO_DESTINATION') = (destination IS NOT NULL)",
            name="ck_sol_access_sessions_scope_destination_coherence",
        ),
        CheckConstraint(
            "NOT extract_ficha OR destination = 'FICHA_RUC'", name="ck_sol_access_sessions_extract_ficha_destination"
        ),
        CheckConstraint(
            f"status IN {_SESSION_STATUS_VALUES}", name="ck_sol_access_sessions_status"
        ),
        CheckConstraint(
            f"operation_result IS NULL OR operation_result IN {_OPERATION_RESULT_VALUES}",
            name="ck_sol_access_sessions_operation_result",
        ),
        CheckConstraint(
            f"terminal_reason_code IS NULL OR terminal_reason_code IN {_TERMINAL_REASON_CODE_VALUES}",
            name="ck_sol_access_sessions_terminal_reason_code",
        ),
        CheckConstraint("version >= 1", name="ck_sol_access_sessions_version_positive"),
        Index("ix_sol_access_sessions_tenant_status", "tenant_id", "status"),
        Index("ix_sol_access_sessions_expires_at", "expires_at"),
        # Maximo 1 sesion no-terminal por (tenant_id, client_id,
        # credential_id) -- Domain A.1 seccion 33.
        Index(
            "ux_sol_access_sessions_active_per_credential",
            "tenant_id",
            "client_id",
            "credential_id",
            unique=True,
            postgresql_where=sa.text(
                "status NOT IN " + str(SESSION_TERMINAL_STATUSES)
            ),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    scope: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SolAccessSessionStatus.CREATED.value
    )
    operation_result: Mapped[str | None] = mapped_column(String(30), nullable=True)
    terminal_reason_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    agent_assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    waiting_for_user_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # M2
    destination: Mapped[str | None] = mapped_column(String(30), nullable=True)
    extract_ficha: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False, server_default=sa.false())
    human_control_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # M3: autorizacion de aceptacion real consumida por esta sesion (destino aun no habilitado globalmente); NULL en el flujo normal
    acceptance_grant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sol_destination_acceptances.id", name="fk_sol_access_sessions_acceptance_grant"), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False
    )


class SolAgentJob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Job dirigido a un agente especifico (ver BLOCK5_2_DATA_MODEL_DRAFT.md
    seccion 3). 14 columnas -- el resumen de la seccion 11 del documento
    decia 12 por un error de conteo; la tabla detallada (fuente de verdad)
    siempre tuvo 14, corregido aqui durante la implementacion de Domain B
    (ver informe). `agent_id` es NOT NULL (Domain A.1 seccion 34): el job
    siempre nace dirigido al agente ya asignado a la sesion, nunca una cola
    competida. El indice `ux_sol_agent_jobs_one_delivery_per_session`
    (Domain A.2, Gate 3/4) es la barrera DB-enforced contra que dos jobs
    distintos de la misma sesion entreguen la credencial bajo
    concurrencia."""

    __tablename__ = "sol_agent_jobs"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["sol_access_sessions.id", "sol_access_sessions.tenant_id"],
            name="fk_sol_agent_jobs_session_tenant",
        ),
        ForeignKeyConstraint(
            ["agent_id", "tenant_id"],
            ["sol_agents.id", "sol_agents.tenant_id"],
            name="fk_sol_agent_jobs_agent_tenant",
        ),
        UniqueConstraint("job_token_hash", name="uq_sol_agent_jobs_job_token_hash"),
        CheckConstraint(f"status IN {_JOB_STATUS_VALUES}", name="ck_sol_agent_jobs_status"),
        CheckConstraint("version >= 1", name="ck_sol_agent_jobs_version_positive"),
        Index("ix_sol_agent_jobs_session_status", "session_id", "status"),
        Index("ix_sol_agent_jobs_agent_status", "agent_id", "status"),
        Index("ix_sol_agent_jobs_expires_at", "expires_at"),
        # Maximo 1 job activo por sesion (Domain A.1 seccion 21).
        Index(
            "ux_sol_agent_jobs_active_per_session",
            "session_id",
            unique=True,
            postgresql_where=sa.text("status IN " + str(JOB_ACTIVE_STATUSES)),
        ),
        # Maximo 1 job activo por agente (Domain A.1 seccion 34).
        Index(
            "ux_sol_agent_jobs_active_per_agent",
            "agent_id",
            unique=True,
            postgresql_where=sa.text("status IN " + str(JOB_ACTIVE_STATUSES)),
        ),
        # Maximo 1 entrega de credencial por sesion, bajo concurrencia real
        # (Domain A.2, Gate 3/4) -- ver BLOCK5_2_DATA_MODEL_DRAFT.md seccion
        # 3, "No-redelivery por sesion".
        Index(
            "ux_sol_agent_jobs_one_delivery_per_session",
            "session_id",
            unique=True,
            postgresql_where=sa.text("credential_delivered_at IS NOT NULL"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default=SolAgentJobStatus.PENDING.value
    )
    job_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class SolAgentPairing(Base, UUIDPrimaryKeyMixin):
    """Intento de pairing de un agente Windows (ver
    BLOCK5_2_DATA_MODEL_DRAFT.md seccion 4). 14 columnas. `pairing_code_verifier`
    almacena unicamente el output opaco de un futuro verificador
    HMAC-SHA-256 con clave servidor-side (`PAIRING_VERIFIER_KEY`, fuera de
    PostgreSQL -- ver Domain A.3) -- este modulo NO implementa el flujo de
    pairing, solo la columna. Nunca el codigo de 6 digitos en claro."""

    __tablename__ = "sol_agent_pairings"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        ForeignKeyConstraint(
            ["requested_by_user_id", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_sol_agent_pairings_requester_tenant",
        ),
        ForeignKeyConstraint(
            ["confirmed_agent_id", "tenant_id"],
            ["sol_agents.id", "sol_agents.tenant_id"],
            name="fk_sol_agent_pairings_confirmed_agent_tenant",
        ),
        CheckConstraint(
            f"status IN {_PAIRING_STATUS_VALUES}", name="ck_sol_agent_pairings_status"
        ),
        CheckConstraint("version >= 1", name="ck_sol_agent_pairings_version_positive"),
        CheckConstraint(
            "expires_at > created_at", name="ck_sol_agent_pairings_expires_after_created"
        ),
        CheckConstraint(
            "failed_attempt_count >= 0",
            name="ck_sol_agent_pairings_failed_attempt_count_non_negative",
        ),
        CheckConstraint(
            "(status = 'PENDING' AND pairing_code_verifier IS NOT NULL) "
            "OR (status <> 'PENDING' AND pairing_code_verifier IS NULL)",
            name="ck_sol_agent_pairings_verifier_coherence",
        ),
        Index("ix_sol_agent_pairings_tenant_status", "tenant_id", "status"),
        Index("ix_sol_agent_pairings_expires_at", "expires_at"),
        # Como mucho un pairing PENDING simultaneo por usuario dentro de un
        # tenant -- evita ambiguedad de "a cual pairing pendiente confirma
        # este codigo" (BLOCK5_2_DATA_MODEL_DRAFT.md seccion 4).
        Index(
            "ux_sol_agent_pairings_pending_per_requester",
            "tenant_id",
            "requested_by_user_id",
            unique=True,
            postgresql_where=sa.text("status = 'PENDING'"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_display_name_candidate: Mapped[str] = mapped_column(String(255), nullable=False)
    pairing_code_verifier: Mapped[str | None] = mapped_column(String(64), nullable=True)
    csr_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default=SolAgentPairingStatus.PENDING.value
    )
    failed_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
