from datetime import timedelta

from sqlalchemy import select

from app.core.config import get_settings
from app.models.audit_log import AuditLog
from app.models.security_event import SecurityEvent

settings = get_settings()


def _fail_login(client, email: str, password: str = "WrongPassword!"):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def test_lockout_triggers_after_max_attempts(client, admin_user, frozen_clock) -> None:
    for _ in range(settings.login_max_attempts):
        response = _fail_login(client, admin_user.email)
        assert response.status_code == 401

    locked_response = _fail_login(client, admin_user.email)
    assert locked_response.status_code == 429
    assert "Retry-After" in locked_response.headers


def test_nonexistent_email_locks_out_identically(client, frozen_clock) -> None:
    identifier = "no-existe@dominio-ficticio.dev"
    for _ in range(settings.login_max_attempts):
        response = _fail_login(client, identifier)
        assert response.status_code == 401

    locked_response = _fail_login(client, identifier)
    assert locked_response.status_code == 429
    assert locked_response.json()["detail"] == "Demasiados intentos. Intenta nuevamente más tarde."


def test_hundred_requests_during_lockout_do_not_change_locked_until(
    client, admin_user, frozen_clock, db_session
) -> None:
    for _ in range(settings.login_max_attempts):
        _fail_login(client, admin_user.email)

    first_locked = _fail_login(client, admin_user.email)
    first_retry_after = first_locked.headers["Retry-After"]

    for _ in range(100):
        response = _fail_login(client, admin_user.email)
        assert response.status_code == 429
        assert response.headers["Retry-After"] == first_retry_after

    from app.models.login_attempt import LoginAttempt

    attempts = db_session.execute(
        select(LoginAttempt).where(LoginAttempt.identifier == admin_user.email)
    ).scalars().all()
    # Los 100 intentos durante el bloqueo NO deben haberse insertado.
    assert len(attempts) == settings.login_max_attempts


def test_unlocks_after_configured_duration_and_successful_login_works(
    client, admin_user, frozen_clock
) -> None:
    for _ in range(settings.login_max_attempts):
        _fail_login(client, admin_user.email)

    locked_response = _fail_login(client, admin_user.email)
    assert locked_response.status_code == 429

    frozen_clock["now"] = frozen_clock["now"] + timedelta(
        minutes=settings.login_lockout_duration_minutes + 1
    )

    success_response = client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "TestPassword123!"},
    )
    assert success_response.status_code == 200


def test_variations_of_case_and_whitespace_count_toward_same_lockout(
    client, admin_user, frozen_clock
) -> None:
    variants = [
        admin_user.email.upper(),
        f"  {admin_user.email}  ",
        admin_user.email,
        admin_user.email.capitalize(),
        admin_user.email,
    ]
    assert len(variants) == settings.login_max_attempts
    for variant in variants:
        response = _fail_login(client, variant)
        assert response.status_code == 401

    locked_response = _fail_login(client, admin_user.email)
    assert locked_response.status_code == 429


def test_lockout_does_not_write_audit_logs(client, admin_user, frozen_clock, db_session) -> None:
    for _ in range(settings.login_max_attempts):
        _fail_login(client, admin_user.email)
    _fail_login(client, admin_user.email)  # ahora bloqueado

    audit_rows = db_session.execute(select(AuditLog)).scalars().all()
    assert audit_rows == []

    events = db_session.execute(
        select(SecurityEvent).where(SecurityEvent.event_type == "LOGIN_ATTEMPT_WHILE_LOCKED")
    ).scalars().all()
    assert len(events) == 1

    triggered = db_session.execute(
        select(SecurityEvent).where(SecurityEvent.event_type == "LOGIN_LOCKOUT_TRIGGERED")
    ).scalars().all()
    assert len(triggered) == 1
