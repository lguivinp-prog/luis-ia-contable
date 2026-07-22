DEFAULT_CSV = (
    "tax_period,applicable_last_digits,required_category_code,priority,"
    "due_date_original,due_date_effective,due_date_type,notes\n"
    "2026-01,0;1;2;3;4,,,2026-02-12,2026-02-12,REGULAR,\n"
    "2026-01,5;6;7;8;9,,,2026-02-13,2026-02-13,REGULAR,\n"
)


def _create_source(auth_client, obligation_catalog, name: str = "Cronograma IGV") -> dict:
    response = auth_client.post(
        "/api/v1/tax-schedules/sources",
        json={"obligation_id": str(obligation_catalog["621"].id), "name": name, "description": None},
    )
    assert response.status_code == 201
    return response.json()


def _import_and_confirm(
    auth_client, source_id: str, *, version_label: str, fiscal_year: int = 2026, csv_text: str = DEFAULT_CSV
) -> dict:
    import_response = auth_client.post(
        f"/api/v1/tax-schedules/sources/{source_id}/import-runs",
        files={"file": ("cronograma.csv", csv_text, "text/csv")},
        data={"fiscal_year": str(fiscal_year), "schedule_type": "GENERAL"},
    )
    assert import_response.status_code == 201
    import_run = import_response.json()
    assert import_run["status"] == "VALIDATED"

    confirm_response = auth_client.post(
        f"/api/v1/tax-schedules/import-runs/{import_run['id']}/confirm",
        json={"version_label": version_label, "normative_reference": None, "source_document_url": None},
    )
    assert confirm_response.status_code == 200
    return confirm_response.json()


def _submit(auth_client, version_id: str, comment: str | None = None):
    return auth_client.post(
        f"/api/v1/tax-schedules/versions/{version_id}/submit-for-review", json={"comment": comment}
    )


def _approve(auth_client, version_id: str, comment: str):
    return auth_client.post(
        f"/api/v1/tax-schedules/versions/{version_id}/approve", json={"comment": comment}
    )


def test_submit_for_review_from_imported(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    version = _import_and_confirm(auth_client, source["id"], version_label="v1")
    assert version["status"] == "IMPORTED"

    response = _submit(auth_client, version["id"])
    assert response.status_code == 200
    assert response.json()["status"] == "PENDING_REVIEW"


def test_approve_requires_non_blank_comment(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    version = _import_and_confirm(auth_client, source["id"], version_label="v1")
    _submit(auth_client, version["id"])

    response = _approve(auth_client, version["id"], " ")
    assert response.status_code == 422


def test_approve_success_creates_history_entry(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    version = _import_and_confirm(auth_client, source["id"], version_label="v1")
    _submit(auth_client, version["id"])

    response = _approve(auth_client, version["id"], "Aprobado")
    assert response.status_code == 200
    assert response.json()["status"] == "APPROVED"

    history = auth_client.get(f"/api/v1/tax-schedules/versions/{version['id']}/approval-history").json()
    assert any(h["to_status"] == "APPROVED" and h["comment"] == "Aprobado" for h in history)


def test_reject_requires_non_blank_reason(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    version = _import_and_confirm(auth_client, source["id"], version_label="v1")
    _submit(auth_client, version["id"])

    response = auth_client.post(
        f"/api/v1/tax-schedules/versions/{version['id']}/reject", json={"reason": ""}
    )
    assert response.status_code == 422


def test_reject_from_pending_review(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    version = _import_and_confirm(auth_client, source["id"], version_label="v1")
    _submit(auth_client, version["id"])

    response = auth_client.post(
        f"/api/v1/tax-schedules/versions/{version['id']}/reject", json={"reason": "Datos incorrectos"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"


def test_cancel_from_draft_imported(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    version = _import_and_confirm(auth_client, source["id"], version_label="v1")

    response = auth_client.post(
        f"/api/v1/tax-schedules/versions/{version['id']}/cancel", json={"reason": "Version de prueba"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"


def test_invalid_transition_is_rejected(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    version = _import_and_confirm(auth_client, source["id"], version_label="v1")

    # PENDING_REVIEW no es una transicion valida desde CANCELLED.
    auth_client.post(f"/api/v1/tax-schedules/versions/{version['id']}/cancel", json={"reason": "Cancelada"})
    response = _submit(auth_client, version["id"])
    assert response.status_code == 409


def test_approved_version_is_immutable(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)
    version = _import_and_confirm(auth_client, source["id"], version_label="v1")
    _submit(auth_client, version["id"])
    _approve(auth_client, version["id"], "Aprobado")

    response = _submit(auth_client, version["id"])
    assert response.status_code == 409


def test_approving_new_version_supersedes_previous_same_scope(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)

    version_a = _import_and_confirm(auth_client, source["id"], version_label="v1")
    _submit(auth_client, version_a["id"])
    _approve(auth_client, version_a["id"], "Aprobado v1")

    version_b = _import_and_confirm(auth_client, source["id"], version_label="v2")
    _submit(auth_client, version_b["id"])
    approve_b = _approve(auth_client, version_b["id"], "Aprobado v2")
    assert approve_b.status_code == 200
    assert approve_b.json()["status"] == "APPROVED"

    version_a_after = auth_client.get(f"/api/v1/tax-schedules/versions/{version_a['id']}").json()
    assert version_a_after["status"] == "SUPERSEDED"
    assert version_a_after["superseded_by_version_id"] == version_b["id"]

    history_a = auth_client.get(f"/api/v1/tax-schedules/versions/{version_a['id']}/approval-history").json()
    assert any(h["to_status"] == "SUPERSEDED" for h in history_a)


def test_different_fiscal_years_do_not_supersede_each_other(auth_client, obligation_catalog) -> None:
    source = _create_source(auth_client, obligation_catalog)

    version_2026 = _import_and_confirm(auth_client, source["id"], version_label="v-2026", fiscal_year=2026)
    _submit(auth_client, version_2026["id"])
    _approve(auth_client, version_2026["id"], "Aprobado")

    version_2027 = _import_and_confirm(auth_client, source["id"], version_label="v-2027", fiscal_year=2027)
    _submit(auth_client, version_2027["id"])
    _approve(auth_client, version_2027["id"], "Aprobado")

    version_2026_after = auth_client.get(f"/api/v1/tax-schedules/versions/{version_2026['id']}").json()
    assert version_2026_after["status"] == "APPROVED"
