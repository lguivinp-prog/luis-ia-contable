import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.google_oauth import GoogleOAuthAuthorizationAttempt, GoogleOAuthConnection


def create_attempt(
    db: Session, attempt: GoogleOAuthAuthorizationAttempt
) -> GoogleOAuthAuthorizationAttempt:
    db.add(attempt)
    db.flush()
    return attempt


def lock_attempt_by_state_hash(
    db: Session, state_hash: str
) -> GoogleOAuthAuthorizationAttempt | None:
    """`SELECT ... FOR UPDATE` por `state_hash` (globalmente unico) -- ver
    flujo de callback de la Seccion 8 del plan v3. `populate_existing`
    obligatorio (mismo motivo que `operational_task_repository.
    lock_exact_task`)."""
    return db.execute(
        select(GoogleOAuthAuthorizationAttempt)
        .where(GoogleOAuthAuthorizationAttempt.state_hash == state_hash)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def lock_attempt_by_id(
    db: Session, tenant_id: uuid.UUID, attempt_id: uuid.UUID
) -> GoogleOAuthAuthorizationAttempt | None:
    return db.execute(
        select(GoogleOAuthAuthorizationAttempt)
        .where(
            GoogleOAuthAuthorizationAttempt.tenant_id == tenant_id,
            GoogleOAuthAuthorizationAttempt.id == attempt_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def get_connection(
    db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> GoogleOAuthConnection | None:
    return db.execute(
        select(GoogleOAuthConnection).where(
            GoogleOAuthConnection.tenant_id == tenant_id, GoogleOAuthConnection.user_id == user_id
        )
    ).scalar_one_or_none()


def lock_connection_for_update(
    db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> GoogleOAuthConnection | None:
    """Bloqueo de la fila exacta de conexion del usuario -- una conexion por
    usuario en este tenant (ver `uq_google_oauth_connections_tenant_user`)."""
    return db.execute(
        select(GoogleOAuthConnection)
        .where(GoogleOAuthConnection.tenant_id == tenant_id, GoogleOAuthConnection.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def create_connection(db: Session, connection: GoogleOAuthConnection) -> GoogleOAuthConnection:
    db.add(connection)
    db.flush()
    return connection
