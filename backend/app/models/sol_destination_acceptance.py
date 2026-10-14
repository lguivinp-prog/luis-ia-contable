"""M3 -- autorizacion de ACEPTACION REAL CONTROLADA de un destino SOL que aun no esta habilitado globalmente.

Una fila autoriza UNA sola prueba real de UN destino `RECON_COMPLETE` para UN cliente con UN perfil de credencial, concedida por el propietario
(administrador) y utilizable solo por el mismo usuario, dentro de un plazo corto. Se consume de forma atomica al crear la sesion. NO habilita el destino
para nadie mas ni de forma global; el resultado (PASS/FAIL) lo registra el propietario tras verificar la pantalla oficial. Nunca guarda secretos."""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin

ACCEPTANCE_STATUSES = ("AUTHORIZED", "CONSUMED", "REVOKED", "EXPIRED")
ACCEPTANCE_RESULTS = ("PASS", "FAIL")


class SolDestinationAcceptance(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "sol_destination_acceptances"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        ForeignKeyConstraint(["client_id", "tenant_id"], ["clients.id", "clients.tenant_id"], name="fk_sol_dest_acceptance_client_tenant"),
        ForeignKeyConstraint(
            ["credential_id", "tenant_id", "client_id"],
            ["client_sol_credentials.id", "client_sol_credentials.tenant_id", "client_sol_credentials.client_id"],
            name="fk_sol_dest_acceptance_credential_client_tenant",
        ),
        ForeignKeyConstraint(["authorized_by_user_id", "tenant_id"], ["users.id", "users.tenant_id"], name="fk_sol_dest_acceptance_user_tenant"),
        CheckConstraint(f"status IN {ACCEPTANCE_STATUSES}", name="ck_sol_dest_acceptance_status"),
        CheckConstraint(f"result IS NULL OR result IN {ACCEPTANCE_RESULTS}", name="ck_sol_dest_acceptance_result"),
        CheckConstraint("result IS NULL OR status = 'CONSUMED'", name="ck_sol_dest_acceptance_result_after_consumption"),
        CheckConstraint("(status = 'CONSUMED') = (consumed_at IS NOT NULL AND session_id IS NOT NULL)", name="ck_sol_dest_acceptance_consumption_coherence"),
        CheckConstraint("version >= 1", name="ck_sol_dest_acceptance_version_positive"),
        # a lo sumo UNA autorizacion vigente por (tenant, cliente, perfil, destino): concesiones concurrentes no se duplican
        Index(
            "ux_sol_dest_acceptance_one_authorized", "tenant_id", "client_id", "credential_id", "destination",
            unique=True, postgresql_where=sa.text("status = 'AUTHORIZED'"),
        ),
        Index("ix_sol_dest_acceptance_client", "tenant_id", "client_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    destination: Mapped[str] = mapped_column(String(30), nullable=False)
    authorized_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="AUTHORIZED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    result: Mapped[str | None] = mapped_column(String(4), nullable=True)
    result_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_note: Mapped[str | None] = mapped_column(Text, nullable=True)  # texto libre corto del propietario; nunca secretos ni datos del contribuyente
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
