import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# --- enums de dominio (Fase 3 / Bloque 3.1 -- ver ADR-0062/ADR-0063) ---


class GoogleOAuthConnectionStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    NEEDS_REAUTH = "NEEDS_REAUTH"
    DISCONNECTED = "DISCONNECTED"


class GoogleOAuthDisconnectionOrigin(str, enum.Enum):
    MANUAL = "MANUAL"
    SYSTEM = "SYSTEM"


# Motivos de reauth_reason: INSUFFICIENT_SCOPE queda deliberadamente fuera --
# una capacidad no autorizada nunca mueve la conexion a NEEDS_REAUTH, la
# conexion permanece ACTIVE (ver correccion de implementacion, Seccion 5).
class GoogleOAuthReauthReason(str, enum.Enum):
    TOKEN_EXPIRED_NO_REFRESH = "TOKEN_EXPIRED_NO_REFRESH"
    TOKEN_REVOKED = "TOKEN_REVOKED"
    ENCRYPTION_KEY_CHANGED = "ENCRYPTION_KEY_CHANGED"
    DECRYPTION_FAILED = "DECRYPTION_FAILED"


class GoogleOAuthCapability(str, enum.Enum):
    IDENTITY = "IDENTITY"
    CALENDAR = "CALENDAR"
    DRIVE = "DRIVE"


class GoogleOAuthAttemptStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class GoogleOAuthAttemptErrorCode(str, enum.Enum):
    AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"
    STATE_EXPIRED = "STATE_EXPIRED"
    REFRESH_TOKEN_REQUIRED = "REFRESH_TOKEN_REQUIRED"
    GOOGLE_ACCOUNT_MISMATCH = "GOOGLE_ACCOUNT_MISMATCH"
    ACCOUNT_ALREADY_CONNECTED = "ACCOUNT_ALREADY_CONNECTED"
    INVALID_EMAIL_NOT_VERIFIED = "INVALID_EMAIL_NOT_VERIFIED"
    TOKEN_EXCHANGE_FAILED = "TOKEN_EXCHANGE_FAILED"
    USERINFO_FAILED = "USERINFO_FAILED"
    UNRECOVERABLE_ERROR = "UNRECOVERABLE_ERROR"


_CONNECTION_STATUS_VALUES = tuple(status.value for status in GoogleOAuthConnectionStatus)
_DISCONNECTION_ORIGIN_VALUES = tuple(origin.value for origin in GoogleOAuthDisconnectionOrigin)
_REAUTH_REASON_VALUES = tuple(reason.value for reason in GoogleOAuthReauthReason)
_CAPABILITY_VALUES = tuple(capability.value for capability in GoogleOAuthCapability)
_ATTEMPT_STATUS_VALUES = tuple(status.value for status in GoogleOAuthAttemptStatus)
_ATTEMPT_ERROR_CODE_VALUES = tuple(code.value for code in GoogleOAuthAttemptErrorCode)

# --- google_oauth_connections: CHECK constraints (nombres y forma exactos del plan v3 + correcciones) ---

_STATUS_COHERENCE_CHECK = """
(
  status = 'ACTIVE' AND access_token_encrypted IS NOT NULL AND refresh_token_encrypted IS NOT NULL
  AND access_token_expires_at IS NOT NULL AND token_key_id IS NOT NULL
  AND reauth_required_at IS NULL AND reauth_reason IS NULL
  AND disconnected_at IS NULL AND disconnected_by IS NULL AND disconnection_origin IS NULL
)
OR
(
  status = 'NEEDS_REAUTH' AND access_token_encrypted IS NULL AND refresh_token_encrypted IS NULL
  AND access_token_expires_at IS NULL AND token_key_id IS NULL
  AND reauth_required_at IS NOT NULL AND reauth_reason IS NOT NULL
  AND disconnected_at IS NULL AND disconnected_by IS NULL AND disconnection_origin IS NULL
)
OR
(
  status = 'DISCONNECTED' AND access_token_encrypted IS NULL AND refresh_token_encrypted IS NULL
  AND access_token_expires_at IS NULL AND token_key_id IS NULL
  AND reauth_required_at IS NULL AND reauth_reason IS NULL
  AND disconnected_at IS NOT NULL AND disconnection_origin IS NOT NULL
  AND (
    (disconnection_origin = 'MANUAL' AND disconnected_by IS NOT NULL)
    OR (disconnection_origin = 'SYSTEM' AND disconnected_by IS NULL)
  )
)
""".strip()


class GoogleOAuthConnection(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Conexion OAuth Google de un usuario (Fase 3 / Bloque 3.1, ver
    ADR-0062). Una fila por usuario -- se reutiliza a traves de
    reconexiones y de reemplazos de cuenta Google tras DISCONNECTED (ver
    Seccion 10 del plan v3); nunca se crea una fila nueva para el mismo
    usuario."""

    __tablename__ = "google_oauth_connections"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_google_oauth_connections_id_tenant"),
        UniqueConstraint(
            "id", "tenant_id", "user_id", name="uq_google_oauth_connections_id_tenant_user"
        ),
        UniqueConstraint("tenant_id", "user_id", name="uq_google_oauth_connections_tenant_user"),
        UniqueConstraint(
            "tenant_id", "google_account_id", name="uq_google_oauth_connections_tenant_account"
        ),
        CheckConstraint("provider IN ('google')", name="ck_google_oauth_connections_provider"),
        CheckConstraint(
            f"status IN {_CONNECTION_STATUS_VALUES}", name="ck_google_oauth_connections_status"
        ),
        CheckConstraint(
            f"disconnection_origin IS NULL OR disconnection_origin IN {_DISCONNECTION_ORIGIN_VALUES}",
            name="ck_google_oauth_connections_disconnection_origin",
        ),
        CheckConstraint(
            f"reauth_reason IS NULL OR reauth_reason IN {_REAUTH_REASON_VALUES}",
            name="ck_google_oauth_connections_reauth_reason",
        ),
        CheckConstraint("version >= 1", name="ck_google_oauth_connections_version_positive"),
        CheckConstraint(
            "jsonb_typeof(scopes_granted) = 'array'", name="ck_google_oauth_connections_scopes_array"
        ),
        CheckConstraint(_STATUS_COHERENCE_CHECK, name="ck_google_oauth_connections_status_coherence"),
        ForeignKeyConstraint(
            ["user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_google_oauth_connections_user_tenant",
        ),
        ForeignKeyConstraint(
            ["disconnected_by", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_google_oauth_connections_disconnected_by_tenant",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="google")
    google_account_email: Mapped[str] = mapped_column(String(255), nullable=False)
    google_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    scopes_granted: Mapped[list] = mapped_column(JSONB, nullable=False)
    access_token_encrypted: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    refresh_token_encrypted: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    token_key_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=GoogleOAuthConnectionStatus.ACTIVE.value
    )
    first_connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    authorized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reauth_required_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reauth_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disconnected_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    disconnection_origin: Mapped[str | None] = mapped_column(String(10), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


# --- google_oauth_authorization_attempts: CHECK constraints ---

_ATTEMPT_STATUS_COHERENCE_CHECK = """
(
  status = 'PENDING' AND code_verifier_encrypted IS NOT NULL AND credential_key_id IS NOT NULL
  AND consumed_at IS NULL AND completed_at IS NULL AND error_code IS NULL
)
OR
(
  status = 'PROCESSING' AND code_verifier_encrypted IS NULL AND credential_key_id IS NULL
  AND consumed_at IS NOT NULL AND completed_at IS NULL AND error_code IS NULL
)
OR
(
  status = 'SUCCEEDED' AND code_verifier_encrypted IS NULL AND credential_key_id IS NULL
  AND consumed_at IS NOT NULL AND completed_at IS NOT NULL AND error_code IS NULL
)
OR
(
  status = 'FAILED' AND code_verifier_encrypted IS NULL AND credential_key_id IS NULL
  AND completed_at IS NOT NULL AND error_code IS NOT NULL
)
""".strip()


class GoogleOAuthAuthorizationAttempt(Base, UUIDPrimaryKeyMixin):
    """Intento de autorizacion OAuth persistido con PKCE (Fase 3 / Bloque
    3.1, ver ADR-0063). Maquina de estados de 4 valores
    (PENDING/PROCESSING/SUCCEEDED/FAILED); el `code_verifier` se purga de
    la fila en el instante en que el `state` se consume (transicion a
    PROCESSING), nunca convive con un intento ya resuelto. Append-only en
    la practica -- sin eliminacion fisica."""

    __tablename__ = "google_oauth_authorization_attempts"
    __table_args__ = (
        UniqueConstraint(
            "state_hash", name="uq_google_oauth_authorization_attempts_state_hash"
        ),
        CheckConstraint(
            f"requested_capability IN {_CAPABILITY_VALUES}", name="ck_google_oauth_attempts_capability"
        ),
        CheckConstraint(f"status IN {_ATTEMPT_STATUS_VALUES}", name="ck_google_oauth_attempts_status"),
        CheckConstraint(
            "jsonb_typeof(requested_scopes) = 'array'", name="ck_google_oauth_attempts_scopes_array"
        ),
        CheckConstraint(
            "state_hash ~ '^[0-9a-f]{64}$'", name="ck_google_oauth_attempts_state_hash_format"
        ),
        CheckConstraint(
            "expires_at > created_at", name="ck_google_oauth_attempts_expiry_after_creation"
        ),
        CheckConstraint(
            f"error_code IS NULL OR error_code IN {_ATTEMPT_ERROR_CODE_VALUES}",
            name="ck_google_oauth_attempts_error_code",
        ),
        CheckConstraint(
            _ATTEMPT_STATUS_COHERENCE_CHECK, name="ck_google_oauth_attempts_status_coherence"
        ),
        ForeignKeyConstraint(
            ["user_id", "tenant_id"], ["users.id", "users.tenant_id"],
            name="fk_google_oauth_authorization_attempts_user_tenant",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code_verifier_encrypted: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    credential_key_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    requested_capability: Mapped[str] = mapped_column(String(20), nullable=False)
    requested_scopes: Mapped[list] = mapped_column(JSONB, nullable=False)
    return_path: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=GoogleOAuthAttemptStatus.PENDING.value
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
