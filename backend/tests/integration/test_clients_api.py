from sqlalchemy import select

from app.models.audit_log import AuditLog
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
