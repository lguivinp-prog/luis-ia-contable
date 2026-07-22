from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.services.ruc_validator import compute_check_digit


def _fake_ruc(prefix: str = "20", base8: str = "55556666") -> str:
    first_ten = prefix + base8
    return first_ten + str(compute_check_digit(first_ten))


def _create_client(auth_client, ruc: str | None = None) -> dict:
    payload = {
        "ruc": ruc or _fake_ruc(),
        "business_name": "EMPRESA AUDITADA S.A.C.",
        "person_type": "juridica",
        "ruc_status": "ACTIVO",
        "fiscal_address_condition": "HABIDO",
        "tax_regime": "GENERAL",
    }
    return auth_client.post("/api/v1/clients", json=payload).json()


def _actions(db_session) -> list[str]:
    return [
        row.action
        for row in db_session.execute(select(AuditLog).order_by(AuditLog.created_at)).scalars()
    ]


def test_client_create_is_audited(auth_client, db_session) -> None:
    _create_client(auth_client)
    assert "CLIENT_CREATE" in _actions(db_session)


def test_ruc_lookup_is_audited(auth_client, db_session) -> None:
    ruc = _fake_ruc()
    response = auth_client.get(f"/api/v1/clients/ruc-lookup/{ruc}")
    assert response.status_code == 200
    assert response.json()["is_simulated"] is True

    logs = db_session.execute(
        select(AuditLog).where(AuditLog.action == "RUC_LOOKUP_QUERY")
    ).scalars().all()
    assert len(logs) == 1
    assert logs[0].log_metadata["ruc"] == ruc


def test_obligation_assignment_lifecycle_is_audited(auth_client, db_session, obligation_catalog) -> None:
    client_data = _create_client(auth_client)
    obligation = obligation_catalog["621"]

    assign_response = auth_client.post(
        f"/api/v1/clients/{client_data['id']}/obligations",
        json={"obligation_id": str(obligation.id)},
    )
    obligation_link_id = assign_response.json()["obligation_id"]

    auth_client.patch(
        f"/api/v1/clients/{client_data['id']}/obligations/{obligation_link_id}",
        json={"is_active": False},
    )
    auth_client.delete(f"/api/v1/clients/{client_data['id']}/obligations/{obligation_link_id}")

    actions = _actions(db_session)
    assert "CLIENT_OBLIGATION_ASSIGN" in actions
    assert "CLIENT_OBLIGATION_UPDATE" in actions
    assert "CLIENT_OBLIGATION_REMOVE" in actions


def test_login_and_logout_are_audited(client, admin_user, db_session) -> None:
    client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "TestPassword123!"},
    )
    client.post("/api/v1/auth/logout")

    actions = _actions(db_session)
    assert "LOGIN_SUCCESS" in actions
    assert "LOGOUT" in actions
