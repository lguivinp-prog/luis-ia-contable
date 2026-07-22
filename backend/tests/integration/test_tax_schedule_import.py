from app.core.config import get_settings

VALID_CSV = (
    "tax_period,applicable_last_digits,required_category_code,priority,"
    "due_date_original,due_date_effective,due_date_type,notes\n"
    "2026-01,0;1;2;3;4,,,2026-02-12,2026-02-12,REGULAR,\n"
    "2026-01,5;6;7;8;9,,,2026-02-13,2026-02-13,REGULAR,\n"
)


def _create_source(auth_client, obligation_catalog, name: str = "Cronograma import") -> dict:
    response = auth_client.post(
        "/api/v1/tax-schedules/sources",
        json={"obligation_id": str(obligation_catalog["621"].id), "name": name, "description": None},
    )
    assert response.status_code == 201
    return response.json()


def _upload(auth_client, source_id: str, csv_text: str, *, fiscal_year: int = 2026):
    return auth_client.post(
        f"/api/v1/tax-schedules/sources/{source_id}/import-runs",
        files={"file": ("cronograma.csv", csv_text, "text/csv")},
        data={"fiscal_year": str(fiscal_year), "schedule_type": "GENERAL"},
    )


def test_import_valid_csv_is_validated_with_no_previous_diff(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    response = _upload(auth_client, source["id"], VALID_CSV)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "VALIDATED"
    assert body["row_count"] == 2
    assert body["validation_errors"] is None
    assert body["diff_summary"]["has_previous"] is False


def test_import_missing_headers_is_rejected(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    bad_csv = "tax_period,applicable_last_digits\n2026-01,0\n"
    response = _upload(auth_client, source["id"], bad_csv)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "REJECTED"
    assert body["validation_errors"]
    assert "Encabezados faltantes" in body["validation_errors"][0]["message"]


def test_import_invalid_date_flags_row_error(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    bad_csv = (
        "tax_period,applicable_last_digits,required_category_code,priority,"
        "due_date_original,due_date_effective,due_date_type,notes\n"
        "2026-01,0;1,,,12-02-2026,2026-02-12,REGULAR,\n"
    )
    response = _upload(auth_client, source["id"], bad_csv)
    body = response.json()
    assert body["status"] == "REJECTED"
    assert "Fecha inválida" in body["validation_errors"][0]["message"]


def test_import_invalid_digits_flags_row_error(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    bad_csv = (
        "tax_period,applicable_last_digits,required_category_code,priority,"
        "due_date_original,due_date_effective,due_date_type,notes\n"
        "2026-01,x;y,,,2026-02-12,2026-02-12,REGULAR,\n"
    )
    response = _upload(auth_client, source["id"], bad_csv)
    body = response.json()
    assert body["status"] == "REJECTED"
    assert "Dígitos inválidos" in body["validation_errors"][0]["message"]


def test_import_duplicate_rows_flagged(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    dup_csv = (
        "tax_period,applicable_last_digits,required_category_code,priority,"
        "due_date_original,due_date_effective,due_date_type,notes\n"
        "2026-01,0;1,,,2026-02-12,2026-02-12,REGULAR,\n"
        "2026-01,0;1,,,2026-02-13,2026-02-13,REGULAR,\n"
    )
    response = _upload(auth_client, source["id"], dup_csv)
    body = response.json()
    assert body["status"] == "REJECTED"
    assert "duplicada" in body["validation_errors"][0]["message"]


def test_import_unknown_category_code_flagged(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    bad_csv = (
        "tax_period,applicable_last_digits,required_category_code,priority,"
        "due_date_original,due_date_effective,due_date_type,notes\n"
        "2026-01,0;1,NO_EXISTE,,2026-02-12,2026-02-12,REGULAR,\n"
    )
    response = _upload(auth_client, source["id"], bad_csv)
    body = response.json()
    assert body["status"] == "REJECTED"
    assert "categoría desconocido" in body["validation_errors"][0]["message"]


def test_import_rejects_non_csv_extension(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    response = auth_client.post(
        f"/api/v1/tax-schedules/sources/{source['id']}/import-runs",
        files={"file": ("cronograma.txt", VALID_CSV, "text/plain")},
        data={"fiscal_year": "2026", "schedule_type": "GENERAL"},
    )
    assert response.status_code == 422


def test_import_rejects_oversized_file(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    settings = get_settings()
    original_max_bytes = settings.tax_schedule_csv_max_bytes
    settings.tax_schedule_csv_max_bytes = 10
    try:
        response = _upload(auth_client, source["id"], VALID_CSV)
        assert response.status_code == 422
    finally:
        settings.tax_schedule_csv_max_bytes = original_max_bytes


def test_confirm_import_creates_version_with_entries(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    import_run = _upload(auth_client, source["id"], VALID_CSV).json()

    confirm = auth_client.post(
        f"/api/v1/tax-schedules/import-runs/{import_run['id']}/confirm",
        json={"version_label": "v1", "normative_reference": "R.S. Demo", "source_document_url": None},
    )
    assert confirm.status_code == 200
    version = confirm.json()
    assert version["status"] == "IMPORTED"

    entries = auth_client.get(f"/api/v1/tax-schedules/versions/{version['id']}/entries").json()
    assert len(entries) == 2


def test_confirm_already_confirmed_import_run_fails(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    import_run = _upload(auth_client, source["id"], VALID_CSV).json()
    auth_client.post(
        f"/api/v1/tax-schedules/import-runs/{import_run['id']}/confirm",
        json={"version_label": "v1", "normative_reference": None, "source_document_url": None},
    )

    second_confirm = auth_client.post(
        f"/api/v1/tax-schedules/import-runs/{import_run['id']}/confirm",
        json={"version_label": "v2", "normative_reference": None, "source_document_url": None},
    )
    assert second_confirm.status_code == 409


def test_reject_import_run_marks_rejected(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    import_run = _upload(auth_client, source["id"], VALID_CSV).json()

    reject = auth_client.post(f"/api/v1/tax-schedules/import-runs/{import_run['id']}/reject")
    assert reject.status_code == 200

    preview = auth_client.get(f"/api/v1/tax-schedules/import-runs/{import_run['id']}").json()
    assert preview["status"] == "REJECTED"


def test_import_diff_summary_against_previous_approved_version(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)

    first_run = _upload(auth_client, source["id"], VALID_CSV).json()
    first_version = auth_client.post(
        f"/api/v1/tax-schedules/import-runs/{first_run['id']}/confirm",
        json={"version_label": "v1", "normative_reference": None, "source_document_url": None},
    ).json()
    auth_client.post(
        f"/api/v1/tax-schedules/versions/{first_version['id']}/submit-for-review", json={"comment": None}
    )
    auth_client.post(
        f"/api/v1/tax-schedules/versions/{first_version['id']}/approve", json={"comment": "Aprobado"}
    )

    changed_csv = (
        "tax_period,applicable_last_digits,required_category_code,priority,"
        "due_date_original,due_date_effective,due_date_type,notes\n"
        "2026-01,0;1;2;3;4,,,2026-02-12,2026-02-20,REGULAR,\n"
        "2026-02,5;6;7;8;9,,,2026-03-13,2026-03-13,REGULAR,\n"
    )
    second_run = _upload(auth_client, source["id"], changed_csv).json()
    diff = second_run["diff_summary"]
    assert diff["has_previous"] is True
    assert diff["previous_version_label"] == "v1"
    assert len(diff["added"]) == 1
    assert len(diff["removed"]) == 1
    assert len(diff["modified_dates"]) == 1
