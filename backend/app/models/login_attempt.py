import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class LoginAttempt(Base, UUIDPrimaryKeyMixin):
    """Registro persistente de intentos de login (ver ADR-0009).

    tenant_id es NULLABLE: solo se conoce con certeza en un intento exitoso.
    Nunca se borran filas; el calculo de bloqueo cuenta los fallos desde el
    ultimo exito.
    """

    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_identifier_attempted_at", "identifier", "attempted_at"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True
    )
    identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
