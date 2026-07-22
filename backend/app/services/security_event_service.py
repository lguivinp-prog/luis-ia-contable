import uuid

from sqlalchemy.orm import Session

from app.core import clock
from app.models.security_event import SecurityEventSeverity, SecurityEventType
from app.repositories import security_event_repository


def record(
    db: Session,
    *,
    event_type: SecurityEventType,
    severity: SecurityEventSeverity,
    tenant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    identifier: str | None = None,
    metadata: dict | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    security_event_repository.create(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        event_type=event_type.value,
        severity=severity.value,
        identifier=identifier,
        metadata=metadata,
        ip_address=ip_address,
        user_agent=user_agent,
        created_at=clock.utcnow(),
    )
