"""M2: snapshots ESTRUCTURADOS de la Ficha RUC (propuesta + revision humana) y alta asistida de clientes.

Se persiste SOLO el resultado estructurado y validado (esquema versionado), nunca HTML crudo, DOM, cookies, capturas ni
volcados de red. La Ficha oficial NUNCA sobrescribe el cliente sin revision humana: el snapshot nace
`PENDING_REVIEW` con el DIFF contra el cliente actual; `ACCEPTED` aplica solo los campos aprobados."""

import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class FichaPurpose(str, enum.Enum):
    ONBOARDING = "ONBOARDING"
    REFRESH = "REFRESH"


class FichaReviewStatus(str, enum.Enum):
    PENDING_REVIEW = "PENDING_REVIEW"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class OnboardingStatus(str, enum.Enum):
    IN_PROGRESS = "IN_PROGRESS"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


FICHA_SOURCE = "SUNAT_FICHA_RUC"
ONBOARDING_ACTIVE_STATUSES = (OnboardingStatus.IN_PROGRESS.value, OnboardingStatus.AWAITING_REVIEW.value)


def _in(enum_cls) -> str:
    return "(" + ", ".join(f"'{m.value}'" for m in enum_cls) + ")"


class ClientFichaRucSnapshot(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "client_ficha_ruc_snapshots"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_client_ficha_ruc_snapshots_id_tenant"),
        UniqueConstraint("session_id", name="uq_client_ficha_ruc_snapshots_session"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        ForeignKeyConstraint(
            ["client_id", "tenant_id"], ["clients.id", "clients.tenant_id"], name="fk_client_ficha_ruc_snapshots_client_tenant"
        ),
        ForeignKeyConstraint(
            ["session_id", "tenant_id"],
            ["sol_access_sessions.id", "sol_access_sessions.tenant_id"],
            name="fk_client_ficha_ruc_snapshots_session_tenant",
        ),
        ForeignKeyConstraint(
            ["reviewed_by", "tenant_id"], ["users.id", "users.tenant_id"], name="fk_client_ficha_ruc_snapshots_reviewer_tenant"
        ),
        CheckConstraint(f"source = '{FICHA_SOURCE}'", name="ck_client_ficha_ruc_snapshots_source"),
        CheckConstraint(f"purpose IN {_in(FichaPurpose)}", name="ck_client_ficha_ruc_snapshots_purpose"),
        CheckConstraint(f"review_status IN {_in(FichaReviewStatus)}", name="ck_client_ficha_ruc_snapshots_review_status"),
        CheckConstraint("schema_version >= 1", name="ck_client_ficha_ruc_snapshots_schema_version"),
        CheckConstraint("version >= 1", name="ck_client_ficha_ruc_snapshots_version_positive"),
        CheckConstraint(
            "(review_status IN ('ACCEPTED', 'REJECTED')) = (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_client_ficha_ruc_snapshots_review_coherence",
        ),
        Index("ix_client_ficha_ruc_snapshots_tenant_client", "tenant_id", "client_id", "retrieved_at"),
        # Como mucho UNA propuesta pendiente por cliente (la nueva reemplaza/supera a la anterior en el servicio).
        Index(
            "ux_client_ficha_ruc_snapshots_pending_per_client",
            "tenant_id",
            "client_id",
            unique=True,
            postgresql_where=sa.text("review_status = 'PENDING_REVIEW'"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    purpose: Mapped[str] = mapped_column(String(12), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default=FICHA_SOURCE)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fields: Mapped[dict] = mapped_column(JSONB, nullable=False)
    malformed: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    diff: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    review_status: Mapped[str] = mapped_column(String(16), nullable=False, default=FichaReviewStatus.PENDING_REVIEW.value)
    accepted_fields: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ClientOnboarding(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Alta asistida por SUNAT: marca al cliente PROVISIONAL (`service_status = prospecto`, valores marcador) y
    gobierna su ciclo. Un cliente provisional nunca aparece activo ni completamente configurado."""

    __tablename__ = "client_onboardings"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        ForeignKeyConstraint(
            ["client_id", "tenant_id"], ["clients.id", "clients.tenant_id"], name="fk_client_onboardings_client_tenant"
        ),
        ForeignKeyConstraint(
            ["created_by", "tenant_id"], ["users.id", "users.tenant_id"], name="fk_client_onboardings_creator_tenant"
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "tenant_id"],
            ["client_ficha_ruc_snapshots.id", "client_ficha_ruc_snapshots.tenant_id"],
            name="fk_client_onboardings_snapshot_tenant",
        ),
        CheckConstraint(f"status IN {_in(OnboardingStatus)}", name="ck_client_onboardings_status"),
        CheckConstraint("version >= 1", name="ck_client_onboardings_version_positive"),
        Index("ix_client_onboardings_tenant_client", "tenant_id", "client_id"),
        Index(
            "ux_client_onboardings_active_per_client",
            "tenant_id",
            "client_id",
            unique=True,
            postgresql_where=sa.text("status IN ('IN_PROGRESS', 'AWAITING_REVIEW')"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=OnboardingStatus.IN_PROGRESS.value)
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
