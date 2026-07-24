import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        # Auxiliar para FKs compuestas (id, tenant_id) desde tablas de
        # Bloque 3 (ver ADR-0025): declaraciones/historiales referencian un
        # usuario garantizando ademas que pertenece al tenant correcto.
        UniqueConstraint("id", "tenant_id", name="uq_users_id_tenant"),
        # RBAC real (Bloque 4, ADR-0050): conjunto fijo de roles, verificado
        # con un preflight antes de crear esta constraint (ver migracion 0012).
        CheckConstraint("role IN ('admin', 'contador', 'asistente')", name="ck_users_role"),
        # Ciclo de vida de desactivacion (ADR-0051): mismo patron de coherencia
        # que `professional_receipts`/`tax_payments` al anular -- el estado
        # vigente (is_active) y sus metadatos de desactivacion viajan juntos.
        CheckConstraint(
            "(is_active = true AND deactivated_at IS NULL AND deactivated_by IS NULL "
            "AND deactivation_reason IS NULL) OR "
            "(is_active = false AND deactivated_at IS NOT NULL AND deactivated_by IS NOT NULL "
            "AND deactivation_reason IS NOT NULL)",
            name="ck_users_deactivation_coherence",
        ),
        ForeignKeyConstraint(
            ["deactivated_by", "tenant_id"],
            ["users.id", "users.tenant_id"],
            name="fk_users_deactivated_by",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False, default="admin")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    deactivation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
