from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.login_attempt import LoginAttempt
from app.models.security_event import SecurityEvent
from app.models.session import Session as SessionModel


def test_login_success_sets_cookies_and_creates_session(client, admin_user, db_session) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "TestPassword123!"},
    )
    assert response.status_code == 200
    assert response.json()["email"] == admin_user.email
    assert "luis_ia_contable_session" in response.cookies
    assert "csrf_token" in response.cookies

    sessions = db_session.execute(
        select(SessionModel).where(SessionModel.user_id == admin_user.id)
    ).scalars().all()
    assert len(sessions) == 1
    assert sessions[0].csrf_token_hash

    logs = db_session.execute(
        select(AuditLog).where(AuditLog.action == "LOGIN_SUCCESS")
    ).scalars().all()
    assert len(logs) == 1


def test_login_failure_does_not_create_session_nor_audit_log(
    client, admin_user, db_session
) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "WrongPassword!"},
    )
    assert response.status_code == 401

    sessions = db_session.execute(
        select(SessionModel).where(SessionModel.user_id == admin_user.id)
    ).scalars().all()
    assert len(sessions) == 0

    # Ver ADR-0009: ningun fallo de login escribe en audit_logs.
    audit_rows = db_session.execute(select(AuditLog)).scalars().all()
    assert audit_rows == []

    attempts = db_session.execute(
        select(LoginAttempt).where(LoginAttempt.identifier == admin_user.email)
    ).scalars().all()
    assert len(attempts) == 1
    assert attempts[0].success is False
    assert attempts[0].tenant_id is None

    events = db_session.execute(
        select(SecurityEvent).where(SecurityEvent.event_type == "LOGIN_FAILURE_ATTEMPT")
    ).scalars().all()
    assert len(events) == 1
    # Se identifico una cuenta real (aunque la contrasena fue incorrecta): el
    # tenant/usuario se conservan internamente para investigacion, nunca se
    # exponen en la respuesta HTTP.
    assert events[0].tenant_id == admin_user.tenant_id
    assert events[0].user_id == admin_user.id


def test_me_without_cookie_returns_401(client) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_me_with_valid_session_returns_user(client, admin_user) -> None:
    client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "TestPassword123!"},
    )
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == admin_user.email


def test_logout_revokes_session(auth_client, admin_user, db_session) -> None:
    logout_response = auth_client.post("/api/v1/auth/logout")
    assert logout_response.status_code == 200
    assert "csrf_token" not in logout_response.cookies

    me_response = auth_client.get("/api/v1/auth/me")
    assert me_response.status_code == 401

    sessions = db_session.execute(
        select(SessionModel).where(SessionModel.user_id == admin_user.id)
    ).scalars().all()
    assert len(sessions) == 1
    assert sessions[0].revoked_at is not None


def test_logout_without_csrf_token_is_rejected(client, admin_user) -> None:
    client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "TestPassword123!"},
    )
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 403
