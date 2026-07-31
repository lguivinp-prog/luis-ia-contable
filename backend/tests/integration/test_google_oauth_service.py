"""Servicio de integracion Google OAuth (Fase 3 / Bloque 3.1, ver
ADR-0062/ADR-0063/ADR-0065): flujo completo de 12 pasos del callback,
maquina de estados de intentos, ciclo de vida/reemplazo de cuenta y
desconexion -- todo contra `FakeOAuthProvider`, sin ninguna llamada de red."""

import base64
import os
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from app.core import integration_crypto
from app.domain.google_oauth.fake_provider import FakeAuthorizationCode, FakeOAuthProvider
from app.domain.google_oauth.provider import OAuthProviderError
from app.models.audit_log import AuditLog
from app.models.google_oauth import GoogleOAuthConnectionStatus
from app.repositories import google_oauth_repository as repo
from app.services import google_oauth_service as svc

_REDIRECT_URI = "http://localhost:8000/api/v1/integrations/google/callback"


@pytest.fixture(autouse=True)
def _configured_encryption_key(monkeypatch):
    settings = integration_crypto.get_settings()
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setattr(settings, "integration_token_encryption_key", key, raising=False)
    monkeypatch.setattr(settings, "integration_token_key_id", "v1", raising=False)


@pytest.fixture()
def fake_provider() -> FakeOAuthProvider:
    return FakeOAuthProvider()


def _extract_state(authorization_url: str) -> str:
    query = parse_qs(urlparse(authorization_url).query)
    return query["state"][0]


def _start(
    db_session,
    tenant,
    user,
    fake_provider,
    capability="IDENTITY",
    return_path="/settings/integrations/google",
):
    url, attempt_id = svc.start_authorization(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        capability=capability,
        return_path=return_path,
        redirect_uri=_REDIRECT_URI,
        oauth_provider=fake_provider,
    )
    return _extract_state(url), attempt_id


def _complete(
    db_session,
    tenant,
    user,
    fake_provider,
    *,
    sub="sub-1",
    email="user@example.com",
    email_verified=True,
    issue_refresh_token=True,
    error=None,
    state=None,
):
    if state is None:
        state, _ = _start(db_session, tenant, user, fake_provider)
    code = f"code-{state[:8]}"
    if error is None:
        fake_provider.register_authorization_code(
            code,
            FakeAuthorizationCode(
                sub=sub,
                email=email,
                email_verified=email_verified,
                scopes=("openid", "email"),
                issue_refresh_token=issue_refresh_token,
            ),
        )
    return svc.process_callback(
        db_session,
        current_tenant_id=tenant.id,
        current_user_id=user.id,
        state=state,
        code=None if error else code,
        error=error,
        redirect_uri=_REDIRECT_URI,
        oauth_provider=fake_provider,
    )


# --- start_authorization ---


def test_start_authorization_creates_pending_attempt(db_session, tenant, admin_user, fake_provider):
    state, attempt_id = _start(db_session, tenant, admin_user, fake_provider)
    attempt = repo.lock_attempt_by_id(db_session, tenant.id, attempt_id)
    assert attempt.status == "PENDING"
    assert attempt.requested_capability == "IDENTITY"
    assert attempt.code_verifier_encrypted is not None
    assert state


def test_start_authorization_rejects_when_encryption_not_configured(
    db_session, tenant, admin_user, fake_provider, monkeypatch
):
    settings = integration_crypto.get_settings()
    monkeypatch.setattr(settings, "integration_token_encryption_key", "", raising=False)
    with pytest.raises(svc.IntegrationMisconfiguredError):
        _start(db_session, tenant, admin_user, fake_provider)


def test_start_authorization_rejects_disabled_capability(db_session, tenant, admin_user, fake_provider):
    with pytest.raises(svc.CapabilityNotEnabledError):
        _start(db_session, tenant, admin_user, fake_provider, capability="CALENDAR")


# --- callback: state invalido/expirado/error de Google ---


def test_callback_unknown_state_raises_invalid_state(db_session, tenant, admin_user, fake_provider):
    with pytest.raises(svc.InvalidStateError):
        svc.process_callback(
            db_session,
            current_tenant_id=tenant.id,
            current_user_id=admin_user.id,
            state="does-not-exist",
            code="whatever",
            error=None,
            redirect_uri=_REDIRECT_URI,
            oauth_provider=fake_provider,
        )


def test_callback_second_call_same_state_raises_invalid_state(db_session, tenant, admin_user, fake_provider):
    outcome = _complete(db_session, tenant, admin_user, fake_provider)
    assert outcome.succeeded

    state, _ = _start(db_session, tenant, admin_user, fake_provider)
    code = f"code-{state[:8]}"
    fake_provider.register_authorization_code(
        code, FakeAuthorizationCode(sub="sub-2", email="a@b.com")
    )
    first = svc.process_callback(
        db_session,
        current_tenant_id=tenant.id,
        current_user_id=admin_user.id,
        state=state,
        code=code,
        error=None,
        redirect_uri=_REDIRECT_URI,
        oauth_provider=fake_provider,
    )
    # sub distinto de la conexion ACTIVE ya creada -> mismatch, no invalid_state
    assert first.succeeded is False

    with pytest.raises(svc.InvalidStateError):
        svc.process_callback(
            db_session,
            current_tenant_id=tenant.id,
            current_user_id=admin_user.id,
            state=state,
            code=code,
            error=None,
            redirect_uri=_REDIRECT_URI,
            oauth_provider=fake_provider,
        )


def test_callback_expired_state_marks_failed_state_expired(
    db_session, tenant, admin_user, fake_provider, frozen_clock
):
    state, attempt_id = _start(db_session, tenant, admin_user, fake_provider)
    frozen_clock["now"] = frozen_clock["now"] + timedelta(minutes=11)
    code = f"code-{state[:8]}"
    fake_provider.register_authorization_code(code, FakeAuthorizationCode(sub="s", email="e@e.com"))

    outcome = svc.process_callback(
        db_session,
        current_tenant_id=tenant.id,
        current_user_id=admin_user.id,
        state=state,
        code=code,
        error=None,
        redirect_uri=_REDIRECT_URI,
        oauth_provider=fake_provider,
    )
    assert outcome.succeeded is False
    assert outcome.error_code == "STATE_EXPIRED"
    attempt = repo.lock_attempt_by_id(db_session, tenant.id, attempt_id)
    assert attempt.status == "FAILED"
    assert attempt.code_verifier_encrypted is None
    assert attempt.consumed_at is not None
    assert attempt.completed_at is not None


def test_callback_authorization_denied_marks_failed(db_session, tenant, admin_user, fake_provider):
    outcome = _complete(db_session, tenant, admin_user, fake_provider, error="access_denied")
    assert outcome.succeeded is False
    assert outcome.error_code == "AUTHORIZATION_DENIED"


# --- callback: exito, refresh token, email no verificado ---


def test_callback_success_creates_active_connection(db_session, tenant, admin_user, fake_provider):
    outcome = _complete(db_session, tenant, admin_user, fake_provider, sub="sub-1", email="user@example.com")
    assert outcome.succeeded is True
    conn = outcome.connection
    assert conn.status == GoogleOAuthConnectionStatus.ACTIVE.value
    assert conn.google_account_id == "sub-1"
    assert conn.google_account_email == "user@example.com"
    assert conn.version == 1
    assert conn.access_token_encrypted is not None
    assert conn.refresh_token_encrypted is not None


def test_callback_email_not_verified_rejected(db_session, tenant, admin_user, fake_provider):
    outcome = _complete(db_session, tenant, admin_user, fake_provider, email_verified=False)
    assert outcome.succeeded is False
    assert outcome.error_code == "INVALID_EMAIL_NOT_VERIFIED"
    assert repo.get_connection(db_session, tenant.id, admin_user.id) is None


def test_callback_empty_sub_rejected(db_session, tenant, admin_user, fake_provider):
    """Defensa en profundidad: un `sub` vacio devuelto por userinfo se
    rechaza en el servicio, incluso si el proveedor (por error o por ser un
    FakeOAuthProvider mal configurado en una prueba) no lo valido."""
    outcome = _complete(db_session, tenant, admin_user, fake_provider, sub="")
    assert outcome.succeeded is False
    assert outcome.error_code == "USERINFO_FAILED"
    assert repo.get_connection(db_session, tenant.id, admin_user.id) is None


def test_first_connection_without_refresh_token_fails(db_session, tenant, admin_user, fake_provider):
    outcome = _complete(db_session, tenant, admin_user, fake_provider, issue_refresh_token=False)
    assert outcome.succeeded is False
    assert outcome.error_code == "REFRESH_TOKEN_REQUIRED"
    assert repo.get_connection(db_session, tenant.id, admin_user.id) is None


def test_incremental_without_refresh_token_conserves_existing(db_session, tenant, admin_user, fake_provider):
    first = _complete(db_session, tenant, admin_user, fake_provider, sub="sub-1")
    assert first.succeeded
    original_refresh = first.connection.refresh_token_encrypted

    second = _complete(
        db_session, tenant, admin_user, fake_provider, sub="sub-1", issue_refresh_token=False
    )
    assert second.succeeded
    assert second.connection.refresh_token_encrypted == original_refresh
    assert second.connection.version == 2


def test_sub_mismatch_on_active_connection_rejected(db_session, tenant, admin_user, fake_provider):
    first = _complete(db_session, tenant, admin_user, fake_provider, sub="sub-1")
    assert first.succeeded

    second = _complete(db_session, tenant, admin_user, fake_provider, sub="sub-DIFFERENT")
    assert second.succeeded is False
    assert second.error_code == "GOOGLE_ACCOUNT_MISMATCH"
    unchanged = repo.get_connection(db_session, tenant.id, admin_user.id)
    assert unchanged.google_account_id == "sub-1"


def test_sub_mismatch_on_needs_reauth_rejected(db_session, tenant, admin_user, fake_provider):
    first = _complete(db_session, tenant, admin_user, fake_provider, sub="sub-1")
    assert first.succeeded
    conn = repo.lock_connection_for_update(db_session, tenant.id, admin_user.id)
    conn.status = "NEEDS_REAUTH"
    conn.access_token_encrypted = None
    conn.refresh_token_encrypted = None
    conn.token_key_id = None
    conn.access_token_expires_at = None
    conn.reauth_required_at = first.connection.authorized_at
    conn.reauth_reason = "TOKEN_REVOKED"
    db_session.flush()

    second = _complete(db_session, tenant, admin_user, fake_provider, sub="sub-DIFFERENT")
    assert second.succeeded is False
    assert second.error_code == "GOOGLE_ACCOUNT_MISMATCH"


def test_reconnect_from_needs_reauth_without_refresh_token_fails(
    db_session, tenant, admin_user, fake_provider
):
    """Distinta de `test_incremental_without_refresh_token_conserves_
    existing` (esa cubre una conexion ACTIVE ampliando scopes y SI conserva
    el refresh_token previo). Aqui la conexion ya esta en NEEDS_REAUTH sin
    ningun token utilizable -- si Google tampoco emite un refresh_token en la
    reconexion, no hay ninguno que conservar y la operacion debe fallar sin
    resucitar la conexion."""
    first = _complete(db_session, tenant, admin_user, fake_provider, sub="sub-1")
    assert first.succeeded
    conn = repo.lock_connection_for_update(db_session, tenant.id, admin_user.id)
    conn.status = "NEEDS_REAUTH"
    conn.access_token_encrypted = None
    conn.refresh_token_encrypted = None
    conn.token_key_id = None
    conn.access_token_expires_at = None
    conn.reauth_required_at = first.connection.authorized_at
    conn.reauth_reason = "TOKEN_REVOKED"
    db_session.flush()
    version_before = conn.version
    connect_audit_before = (
        db_session.query(AuditLog).filter(AuditLog.action == "INTEGRATION_GOOGLE_CONNECT").count()
    )

    state, attempt_id = _start(db_session, tenant, admin_user, fake_provider)
    code = f"code-{state[:8]}"
    fake_provider.register_authorization_code(
        code,
        FakeAuthorizationCode(sub="sub-1", email="user@example.com", issue_refresh_token=False),
    )
    outcome = svc.process_callback(
        db_session,
        current_tenant_id=tenant.id,
        current_user_id=admin_user.id,
        state=state,
        code=code,
        error=None,
        redirect_uri=_REDIRECT_URI,
        oauth_provider=fake_provider,
    )

    assert outcome.succeeded is False
    assert outcome.error_code == "REFRESH_TOKEN_REQUIRED"

    attempt = repo.lock_attempt_by_id(db_session, tenant.id, attempt_id)
    assert attempt.status == "FAILED"
    assert attempt.error_code == "REFRESH_TOKEN_REQUIRED"

    unchanged = repo.get_connection(db_session, tenant.id, admin_user.id)
    assert unchanged.status == "NEEDS_REAUTH"
    assert unchanged.version == version_before
    assert unchanged.access_token_encrypted is None
    assert unchanged.refresh_token_encrypted is None
    assert unchanged.access_token_expires_at is None

    connect_audit_after = (
        db_session.query(AuditLog).filter(AuditLog.action == "INTEGRATION_GOOGLE_CONNECT").count()
    )
    assert connect_audit_after == connect_audit_before


def test_account_replacement_after_disconnect(db_session, tenant, admin_user, fake_provider):
    first = _complete(db_session, tenant, admin_user, fake_provider, sub="sub-1")
    assert first.succeeded
    original_first_connected_at = first.connection.first_connected_at

    connection, revocation_ok = svc.disconnect(
        db_session,
        tenant_id=tenant.id,
        user_id=admin_user.id,
        actor_user_id=admin_user.id,
        oauth_provider=fake_provider,
    )
    assert connection.status == "DISCONNECTED"

    second = _complete(db_session, tenant, admin_user, fake_provider, sub="sub-REPLACEMENT")
    assert second.succeeded is True
    assert second.connection.google_account_id == "sub-REPLACEMENT"
    assert second.connection.first_connected_at == original_first_connected_at
    assert second.connection.version == 3  # 1 (creado) -> 2 (disconnect) -> 3 (reemplazo)

    replaced_audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "INTEGRATION_GOOGLE_ACCOUNT_REPLACED")
        .all()
    )
    assert len(replaced_audit) == 1


# --- disconnect ---


def test_disconnect_with_successful_revocation(db_session, tenant, admin_user, fake_provider):
    outcome = _complete(db_session, tenant, admin_user, fake_provider)
    assert outcome.succeeded
    connection, revocation_ok = svc.disconnect(
        db_session,
        tenant_id=tenant.id,
        user_id=admin_user.id,
        actor_user_id=admin_user.id,
        oauth_provider=fake_provider,
    )
    assert connection.status == "DISCONNECTED"
    assert connection.disconnection_origin == "MANUAL"
    assert connection.disconnected_by == admin_user.id
    assert revocation_ok is True


def test_disconnect_when_revocation_fails_still_disconnects_locally(
    db_session, tenant, admin_user, fake_provider
):
    outcome = _complete(db_session, tenant, admin_user, fake_provider)
    assert outcome.succeeded

    def _boom(*, token):
        raise OAuthProviderError("simulated remote failure")

    fake_provider.revoke_token = _boom
    connection, revocation_ok = svc.disconnect(
        db_session,
        tenant_id=tenant.id,
        user_id=admin_user.id,
        actor_user_id=admin_user.id,
        oauth_provider=fake_provider,
    )
    assert connection.status == "DISCONNECTED"
    assert revocation_ok is False


def test_disconnect_nonexistent_connection_raises_not_found(db_session, tenant, admin_user, fake_provider):
    with pytest.raises(svc.ConnectionNotFoundError):
        svc.disconnect(
            db_session,
            tenant_id=tenant.id,
            user_id=admin_user.id,
            actor_user_id=admin_user.id,
            oauth_provider=fake_provider,
        )


def test_disconnect_for_user_deactivation_never_calls_provider(db_session, tenant, admin_user, fake_provider):
    outcome = _complete(db_session, tenant, admin_user, fake_provider)
    assert outcome.succeeded

    calls = []
    fake_provider.revoke_token = lambda *, token: calls.append(token)

    svc.disconnect_for_user_deactivation(db_session, tenant_id=tenant.id, user_id=admin_user.id)

    connection = repo.get_connection(db_session, tenant.id, admin_user.id)
    assert connection.status == "DISCONNECTED"
    assert connection.disconnection_origin == "SYSTEM"
    assert connection.disconnected_by is None
    assert calls == []  # nunca se llamo al proveedor


# --- get_status ---


def test_get_status_reflects_identity_authorized(db_session, tenant, admin_user, fake_provider):
    status_before = svc.get_status(db_session, tenant_id=tenant.id, user_id=admin_user.id)
    assert status_before["connected"] is False
    assert status_before["identity_authorized"] is False

    _complete(db_session, tenant, admin_user, fake_provider)
    status_after = svc.get_status(db_session, tenant_id=tenant.id, user_id=admin_user.id)
    assert status_after["connected"] is True
    assert status_after["identity_authorized"] is True
    assert status_after["calendar_authorized"] is False
    assert status_after["drive_authorized"] is False
