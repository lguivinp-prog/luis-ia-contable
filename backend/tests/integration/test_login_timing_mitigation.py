from sqlalchemy import select

import app.services.auth_service as auth_service_module
from app.models.audit_log import AuditLog
from app.models.login_attempt import LoginAttempt
from app.models.security_event import SecurityEvent


def _spy_on_verify_password(monkeypatch):
    call_count = {"n": 0}
    original = auth_service_module.verify_password

    def spy(*args, **kwargs):
        call_count["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(auth_service_module, "verify_password", spy)
    return call_count


def test_verify_password_runs_for_nonexistent_and_existing_accounts(
    client, admin_user, monkeypatch
) -> None:
    call_count = _spy_on_verify_password(monkeypatch)

    nonexistent_response = client.post(
        "/api/v1/auth/login",
        json={"email": "no-existe@dominio-ficticio.dev", "password": "cualquiera"},
    )
    assert nonexistent_response.status_code == 401
    assert call_count["n"] == 1, "verify_password debe ejecutarse aunque la cuenta no exista"

    call_count["n"] = 0
    wrong_password_response = client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "WrongPassword!"},
    )
    assert wrong_password_response.status_code == 401
    assert call_count["n"] == 1, "verify_password debe ejecutarse igual para una cuenta real"


def test_response_shape_is_identical_for_both_failure_cases(client, admin_user) -> None:
    nonexistent_response = client.post(
        "/api/v1/auth/login",
        json={"email": "otro-inexistente@dominio-ficticio.dev", "password": "x"},
    )
    wrong_password_response = client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "WrongPassword!"},
    )

    assert nonexistent_response.status_code == wrong_password_response.status_code == 401
    assert nonexistent_response.json() == wrong_password_response.json()
    assert set(nonexistent_response.json().keys()) == set(wrong_password_response.json().keys())


def test_both_failure_cases_produce_identical_persistence_footprint(
    client, admin_user, db_session
) -> None:
    nonexistent_identifier = "tercero-inexistente@dominio-ficticio.dev"

    client.post(
        "/api/v1/auth/login", json={"email": nonexistent_identifier, "password": "x"}
    )
    client.post(
        "/api/v1/auth/login", json={"email": admin_user.email, "password": "WrongPassword!"}
    )

    attempts_nonexistent = db_session.execute(
        select(LoginAttempt).where(LoginAttempt.identifier == nonexistent_identifier)
    ).scalars().all()
    attempts_existing = db_session.execute(
        select(LoginAttempt).where(LoginAttempt.identifier == admin_user.email)
    ).scalars().all()
    assert len(attempts_nonexistent) == len(attempts_existing) == 1
    assert attempts_nonexistent[0].tenant_id is None
    assert attempts_existing[0].tenant_id is None  # tenant solo se rellena en exito

    events_nonexistent = db_session.execute(
        select(SecurityEvent).where(SecurityEvent.identifier == nonexistent_identifier)
    ).scalars().all()
    events_existing = db_session.execute(
        select(SecurityEvent).where(SecurityEvent.identifier == admin_user.email)
    ).scalars().all()
    assert len(events_nonexistent) == len(events_existing) == 1

    # Unica diferencia permitida: el valor interno de tenant_id/user_id en
    # security_events (nunca expuesto en la respuesta HTTP).
    assert events_nonexistent[0].tenant_id is None
    assert events_nonexistent[0].user_id is None
    assert events_existing[0].tenant_id == admin_user.tenant_id
    assert events_existing[0].user_id == admin_user.id

    audit_rows = db_session.execute(select(AuditLog)).scalars().all()
    assert audit_rows == [], "Ningun fallo de login debe escribir en audit_logs"


def test_successful_login_still_writes_audit_log(client, admin_user, db_session) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "TestPassword123!"},
    )
    assert response.status_code == 200

    logs = db_session.execute(
        select(AuditLog).where(AuditLog.action == "LOGIN_SUCCESS")
    ).scalars().all()
    assert len(logs) == 1
