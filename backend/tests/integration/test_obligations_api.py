from app.services.ruc_validator import compute_check_digit


def _fake_ruc(prefix: str = "20", base8: str = "33334444") -> str:
    first_ten = prefix + base8
    return first_ten + str(compute_check_digit(first_ten))


def _create_client(auth_client) -> dict:
    payload = {
        "ruc": _fake_ruc(),
        "business_name": "EMPRESA CON OBLIGACIONES S.A.C.",
        "person_type": "juridica",
        "ruc_status": "ACTIVO",
        "fiscal_address_condition": "HABIDO",
        "tax_regime": "GENERAL",
    }
    return auth_client.post("/api/v1/clients", json=payload).json()


def test_catalog_lists_seeded_obligations(auth_client, obligation_catalog) -> None:
    response = auth_client.get("/api/v1/obligations/catalog")
    assert response.status_code == 200
    codes = {item["code"] for item in response.json()}
    assert "621" in codes
    assert "PLAME" in codes


def test_assign_obligation_to_client(auth_client, obligation_catalog) -> None:
    client_data = _create_client(auth_client)
    obligation = obligation_catalog["621"]

    response = auth_client.post(
        f"/api/v1/clients/{client_data['id']}/obligations",
        json={"obligation_id": str(obligation.id)},
    )
    assert response.status_code == 201
    assert response.json()["obligation_id"] == str(obligation.id)

    listing = auth_client.get(f"/api/v1/clients/{client_data['id']}/obligations")
    assert listing.status_code == 200
    assert len(listing.json()) == 1


def test_assign_duplicate_obligation_is_rejected(auth_client, obligation_catalog) -> None:
    client_data = _create_client(auth_client)
    obligation = obligation_catalog["621"]

    first = auth_client.post(
        f"/api/v1/clients/{client_data['id']}/obligations",
        json={"obligation_id": str(obligation.id)},
    )
    assert first.status_code == 201

    second = auth_client.post(
        f"/api/v1/clients/{client_data['id']}/obligations",
        json={"obligation_id": str(obligation.id)},
    )
    assert second.status_code == 409


def test_deactivate_client_obligation(auth_client, obligation_catalog) -> None:
    client_data = _create_client(auth_client)
    obligation = obligation_catalog["PLAME"]

    assign_response = auth_client.post(
        f"/api/v1/clients/{client_data['id']}/obligations",
        json={"obligation_id": str(obligation.id)},
    )
    obligation_id = assign_response.json()["obligation_id"]

    patch_response = auth_client.patch(
        f"/api/v1/clients/{client_data['id']}/obligations/{obligation_id}",
        json={"is_active": False},
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["is_active"] is False


def test_remove_client_obligation(auth_client, obligation_catalog) -> None:
    client_data = _create_client(auth_client)
    obligation = obligation_catalog["ITAN"]

    assign_response = auth_client.post(
        f"/api/v1/clients/{client_data['id']}/obligations",
        json={"obligation_id": str(obligation.id)},
    )
    obligation_id = assign_response.json()["obligation_id"]

    delete_response = auth_client.delete(
        f"/api/v1/clients/{client_data['id']}/obligations/{obligation_id}"
    )
    assert delete_response.status_code == 204

    listing = auth_client.get(f"/api/v1/clients/{client_data['id']}/obligations")
    assert listing.json() == []
