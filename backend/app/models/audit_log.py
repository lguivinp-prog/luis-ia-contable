import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class AuditAction(str, enum.Enum):
    # Nota (Bloque 2 / ADR-0009): ya no existe LOGIN_FAILURE en audit_logs.
    # Los fallos de login (exista o no la cuenta) se registran exclusivamente
    # en security_events + login_attempts. audit_logs queda reservado para
    # actuaciones dentro de una sesion autenticada.
    LOGIN_SUCCESS = "LOGIN_SUCCESS"
    LOGOUT = "LOGOUT"
    PASSWORD_CHANGED = "PASSWORD_CHANGED"
    SESSION_REVOKED = "SESSION_REVOKED"
    SESSION_REVOKED_ALL_OTHERS = "SESSION_REVOKED_ALL_OTHERS"
    CLIENT_CREATE = "CLIENT_CREATE"
    CLIENT_UPDATE = "CLIENT_UPDATE"
    CLIENT_STATUS_CHANGE = "CLIENT_STATUS_CHANGE"
    CLIENT_OBLIGATION_ASSIGN = "CLIENT_OBLIGATION_ASSIGN"
    CLIENT_OBLIGATION_UPDATE = "CLIENT_OBLIGATION_UPDATE"
    CLIENT_OBLIGATION_REMOVE = "CLIENT_OBLIGATION_REMOVE"
    RUC_LOOKUP_QUERY = "RUC_LOOKUP_QUERY"
    TAX_CATEGORY_ASSIGN = "TAX_CATEGORY_ASSIGN"
    TAX_CATEGORY_REMOVE = "TAX_CATEGORY_REMOVE"
    TAX_SCHEDULE_SOURCE_CREATE = "TAX_SCHEDULE_SOURCE_CREATE"
    TAX_SCHEDULE_IMPORT_RUN_CREATE = "TAX_SCHEDULE_IMPORT_RUN_CREATE"
    TAX_SCHEDULE_IMPORT_CONFIRM = "TAX_SCHEDULE_IMPORT_CONFIRM"
    TAX_SCHEDULE_IMPORT_REJECT = "TAX_SCHEDULE_IMPORT_REJECT"
    TAX_SCHEDULE_SUBMIT_FOR_REVIEW = "TAX_SCHEDULE_SUBMIT_FOR_REVIEW"
    TAX_SCHEDULE_APPROVE = "TAX_SCHEDULE_APPROVE"
    TAX_SCHEDULE_REJECT = "TAX_SCHEDULE_REJECT"
    TAX_SCHEDULE_CANCEL = "TAX_SCHEDULE_CANCEL"
    TAX_SCHEDULE_PREVIEW_QUERY = "TAX_SCHEDULE_PREVIEW_QUERY"


class AuditLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "audit_logs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    log_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
