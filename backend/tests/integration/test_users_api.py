"""Gestion de usuarios (Bloque 4, ADR-0051): CRUD completo, revocacion de
sesiones, aislamiento multitenant, auto-operacion rechazada, mismo-rol
rechazado, y el caso simple de un solo hilo del invariante de ultimo-admin
(la version con dos conexiones reales/concurrencia esta en
test_users_last_admin_concurrency.py)."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import clock
from app.core.security import hash_password
from app.main import app
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.models.user import User
from app.services import user_service
from app.services.user_service import LastActiveAdminError


def _create_user_payload(**overrides) -> dict:
    payload = {
        "email": f"nuevo-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
        "full_name": "Usuario Nuevo De Prueba",
        "role": "contador",
        "password": "PasswordTemporal123",
    }
    payload.update(overrides)
    return payload


# ---- CRUD completo ----


def test_list_users_includes_seeded_admin(auth_client, admin_user) -> None:
    response = auth_client.get("/api/v1/users")
    assert response.status_code == 200
    emails = {row["email"] for row in response.json()}
    assert admin_user.email in emails


def test_create_user_sets_must_change_password(auth_client) -> None:
    response = auth_client.post("/api/v1/users", json=_create_user_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["must_change_password"] is True
    assert body["is_active"] is True
    assert "password" not in body
    assert "password_hash" not in body


def test_create_user_rejects_duplicate_email(auth_client) -> None:
    payload = _create_user_payload()
    first = auth_client.post("/api/v1/users", json=payload)
    assert first.status_code == 201

    second = auth_client.post("/api/v1/users", json=_create_user_payload(email=payload["email"]))
    assert second.status_code == 409


def test_create_user_rejects_short_password(auth_client) -> None:
    response = auth_client.post("/api/v1/users", json=_create_user_payload(password="short"))
    assert response.status_code == 422


def test_change_role_success_is_audited(auth_client, db_session) -> None:
    created = auth_client.post("/api/v1/users", json=_create_user_payload()).json()
    response = auth_client.patch(
        f"/api/v1/users/{created['id']}/role",
        json={"role": "asistente", "reason": "Cambio de responsabilidades"},
    )
    assert response.status_code == 200
    assert response.json()["role"] == "asistente"

    logs = db_session.execute(
        select(AuditLog).where(AuditLog.action == "USER_ROLE_CHANGE")
    ).scalars().all()
    assert len(logs) == 1
    assert logs[0].log_metadata["from_role"] == "contador"
    assert logs[0].log_metadata["to_role"] == "asistente"


def test_change_role_to_same_role_is_rejected(auth_client) -> None:
    created = auth_client.post("/api/v1/users", json=_create_user_payload()).json()
    response = auth_client.patch(
        f"/api/v1/users/{created['id']}/role",
        json={"role": "contador", "reason": "sin cambio real"},
    )
    assert response.status_code == 422


def test_change_role_rejects_empty_reason(auth_client) -> None:
    created = auth_client.post("/api/v1/users", json=_create_user_payload()).json()
    response = auth_client.patch(
        f"/api/v1/users/{created['id']}/role", json={"role": "asistente", "reason": "   "}
    )
    assert response.status_code == 422


def test_deactivate_and_reactivate_cycle(auth_client) -> None:
    created = auth_client.post("/api/v1/users", json=_create_user_payload()).json()

    deactivated = auth_client.patch(
        f"/api/v1/users/{created['id']}/deactivate", json={"reason": "Fin de contrato"}
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False

    reactivated = auth_client.patch(f"/api/v1/users/{created['id']}/reactivate")
    assert reactivated.status_code == 200
    body = reactivated.json()
    assert body["is_active"] is True
    assert body["deactivated_at"] is None


def test_reactivate_already_active_user_is_rejected(auth_client) -> None:
    created = auth_client.post("/api/v1/users", json=_create_user_payload()).json()
    response = auth_client.patch(f"/api/v1/users/{created['id']}/reactivate")
    assert response.status_code == 422


def test_reset_password_sets_must_change_password_and_is_never_leaked(auth_client) -> None:
    created = auth_client.post("/api/v1/users", json=_create_user_payload()).json()
    response = auth_client.patch(
        f"/api/v1/users/{created['id']}/reset-password",
        json={"new_password": "OtraPasswordTemporal456", "reason": "Usuario olvido su clave"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["must_change_password"] is True
    assert "password" not in body
    assert "new_password" not in body
    assert "password_hash" not in body


# ---- Auto-operacion rechazada ----


def test_cannot_deactivate_self(auth_client, admin_user) -> None:
    response = auth_client.patch(
        f"/api/v1/users/{admin_user.id}/deactivate", json={"reason": "intento invalido"}
    )
    assert response.status_code == 409


def test_cannot_change_own_role(auth_client, admin_user) -> None:
    response = auth_client.patch(
        f"/api/v1/users/{admin_user.id}/role", json={"role": "contador", "reason": "intento invalido"}
    )
    assert response.status_code == 409


def test_cannot_reset_own_password_via_admin_endpoint(auth_client, admin_user) -> None:
    response = auth_client.patch(
        f"/api/v1/users/{admin_user.id}/reset-password",
        json={"new_password": "NuevaPasswordAdmin123", "reason": "intento invalido"},
    )
    assert response.status_code == 409


# ---- Aislamiento multitenant ----


def test_user_management_isolated_by_tenant(auth_client, db_session) -> None:
    other_tenant = Tenant(name="Tenant B de prueba (usuarios)")
    db_session.add(other_tenant)
    db_session.flush()

    foreign_user = User(
        tenant_id=other_tenant.id,
        email="foraneo@luisiacontable-tests.dev",
        full_name="Usuario de otro tenant",
        password_hash=hash_password("TestPassword123!"),
        role="contador",
        is_active=True,
    )
    db_session.add(foreign_user)
    db_session.flush()

    response = auth_client.patch(
        f"/api/v1/users/{foreign_user.id}/deactivate", json={"reason": "cruce indebido"}
    )
    assert response.status_code == 404


# ---- Revocacion de sesiones ----


def test_deactivating_user_revokes_their_active_session(auth_client, db_session, tenant) -> None:
    target_email = f"objetivo-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev"
    created = auth_client.post(
        "/api/v1/users",
        json=_create_user_payload(email=target_email, role="contador"),
    ).json()

    target_client = TestClient(app)
    login_response = target_client.post(
        "/api/v1/auth/login",
        json={"email": target_email, "password": "PasswordTemporal123"},
    )
    assert login_response.status_code == 200

    still_valid = target_client.get("/api/v1/auth/me")
    assert still_valid.status_code == 200

    deactivate_response = auth_client.patch(
        f"/api/v1/users/{created['id']}/deactivate", json={"reason": "revocacion de prueba"}
    )
    assert deactivate_response.status_code == 200

    after_deactivation = target_client.get("/api/v1/auth/me")
    assert after_deactivation.status_code == 401


def test_reset_password_revokes_active_session(auth_client, tenant) -> None:
    target_email = f"resetobjetivo-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev"
    created = auth_client.post(
        "/api/v1/users",
        json=_create_user_payload(email=target_email, role="asistente"),
    ).json()

    target_client = TestClient(app)
    login_response = target_client.post(
        "/api/v1/auth/login",
        json={"email": target_email, "password": "PasswordTemporal123"},
    )
    assert login_response.status_code == 200
    assert target_client.get("/api/v1/auth/me").status_code == 200

    reset_response = auth_client.patch(
        f"/api/v1/users/{created['id']}/reset-password",
        json={"new_password": "OtraPasswordTemporal789", "reason": "reset de prueba"},
    )
    assert reset_response.status_code == 200

    assert target_client.get("/api/v1/auth/me").status_code == 401


# ---- Invariante de ultimo-admin: caso simple de un solo hilo (sin concurrencia) ----


def test_deactivating_the_only_active_admin_is_blocked_single_threaded(db_session, tenant) -> None:
    solo_admin = User(
        tenant_id=tenant.id,
        email="solo-admin@luisiacontable-tests.dev",
        full_name="Unico admin del tenant",
        password_hash=hash_password("TestPassword123!"),
        role="admin",
        is_active=True,
    )
    unrelated_actor = User(
        tenant_id=tenant.id,
        email="actor-sin-permiso@luisiacontable-tests.dev",
        full_name="Actor de prueba (solo para invocar el servicio)",
        password_hash=hash_password("TestPassword123!"),
        role="contador",
        is_active=True,
    )
    db_session.add_all([solo_admin, unrelated_actor])
    db_session.flush()

    try:
        user_service.deactivate_user(
            db_session,
            tenant.id,
            unrelated_actor,
            solo_admin.id,
            now=clock.utcnow(),
            reason="intento de dejar el tenant sin admin",
        )
        raised = False
    except LastActiveAdminError:
        raised = True
    assert raised


def test_demoting_the_only_active_admin_is_blocked_single_threaded(db_session, tenant) -> None:
    solo_admin = User(
        tenant_id=tenant.id,
        email="solo-admin-2@luisiacontable-tests.dev",
        full_name="Unico admin del tenant (rol)",
        password_hash=hash_password("TestPassword123!"),
        role="admin",
        is_active=True,
    )
    unrelated_actor = User(
        tenant_id=tenant.id,
        email="actor-sin-permiso-2@luisiacontable-tests.dev",
        full_name="Actor de prueba (solo para invocar el servicio)",
        password_hash=hash_password("TestPassword123!"),
        role="contador",
        is_active=True,
    )
    db_session.add_all([solo_admin, unrelated_actor])
    db_session.flush()

    try:
        user_service.change_role(
            db_session,
            tenant.id,
            unrelated_actor,
            solo_admin.id,
            new_role="contador",
            reason="intento de degradar al unico admin",
        )
        raised = False
    except LastActiveAdminError:
        raised = True
    assert raised


def test_deactivating_admin_when_another_admin_remains_is_allowed_via_api(
    auth_client, db_session, tenant
) -> None:
    second_admin_payload = _create_user_payload(role="admin")
    second_admin = auth_client.post("/api/v1/users", json=second_admin_payload).json()

    response = auth_client.patch(
        f"/api/v1/users/{second_admin['id']}/deactivate", json={"reason": "reduccion de administradores"}
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False
