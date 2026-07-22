from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.main import app
from app.models.security_event import SecurityEvent
from app.models.session import Session as SessionModel

settings = get_settings()


def _login(test_client, admin_user, password: str = "TestPassword123!"):
    response = test_client.post(
        "/api/v1/auth/login", json={"email": admin_user.email, "password": password}
    )
    assert response.status_code == 200
    csrf_token = response.cookies.get("csrf_token")
    test_client.headers.update({"X-CSRF-Token": csrf_token})
    return response


def test_session_rotates_on_each_login(client, admin_user, db_session) -> None:
    _login(client, admin_user)
    _login(client, admin_user)

    sessions = db_session.execute(
        select(SessionModel).where(SessionModel.user_id == admin_user.id)
    ).scalars().all()
    assert len(sessions) == 2
    assert sessions[0].token_hash != sessions[1].token_hash
    assert sessions[0].csrf_token_hash != sessions[1].csrf_token_hash


def test_session_expires_by_inactivity(client, admin_user, frozen_clock) -> None:
    _login(client, admin_user)

    frozen_clock["now"] = frozen_clock["now"] + timedelta(
        minutes=settings.session_inactivity_timeout_minutes + 1
    )

    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_session_expires_absolute_despite_recent_activity(
    client, admin_user, frozen_clock, db_session
) -> None:
    _login(client, admin_user)

    step = timedelta(minutes=settings.session_inactivity_timeout_minutes - 5)
    total = timedelta(0)
    limit = timedelta(hours=settings.session_ttl_hours)
    last_response = None

    while total < limit + step:
        frozen_clock["now"] = frozen_clock["now"] + step
        total += step
        last_response = client.post("/api/v1/auth/keep-alive")
        if last_response.status_code != 200:
            break

    assert last_response is not None
    assert last_response.status_code == 401

    last_event = db_session.execute(
        select(SecurityEvent)
        .where(SecurityEvent.event_type.in_(["SESSION_EXPIRED_ABSOLUTE", "SESSION_EXPIRED_INACTIVITY"]))
        .order_by(SecurityEvent.created_at.desc())
        .limit(1)
    ).scalar_one()
    assert last_event.event_type == "SESSION_EXPIRED_ABSOLUTE"


def test_logout_revokes_and_reusing_old_cookie_fails(client, admin_user, db_session) -> None:
    login_response = _login(client, admin_user)
    raw_cookie_value = login_response.cookies.get(settings.session_cookie_name)

    client.post("/api/v1/auth/logout")

    client.cookies.set(settings.session_cookie_name, raw_cookie_value)
    reuse_response = client.get("/api/v1/auth/me")
    assert reuse_response.status_code == 401


def test_concurrent_session_limit_evicts_oldest(client, admin_user, db_session) -> None:
    for _ in range(settings.session_max_concurrent + 1):
        _login(client, admin_user)

    sessions = db_session.execute(
        select(SessionModel)
        .where(SessionModel.user_id == admin_user.id)
        .order_by(SessionModel.created_at.asc())
    ).scalars().all()

    active = [s for s in sessions if s.revoked_at is None]
    assert len(active) == settings.session_max_concurrent
    assert sessions[0].revoked_at is not None  # la mas antigua fue expulsada


def test_list_and_revoke_other_sessions(client, admin_user, db_session) -> None:
    _login(client, admin_user)  # sesion A (la que se conserva)

    client2 = TestClient(app)
    _login(client2, admin_user)  # sesion B

    listing = client.get("/api/v1/auth/sessions")
    assert listing.status_code == 200
    sessions_payload = listing.json()
    assert len(sessions_payload) == 2
    current_flags = [s["is_current"] for s in sessions_payload]
    assert current_flags.count(True) == 1

    revoke_response = client.post("/api/v1/auth/sessions/revoke-others")
    assert revoke_response.status_code == 200
    assert revoke_response.json()["revoked_count"] == 1

    assert client2.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 200


def test_change_password_revokes_all_sessions_and_reissues_current(
    client, admin_user, db_session
) -> None:
    login_response = _login(client, admin_user)
    old_cookie_value = login_response.cookies.get(settings.session_cookie_name)

    response = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "TestPassword123!", "new_password": "NewPassword456!"},
    )
    assert response.status_code == 200

    # La cookie vieja ya no sirve (fue revocada, se emitio una sesion nueva).
    client.cookies.set(settings.session_cookie_name, old_cookie_value)
    assert client.get("/api/v1/auth/me").status_code == 401

    # La cookie nueva (establecida por el response) si funciona.
    new_cookie_value = response.cookies.get(settings.session_cookie_name)
    client.cookies.set(settings.session_cookie_name, new_cookie_value)
    assert client.get("/api/v1/auth/me").status_code == 200


def test_change_password_with_wrong_current_password_is_rejected(client, admin_user) -> None:
    _login(client, admin_user)
    response = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "Incorrecta!", "new_password": "NewPassword456!"},
    )
    assert response.status_code == 401


def test_keep_alive_updates_last_seen(client, admin_user, db_session) -> None:
    _login(client, admin_user)
    response = client.post("/api/v1/auth/keep-alive")
    assert response.status_code == 200
    assert "last_seen_at" in response.json()
