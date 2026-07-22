from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.session import Session as SessionModel


def test_login_success_sets_cookie_and_creates_session(client, admin_user, db_session) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "TestPassword123!"},
    )
    assert response.status_code == 200
    assert response.json()["email"] == admin_user.email
    assert "luis_ia_contable_session" in response.cookies

    sessions = db_session.execute(
        select(SessionModel).where(SessionModel.user_id == admin_user.id)
    ).scalars().all()
    assert len(sessions) == 1

    logs = db_session.execute(
        select(AuditLog).where(AuditLog.action == "LOGIN_SUCCESS")
    ).scalars().all()
    assert len(logs) == 1


def test_login_failure_does_not_create_session_and_audits_failure(
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

    logs = db_session.execute(
        select(AuditLog).where(AuditLog.action == "LOGIN_FAILURE")
    ).scalars().all()
    assert len(logs) == 1


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


def test_logout_revokes_session(client, admin_user, db_session) -> None:
    client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "TestPassword123!"},
    )
    logout_response = client.post("/api/v1/auth/logout")
    assert logout_response.status_code == 200

    me_response = client.get("/api/v1/auth/me")
    assert me_response.status_code == 401

    sessions = db_session.execute(
        select(SessionModel).where(SessionModel.user_id == admin_user.id)
    ).scalars().all()
    assert len(sessions) == 1
    assert sessions[0].revoked_at is not None
