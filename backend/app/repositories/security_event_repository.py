import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.security_event import SecurityEvent


def create(
    db: Session,
    *,
    tenant_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    event_type: str,
    severity: str,
    identifier: str | None,
    metadata: dict | None,
    ip_address: str | None,
    user_agent: str | None,
    created_at: datetime,
) -> SecurityEvent:
    event = SecurityEvent(
        tenant_id=tenant_id,
        user_id=user_id,
        event_type=event_type,
        severity=severity,
        identifier=identifier,
        event_metadata=metadata,
        ip_address=ip_address,
        user_agent=user_agent,
        created_at=created_at,
    )
    db.add(event)
    db.flush()
    return event
