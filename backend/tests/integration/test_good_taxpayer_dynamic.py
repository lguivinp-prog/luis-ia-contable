from app.services.ruc_validator import compute_check_digit


def _fake_ruc(prefix: str = "20", base8: str = "77778888") -> str:
    first_ten = prefix + base8
    return first_ten + str(compute_check_digit(first_ten))


def _client_payload(**overrides) -> dict:
    payload = {
        "ruc": _fake_ruc(),
        "business_name": "EMPRESA BUEN CONTRIBUYENTE S.A.C.",
        "person_type": "juridica",
        "ruc_status": "ACTIVO",
        "fiscal_address_condition": "HABIDO",
        "tax_regime": "GENERAL",
    }
    payload.update(overrides)
    return payload


def _create_catalog_item(auth_client, code: str = "BUEN_CONTRIBUYENTE") -> dict:
    response = auth_client.post(
        "/api/v1/tax-categories/catalog", json={"code": code, "name": code.title(), "description": None}
    )
    assert response.status_code == 201
    return response.json()


def test_is_good_taxpayer_false_by_default(auth_client) -> None:
    created = auth_client.post("/api/v1/clients", json=_client_payload()).json()
    assert created["is_good_taxpayer"] is False


def test_is_good_taxpayer_true_when_active_category_assigned(auth_client) -> None:
    created = auth_client.post("/api/v1/clients", json=_client_payload()).json()
    _create_catalog_item(auth_client)

    auth_client.post(
        f"/api/v1/clients/{created['id']}/tax-categories",
        json={"category_code": "BUEN_CONTRIBUYENTE", "valid_from": "2020-01-01", "valid_until": None},
    )

    response = auth_client.get(f"/api/v1/clients/{created['id']}")
    assert response.json()["is_good_taxpayer"] is True


def test_is_good_taxpayer_false_after_validity_expired(auth_client) -> None:
    created = auth_client.post("/api/v1/clients", json=_client_payload()).json()
    _create_catalog_item(auth_client)

    auth_client.post(
        f"/api/v1/clients/{created['id']}/tax-categories",
        json={"category_code": "BUEN_CONTRIBUYENTE", "valid_from": "2000-01-01", "valid_until": "2001-01-01"},
    )

    response = auth_client.get(f"/api/v1/clients/{created['id']}")
    assert response.json()["is_good_taxpayer"] is False


def test_client_create_rejects_is_good_taxpayer_field(auth_client) -> None:
    payload = _client_payload(is_good_taxpayer=True)
    response = auth_client.post("/api/v1/clients", json=payload)
    assert response.status_code == 422


def test_client_update_rejects_is_good_taxpayer_field(auth_client) -> None:
    created = auth_client.post("/api/v1/clients", json=_client_payload()).json()
    payload = _client_payload(ruc=created["ruc"], is_good_taxpayer=True)
    response = auth_client.put(f"/api/v1/clients/{created['id']}", json=payload)
    assert response.status_code == 422


def test_list_clients_computes_good_taxpayer_per_row_without_n_plus_one(auth_client) -> None:
    plain = auth_client.post("/api/v1/clients", json=_client_payload()).json()
    good = auth_client.post(
        "/api/v1/clients", json=_client_payload(ruc=_fake_ruc(base8="88889999"))
    ).json()
    _create_catalog_item(auth_client)
    auth_client.post(
        f"/api/v1/clients/{good['id']}/tax-categories",
        json={"category_code": "BUEN_CONTRIBUYENTE", "valid_from": "2020-01-01", "valid_until": None},
    )

    listing = auth_client.get("/api/v1/clients").json()["items"]
    by_id = {item["id"]: item for item in listing}
    assert by_id[plain["id"]]["is_good_taxpayer"] is False
    assert by_id[good["id"]]["is_good_taxpayer"] is True
