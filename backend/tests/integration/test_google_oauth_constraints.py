"""Integridad a nivel de base de datos de `google_oauth_connections`/
`google_oauth_authorization_attempts` (Fase 3 / Bloque 3.1, ver
ADR-0062/ADR-0063): intentos de insercion SQL/ORM directa que deben ser
rechazados por las UNIQUE/CHECK/FK, incluso pasando por alto la capa de
servicio -- mismo patron que test_operational_task_constraints.py."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.security import hash_password
from app.models.google_oauth import GoogleOAuthAuthorizationAttempt, GoogleOAuthConnection
from app.models.user import User

_NOW = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)


def _attempt_and_expect_rejection(db_session, obj) -> None:
    savepoint = db_session.begin_nested()
    with pytest.raises(IntegrityError):
        db_session.add(obj)
        db_session.flush()
    savepoint.rollback()


def _valid_connection(tenant, user, **overrides) -> GoogleOAuthConnection:
    kwargs = dict(
        tenant_id=tenant.id,
        user_id=user.id,
        provider="google",
        google_account_email="user@example.com",
        google_account_id="sub-123",
        scopes_granted=["openid", "email"],
        access_token_encrypted=b"\x01" + b"0" * 12 + b"cipher",
        refresh_token_encrypted=b"\x01" + b"0" * 12 + b"cipher",
        token_key_id="v1",
        access_token_expires_at=_NOW + timedelta(hours=1),
        status="ACTIVE",
        first_connected_at=_NOW,
        authorized_at=_NOW,
        version=1,
    )
    kwargs.update(overrides)
    return GoogleOAuthConnection(**kwargs)


def _valid_attempt(tenant, user, **overrides) -> GoogleOAuthAuthorizationAttempt:
    kwargs = dict(
        tenant_id=tenant.id,
        user_id=user.id,
        state_hash="a" * 64,
        code_verifier_encrypted=b"\x01" + b"0" * 12 + b"cipher",
        credential_key_id="v1",
        requested_capability="IDENTITY",
        requested_scopes=["openid", "email"],
        return_path="/settings/integrations/google",
        status="PENDING",
        expires_at=_NOW + timedelta(minutes=10),
        created_at=_NOW,
    )
    kwargs.update(overrides)
    return GoogleOAuthAuthorizationAttempt(**kwargs)


# --- google_oauth_connections ---


def test_valid_active_connection_succeeds(db_session, tenant, admin_user) -> None:
    conn = _valid_connection(tenant, admin_user)
    db_session.add(conn)
    db_session.flush()
    assert conn.id is not None


def test_active_with_null_access_token_rejected(db_session, tenant, admin_user) -> None:
    conn = _valid_connection(tenant, admin_user, access_token_encrypted=None)
    _attempt_and_expect_rejection(db_session, conn)


def test_needs_reauth_with_non_null_tokens_rejected(db_session, tenant, admin_user) -> None:
    conn = _valid_connection(
        tenant,
        admin_user,
        status="NEEDS_REAUTH",
        reauth_required_at=_NOW,
        reauth_reason="TOKEN_REVOKED",
    )
    _attempt_and_expect_rejection(db_session, conn)


def test_valid_needs_reauth_succeeds(db_session, tenant, admin_user) -> None:
    conn = _valid_connection(
        tenant,
        admin_user,
        status="NEEDS_REAUTH",
        access_token_encrypted=None,
        refresh_token_encrypted=None,
        token_key_id=None,
        access_token_expires_at=None,
        reauth_required_at=_NOW,
        reauth_reason="TOKEN_REVOKED",
    )
    db_session.add(conn)
    db_session.flush()
    assert conn.id is not None


def test_reauth_reason_insufficient_scope_rejected(db_session, tenant, admin_user) -> None:
    """INSUFFICIENT_SCOPE nunca es un reauth_reason valido -- una capacidad
    no autorizada mantiene la conexion ACTIVE (ver correccion Seccion 5)."""
    conn = _valid_connection(
        tenant,
        admin_user,
        status="NEEDS_REAUTH",
        access_token_encrypted=None,
        refresh_token_encrypted=None,
        token_key_id=None,
        access_token_expires_at=None,
        reauth_required_at=_NOW,
        reauth_reason="INSUFFICIENT_SCOPE",
    )
    _attempt_and_expect_rejection(db_session, conn)


def test_disconnected_manual_without_disconnected_by_rejected(db_session, tenant, admin_user) -> None:
    conn = _valid_connection(
        tenant,
        admin_user,
        status="DISCONNECTED",
        access_token_encrypted=None,
        refresh_token_encrypted=None,
        token_key_id=None,
        access_token_expires_at=None,
        disconnected_at=_NOW,
        disconnection_origin="MANUAL",
        disconnected_by=None,
    )
    _attempt_and_expect_rejection(db_session, conn)


def test_disconnected_system_with_disconnected_by_rejected(db_session, tenant, admin_user) -> None:
    conn = _valid_connection(
        tenant,
        admin_user,
        status="DISCONNECTED",
        access_token_encrypted=None,
        refresh_token_encrypted=None,
        token_key_id=None,
        access_token_expires_at=None,
        disconnected_at=_NOW,
        disconnection_origin="SYSTEM",
        disconnected_by=admin_user.id,
    )
    _attempt_and_expect_rejection(db_session, conn)


def test_invalid_status_value_rejected(db_session, tenant, admin_user) -> None:
    conn = _valid_connection(tenant, admin_user, status="BOGUS")
    _attempt_and_expect_rejection(db_session, conn)


def test_version_zero_rejected(db_session, tenant, admin_user) -> None:
    conn = _valid_connection(tenant, admin_user, version=0)
    _attempt_and_expect_rejection(db_session, conn)


def test_scopes_granted_must_be_json_array(db_session, tenant, admin_user) -> None:
    conn = _valid_connection(tenant, admin_user, scopes_granted={"not": "an array"})
    _attempt_and_expect_rejection(db_session, conn)


def test_duplicate_connection_same_user_rejected(db_session, tenant, admin_user) -> None:
    db_session.add(_valid_connection(tenant, admin_user))
    db_session.flush()
    dup = _valid_connection(tenant, admin_user, google_account_id="sub-different")
    _attempt_and_expect_rejection(db_session, dup)


def test_duplicate_google_account_different_users_same_tenant_rejected(
    db_session, tenant, admin_user, contador_user
) -> None:
    db_session.add(_valid_connection(tenant, admin_user, google_account_id="shared-sub"))
    db_session.flush()
    dup = _valid_connection(tenant, contador_user, google_account_id="shared-sub")
    _attempt_and_expect_rejection(db_session, dup)


def test_connection_cross_tenant_user_rejected(db_session, tenant, admin_user) -> None:
    """FK compuesta (user_id, tenant_id) -> users(id, tenant_id): un usuario
    de otro tenant nunca puede ser el titular de una conexion de este tenant."""
    from app.models.tenant import Tenant

    other_tenant = Tenant(name="Otro tenant")
    db_session.add(other_tenant)
    db_session.flush()
    other_user = User(
        tenant_id=other_tenant.id,
        email="otro@luisiacontable-tests.dev",
        full_name="Usuario de otro tenant",
        password_hash=hash_password("TestPassword123!"),
        role="admin",
        is_active=True,
    )
    db_session.add(other_user)
    db_session.flush()

    conn = _valid_connection(tenant, other_user)
    _attempt_and_expect_rejection(db_session, conn)


# --- google_oauth_authorization_attempts ---


def test_valid_pending_attempt_succeeds(db_session, tenant, admin_user) -> None:
    attempt = _valid_attempt(tenant, admin_user)
    db_session.add(attempt)
    db_session.flush()
    assert attempt.id is not None


def test_pending_with_null_verifier_rejected(db_session, tenant, admin_user) -> None:
    attempt = _valid_attempt(tenant, admin_user, code_verifier_encrypted=None)
    _attempt_and_expect_rejection(db_session, attempt)


def test_processing_with_non_null_verifier_rejected(db_session, tenant, admin_user) -> None:
    attempt = _valid_attempt(
        tenant, admin_user, status="PROCESSING", consumed_at=_NOW
    )
    _attempt_and_expect_rejection(db_session, attempt)


def test_valid_processing_succeeds(db_session, tenant, admin_user) -> None:
    attempt = _valid_attempt(
        tenant,
        admin_user,
        status="PROCESSING",
        code_verifier_encrypted=None,
        credential_key_id=None,
        consumed_at=_NOW,
    )
    db_session.add(attempt)
    db_session.flush()
    assert attempt.id is not None


def test_succeeded_missing_completed_at_rejected(db_session, tenant, admin_user) -> None:
    attempt = _valid_attempt(
        tenant,
        admin_user,
        status="SUCCEEDED",
        code_verifier_encrypted=None,
        credential_key_id=None,
        consumed_at=_NOW,
        completed_at=None,
    )
    _attempt_and_expect_rejection(db_session, attempt)


def test_failed_missing_error_code_rejected(db_session, tenant, admin_user) -> None:
    attempt = _valid_attempt(
        tenant,
        admin_user,
        status="FAILED",
        code_verifier_encrypted=None,
        credential_key_id=None,
        completed_at=_NOW,
        error_code=None,
    )
    _attempt_and_expect_rejection(db_session, attempt)


def test_failed_with_null_consumed_at_succeeds(db_session, tenant, admin_user) -> None:
    """FAILED admite consumed_at NULL -- Google rechazo antes de aceptar el
    state para procesamiento (p. ej. access_denied directo)."""
    attempt = _valid_attempt(
        tenant,
        admin_user,
        status="FAILED",
        code_verifier_encrypted=None,
        credential_key_id=None,
        consumed_at=None,
        completed_at=_NOW,
        error_code="AUTHORIZATION_DENIED",
    )
    db_session.add(attempt)
    db_session.flush()
    assert attempt.id is not None


def test_invalid_capability_rejected(db_session, tenant, admin_user) -> None:
    """El CHECK a nivel de BD admite IDENTITY/CALENDAR/DRIVE (las 3
    capacidades del diseño final de Fase 3) -- la restriccion a solo
    IDENTITY en el Bloque 3.1 se aplica en el esquema Pydantic del
    endpoint, no en este CHECK. Un valor realmente invalido sigue rechazado."""
    attempt = _valid_attempt(tenant, admin_user, requested_capability="GMAIL")
    _attempt_and_expect_rejection(db_session, attempt)


def test_invalid_error_code_rejected(db_session, tenant, admin_user) -> None:
    attempt = _valid_attempt(
        tenant,
        admin_user,
        status="FAILED",
        code_verifier_encrypted=None,
        credential_key_id=None,
        completed_at=_NOW,
        error_code="NOT_A_REAL_CODE",
    )
    _attempt_and_expect_rejection(db_session, attempt)


def test_state_hash_wrong_format_rejected(db_session, tenant, admin_user) -> None:
    attempt = _valid_attempt(tenant, admin_user, state_hash="not-hex-and-wrong-length")
    _attempt_and_expect_rejection(db_session, attempt)


def test_expiry_not_after_creation_rejected(db_session, tenant, admin_user) -> None:
    attempt = _valid_attempt(tenant, admin_user, expires_at=_NOW - timedelta(minutes=1))
    _attempt_and_expect_rejection(db_session, attempt)


def test_requested_scopes_must_be_json_array(db_session, tenant, admin_user) -> None:
    attempt = _valid_attempt(tenant, admin_user, requested_scopes={"not": "array"})
    _attempt_and_expect_rejection(db_session, attempt)


def test_duplicate_state_hash_rejected(db_session, tenant, admin_user) -> None:
    db_session.add(_valid_attempt(tenant, admin_user, state_hash="b" * 64))
    db_session.flush()
    dup = _valid_attempt(tenant, admin_user, state_hash="b" * 64)
    _attempt_and_expect_rejection(db_session, dup)
