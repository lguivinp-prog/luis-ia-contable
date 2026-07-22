import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction
from app.repositories import audit_log_repository


def record(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    action: AuditAction,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    metadata: dict | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    audit_log_repository.create(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=action.value,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata=metadata,
        ip_address=ip_address,
        user_agent=user_agent,
        created_at=datetime.now(UTC),
    )
