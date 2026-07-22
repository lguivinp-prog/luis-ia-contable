from app.services.ruc_validator import compute_check_digit

CSV_TEMPLATE = (
    "tax_period,applicable_last_digits,required_category_code,priority,"
    "due_date_original,due_date_effective,due_date_type,notes\n"
    "2026-01,{digits_a},,,2026-02-12,{due_a},REGULAR,\n"
    "2026-01,{digits_b},,,2026-02-13,2026-02-13,REGULAR,\n"
)


def _fake_ruc(prefix: str = "20", base8: str = "44445555") -> str:
    first_ten = prefix + base8
    return first_ten + str(compute_check_digit(first_ten))


def _create_source(auth_client, obligation_catalog, name: str = "Cronograma misc") -> dict:
    response = auth_client.post(
        "/api/v1/tax-schedules/sources",
        json={"obligation_id": str(obligation_catalog["621"].id), "name": name, "description": None},
    )
    assert response.status_code == 201
    return response.json()


def _import_and_confirm(auth_client, source_id: str, *, version_label: str, csv_text: str) -> dict:
    import_run = auth_client.post(
        f"/api/v1/tax-schedules/sources/{source_id}/import-runs",
        files={"file": ("cronograma.csv", csv_text, "text/csv")},
        data={"fiscal_year": "2026", "schedule_type": "GENERAL"},
    ).json()
    return auth_client.post(
        f"/api/v1/tax-schedules/import-runs/{import_run['id']}/confirm",
        json={"version_label": version_label, "normative_reference": None, "source_document_url": None},
    ).json()


def test_get_source_not_found_returns_404(auth_client) -> None:
    response = auth_client.get("/api/v1/tax-schedules/sources/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_get_version_not_found_returns_404(auth_client) -> None:
    response = auth_client.get("/api/v1/tax-schedules/versions/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_export_version_entries_csv(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    csv_text = CSV_TEMPLATE.format(digits_a="0;1", digits_b="2;3", due_a="2026-02-12")
    version = _import_and_confirm(auth_client, source["id"], version_label="v1", csv_text=csv_text)

    response = auth_client.get(f"/api/v1/tax-schedules/versions/{version['id']}/entries.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    body = response.text
    assert "tax_period,applicable_last_digits" in body
    assert "2026-01" in body


def test_compare_versions_reports_added_removed_modified(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    csv_v1 = CSV_TEMPLATE.format(digits_a="0;1", digits_b="2;3", due_a="2026-02-12")
    version_a = _import_and_confirm(auth_client, source["id"], version_label="v1", csv_text=csv_v1)

    csv_v2 = CSV_TEMPLATE.format(digits_a="0;1", digits_b="4;5", due_a="2026-02-20")
    version_b = _import_and_confirm(auth_client, source["id"], version_label="v2", csv_text=csv_v2)

    response = auth_client.get(
        "/api/v1/tax-schedules/versions/compare",
        params={"version_a": version_a["id"], "version_b": version_b["id"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["version_a"] == "v1"
    assert body["version_b"] == "v2"
    assert len(body["added"]) == 1
    assert len(body["removed"]) == 1
    assert len(body["modified"]) == 1


def test_approval_history_empty_for_fresh_version(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    csv_text = CSV_TEMPLATE.format(digits_a="0;1", digits_b="2;3", due_a="2026-02-12")
    version = _import_and_confirm(auth_client, source["id"], version_label="v1", csv_text=csv_text)

    response = auth_client.get(f"/api/v1/tax-schedules/versions/{version['id']}/approval-history")
    assert response.status_code == 200
    assert response.json() == []


def test_preview_due_date_unknown_client_returns_404(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    response = auth_client.get(
        "/api/v1/tax-schedules/preview-due-date",
        params={
            "source_id": source["id"],
            "client_id": "00000000-0000-0000-0000-000000000000",
            "tax_period": "2026-01",
        },
    )
    assert response.status_code == 404


def test_preview_due_date_no_approved_version_returns_404(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    client_data = auth_client.post(
        "/api/v1/clients",
        json={
            "ruc": _fake_ruc(),
            "business_name": "EMPRESA PREVIEW S.A.C.",
            "person_type": "juridica",
            "ruc_status": "ACTIVO",
            "fiscal_address_condition": "HABIDO",
            "tax_regime": "GENERAL",
        },
    ).json()

    response = auth_client.get(
        "/api/v1/tax-schedules/preview-due-date",
        params={"source_id": source["id"], "client_id": client_data["id"], "tax_period": "2026-01"},
    )
    assert response.status_code == 404


def test_preview_due_date_success_after_approval(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    client_data = auth_client.post(
        "/api/v1/clients",
        json={
            "ruc": _fake_ruc(base8="44446666"),
            "business_name": "EMPRESA PREVIEW OK S.A.C.",
            "person_type": "juridica",
            "ruc_status": "ACTIVO",
            "fiscal_address_condition": "HABIDO",
            "tax_regime": "GENERAL",
        },
    ).json()
    last_digit = int(client_data["ruc"][10])
    other_digit = (last_digit + 1) % 10

    csv_text = CSV_TEMPLATE.format(digits_a=str(last_digit), digits_b=str(other_digit), due_a="2026-02-12")
    version = _import_and_confirm(auth_client, source["id"], version_label="v1", csv_text=csv_text)
    auth_client.post(
        f"/api/v1/tax-schedules/versions/{version['id']}/submit-for-review", json={"comment": None}
    )
    auth_client.post(f"/api/v1/tax-schedules/versions/{version['id']}/approve", json={"comment": "Aprobado"})

    response = auth_client.get(
        "/api/v1/tax-schedules/preview-due-date",
        params={"source_id": source["id"], "client_id": client_data["id"], "tax_period": "2026-01"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["due_date_effective"] == "2026-02-12"
    assert "DATOS DE DEMOSTRACIÓN" in body["demo_disclaimer"]
