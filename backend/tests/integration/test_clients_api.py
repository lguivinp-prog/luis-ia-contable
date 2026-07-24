from sqlalchemy import select

from app.core.security import hash_password
from app.models.audit_log import AuditLog
from app.models.client import Client as ClientModel
from app.models.tenant import Tenant
from app.models.user import User
from app.services.ruc_validator import compute_check_digit


def _fake_ruc(prefix: str = "20", base8: str = "11112222") -> str:
    first_ten = prefix + base8
    return first_ten + str(compute_check_digit(first_ten))


def _client_payload(**overrides) -> dict:
    payload = {
        "ruc": _fake_ruc(),
        "business_name": "EMPRESA DE PRUEBA S.A.C.",
        "person_type": "juridica",
        "ruc_status": "ACTIVO",
        "fiscal_address_condition": "HABIDO",
        "tax_regime": "GENERAL",
    }
    payload.update(overrides)
    return payload


def test_create_client_success(auth_client) -> None:
    response = auth_client.post("/api/v1/clients", json=_client_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["ruc"] == _client_payload()["ruc"]
    assert body["service_status"] == "prospecto"
    assert body["ruc_last_digit"] == int(_client_payload()["ruc"][10])


def test_create_client_rejects_invalid_check_digit(auth_client) -> None:
    ruc = _fake_ruc()
    tampered = ruc[:10] + ("0" if ruc[10] != "0" else "1")
    response = auth_client.post("/api/v1/clients", json=_client_payload(ruc=tampered))
    assert response.status_code == 422


def test_create_client_rejects_duplicate_ruc(auth_client) -> None:
    payload = _client_payload()
    first = auth_client.post("/api/v1/clients", json=payload)
    assert first.status_code == 201

    second = auth_client.post(
        "/api/v1/clients", json=_client_payload(ruc=payload["ruc"], business_name="OTRA RAZON SOCIAL")
    )
    assert second.status_code == 409


def test_list_clients_filters_by_service_status(auth_client) -> None:
    auth_client.post("/api/v1/clients", json=_client_payload())
    response = auth_client.get("/api/v1/clients", params={"service_status": "prospecto"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert all(item["service_status"] == "prospecto" for item in body["items"])


def test_get_client_detail(auth_client) -> None:
    created = auth_client.post("/api/v1/clients", json=_client_payload()).json()
    response = auth_client.get(f"/api/v1/clients/{created['id']}")
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_update_client(auth_client) -> None:
    created = auth_client.post("/api/v1/clients", json=_client_payload()).json()
    update_payload = _client_payload(ruc=created["ruc"], business_name="RAZON SOCIAL ACTUALIZADA")
    response = auth_client.put(f"/api/v1/clients/{created['id']}", json=update_payload)
    assert response.status_code == 200
    assert response.json()["business_name"] == "RAZON SOCIAL ACTUALIZADA"


def test_update_client_rejects_extra_fields(auth_client) -> None:
    # `ClientUpdate(ClientBase)` usa extra="forbid" deliberadamente (ver
    # ADR-0013 / bug de edicion de contribuyentes, Bloque 3 rama
    # fix/client-update-payload): un campo de solo lectura (id, tenant_id,
    # ruc_last_digit, service_status, last_verified_at, created_at,
    # updated_at, is_good_taxpayer) enviado en el body debe seguir
    # rechazandose con 422 -- esta prueba existe para que nadie debilite esa
    # politica con extra="ignore" solo para esconder un bug del frontend.
    created = auth_client.post("/api/v1/clients", json=_client_payload()).json()
    update_payload = _client_payload(ruc=created["ruc"])
    update_payload["id"] = created["id"]
    update_payload["tenant_id"] = "00000000-0000-0000-0000-000000000000"
    update_payload["service_status"] = "activo"
    update_payload["is_good_taxpayer"] = True

    response = auth_client.put(f"/api/v1/clients/{created['id']}", json=update_payload)
    assert response.status_code == 422
    rejected_fields = {item["loc"][-1] for item in response.json()["detail"]}
    assert rejected_fields == {"id", "tenant_id", "service_status", "is_good_taxpayer"}


def test_update_client_isolated_by_tenant(client, db_session) -> None:
    # El contribuyente de un tenant no debe ser editable (ni siquiera
    # visible) desde la sesion de otro tenant -- `client_service.get_client`
    # filtra por tenant_id antes de aplicar cualquier cambio.
    tenant_a = Tenant(name="Tenant A de prueba")
    tenant_b = Tenant(name="Tenant B de prueba")
    db_session.add_all([tenant_a, tenant_b])
    db_session.flush()

    admin_b = User(
        tenant_id=tenant_b.id,
        email="admin-b@luisiacontable-tests.dev",
        full_name="Admin B de prueba",
        password_hash=hash_password("TestPassword123!"),
        role="admin",
        is_active=True,
    )
    db_session.add(admin_b)
    db_session.flush()

    login_a = client.post(
        "/api/v1/auth/login",
        json={"email": "admin-b@luisiacontable-tests.dev", "password": "TestPassword123!"},
    )
    assert login_a.status_code == 200
    csrf_b = login_a.cookies.get("csrf_token")
    client.headers.update({"X-CSRF-Token": csrf_b})

    # El cliente de tenant_a se crea directamente vinculado a tenant_a
    # (fuera de la sesion de tenant_b) para simular que ya existia.
    client_a = ClientModel(
        tenant_id=tenant_a.id,
        ruc=_fake_ruc(base8="99990000"),
        business_name="EMPRESA DE OTRO TENANT S.A.C.",
        person_type="juridica",
        ruc_status="ACTIVO",
        fiscal_address_condition="HABIDO",
        tax_regime="GENERAL",
    )
    db_session.add(client_a)
    db_session.flush()

    response = client.put(
        f"/api/v1/clients/{client_a.id}",
        json=_client_payload(ruc=client_a.ruc, business_name="INTENTO DE EDICION CRUZADA"),
    )
    assert response.status_code == 404


def test_change_client_status_is_audited(auth_client, db_session) -> None:
    created = auth_client.post("/api/v1/clients", json=_client_payload()).json()
    response = auth_client.patch(
        f"/api/v1/clients/{created['id']}/status",
        json={"service_status": "activo", "note": "Contrato firmado"},
    )
    assert response.status_code == 200
    assert response.json()["service_status"] == "activo"

    logs = db_session.execute(
        select(AuditLog).where(AuditLog.action == "CLIENT_STATUS_CHANGE")
    ).scalars().all()
    assert len(logs) == 1
    assert logs[0].log_metadata["to"] == "activo"
