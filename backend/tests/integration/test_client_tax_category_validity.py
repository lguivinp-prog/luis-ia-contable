from app.services.ruc_validator import compute_check_digit


def _fake_ruc(prefix: str = "20", base8: str = "55556666") -> str:
    first_ten = prefix + base8
    return first_ten + str(compute_check_digit(first_ten))


def _create_client(auth_client) -> dict:
    payload = {
        "ruc": _fake_ruc(),
        "business_name": "EMPRESA CATEGORIAS S.A.C.",
        "person_type": "juridica",
        "ruc_status": "ACTIVO",
        "fiscal_address_condition": "HABIDO",
        "tax_regime": "GENERAL",
    }
    return auth_client.post("/api/v1/clients", json=payload).json()


def _create_catalog_item(auth_client, code: str = "BUEN_CONTRIBUYENTE") -> dict:
    response = auth_client.post(
        "/api/v1/tax-categories/catalog", json={"code": code, "name": code.title(), "description": None}
    )
    assert response.status_code == 201
    return response.json()


def test_assign_category_success(auth_client) -> None:
    client_data = _create_client(auth_client)
    _create_catalog_item(auth_client)

    response = auth_client.post(
        f"/api/v1/clients/{client_data['id']}/tax-categories",
        json={"category_code": "BUEN_CONTRIBUYENTE", "valid_from": "2026-01-01", "valid_until": None},
    )
    assert response.status_code == 201
    assert response.json()["category_code"] == "BUEN_CONTRIBUYENTE"


def test_assign_unknown_category_code_returns_404(auth_client) -> None:
    client_data = _create_client(auth_client)

    response = auth_client.post(
        f"/api/v1/clients/{client_data['id']}/tax-categories",
        json={"category_code": "NO_EXISTE", "valid_from": "2026-01-01", "valid_until": None},
    )
    assert response.status_code == 404


def test_overlapping_validity_is_rejected(auth_client) -> None:
    client_data = _create_client(auth_client)
    _create_catalog_item(auth_client)

    first = auth_client.post(
        f"/api/v1/clients/{client_data['id']}/tax-categories",
        json={"category_code": "BUEN_CONTRIBUYENTE", "valid_from": "2026-01-01", "valid_until": "2026-12-31"},
    )
    assert first.status_code == 201

    overlapping = auth_client.post(
        f"/api/v1/clients/{client_data['id']}/tax-categories",
        json={"category_code": "BUEN_CONTRIBUYENTE", "valid_from": "2026-06-01", "valid_until": None},
    )
    assert overlapping.status_code == 409


def test_non_overlapping_renewal_is_allowed(auth_client) -> None:
    client_data = _create_client(auth_client)
    _create_catalog_item(auth_client)

    first = auth_client.post(
        f"/api/v1/clients/{client_data['id']}/tax-categories",
        json={"category_code": "BUEN_CONTRIBUYENTE", "valid_from": "2025-01-01", "valid_until": "2025-12-31"},
    )
    assert first.status_code == 201

    renewal = auth_client.post(
        f"/api/v1/clients/{client_data['id']}/tax-categories",
        json={"category_code": "BUEN_CONTRIBUYENTE", "valid_from": "2026-01-01", "valid_until": None},
    )
    assert renewal.status_code == 201

    listing = auth_client.get(f"/api/v1/clients/{client_data['id']}/tax-categories")
    assert len(listing.json()) == 2


def test_remove_category_assignment(auth_client) -> None:
    client_data = _create_client(auth_client)
    _create_catalog_item(auth_client)

    assignment = auth_client.post(
        f"/api/v1/clients/{client_data['id']}/tax-categories",
        json={"category_code": "BUEN_CONTRIBUYENTE", "valid_from": "2026-01-01", "valid_until": None},
    ).json()

    delete_response = auth_client.delete(
        f"/api/v1/clients/{client_data['id']}/tax-categories/{assignment['id']}"
    )
    assert delete_response.status_code == 204

    listing = auth_client.get(f"/api/v1/clients/{client_data['id']}/tax-categories")
    assert listing.json() == []
