import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog


def create(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    action: str,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    metadata: dict | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    created_at: datetime,
) -> AuditLog:
    log = AuditLog(
        tenant_id=tenant_id,
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        log_metadata=metadata,
        ip_address=ip_address,
        user_agent=user_agent,
        created_at=created_at,
    )
    db.add(log)
    db.flush()
    return log
