import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class SecurityEventType(str, enum.Enum):
    LOGIN_FAILURE_ATTEMPT = "LOGIN_FAILURE_ATTEMPT"
    LOGIN_ATTEMPT_WHILE_LOCKED = "LOGIN_ATTEMPT_WHILE_LOCKED"
    LOGIN_LOCKOUT_TRIGGERED = "LOGIN_LOCKOUT_TRIGGERED"
    LOGIN_UNLOCKED_MANUALLY = "LOGIN_UNLOCKED_MANUALLY"
    SESSION_EXPIRED_INACTIVITY = "SESSION_EXPIRED_INACTIVITY"
    SESSION_EXPIRED_ABSOLUTE = "SESSION_EXPIRED_ABSOLUTE"
    SESSION_REVOKED_CONCURRENT_LIMIT = "SESSION_REVOKED_CONCURRENT_LIMIT"
    SESSION_REVOKED_PASSWORD_CHANGE = "SESSION_REVOKED_PASSWORD_CHANGE"
    CSRF_REJECTED = "CSRF_REJECTED"
    CONCURRENT_APPROVAL_CONFLICT = "CONCURRENT_APPROVAL_CONFLICT"


class SecurityEventSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class SecurityEvent(Base, UUIDPrimaryKeyMixin):
    """Sucesos de seguridad, incluso sin usuario/tenant identificado (ver
    ADR-0009). Distinto de audit_logs: audit_logs solo registra actuaciones
    dentro de una sesion autenticada; security_events cubre tambien sucesos
    previos a la autenticacion (bloqueos, intentos fallidos, CSRF, etc.)."""

    __tablename__ = "security_events"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    identifier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
