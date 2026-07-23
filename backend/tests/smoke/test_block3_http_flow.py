"""Flujo HTTP de extremo a extremo de Bloque 3.1: crear periodo, generar
declaraciones masivamente, confirmar, consultar tablero y detalle -- via la
API real (auth_client), no solo la capa de servicio."""

from app.services.ruc_validator import compute_check_digit


def _fake_ruc() -> str:
    import uuid

    first_ten = "20" + f"{uuid.uuid4().int % 10**8:08d}"
    return first_ten + str(compute_check_digit(first_ten))


def _make_client(auth_client) -> dict:
    payload = {
        "ruc": _fake_ruc(),
        "business_name": "EMPRESA BLOQUE3 S.A.C.",
        "person_type": "juridica",
        "ruc_status": "ACTIVO",
        "fiscal_address_condition": "HABIDO",
        "tax_regime": "GENERAL",
    }
    response = auth_client.post("/api/v1/clients", json=payload)
    assert response.status_code == 201
    client = response.json()

    activate_response = auth_client.patch(
        f"/api/v1/clients/{client['id']}/status",
        json={"service_status": "activo", "note": "Activacion smoke"},
    )
    assert activate_response.status_code == 200
    return activate_response.json()


def _assign_obligation(auth_client, client_id: str, obligation_id: str) -> None:
    response = auth_client.post(
        f"/api/v1/clients/{client_id}/obligations", json={"obligation_id": obligation_id}
    )
    assert response.status_code == 201


def _make_approved_schedule(auth_client, obligation_id: str) -> str:
    source = auth_client.post(
        "/api/v1/tax-schedules/sources",
        json={"obligation_id": obligation_id, "name": "Cronograma HTTP smoke", "description": None},
    ).json()

    csv_text = (
        "tax_period,applicable_last_digits,required_category_code,priority,"
        "due_date_original,due_date_effective,due_date_type,notes\n"
        "2026-01,0;1;2;3;4;5;6;7;8;9,,,2026-02-12,2026-02-12,REGULAR,\n"
    )
    import_response = auth_client.post(
        f"/api/v1/tax-schedules/sources/{source['id']}/import-runs",
        files={"file": ("cronograma.csv", csv_text, "text/csv")},
        data={"fiscal_year": "2026", "schedule_type": "GENERAL"},
    )
    assert import_response.status_code == 201
    import_run = import_response.json()

    confirm_response = auth_client.post(
        f"/api/v1/tax-schedules/import-runs/{import_run['id']}/confirm",
        json={"version_label": "v1-http-smoke", "normative_reference": None, "source_document_url": None},
    )
    assert confirm_response.status_code == 200
    version = confirm_response.json()

    submit = auth_client.post(
        f"/api/v1/tax-schedules/versions/{version['id']}/submit-for-review", json={"comment": None}
    )
    assert submit.status_code == 200

    approve = auth_client.post(
        f"/api/v1/tax-schedules/versions/{version['id']}/approve", json={"comment": "Aprobado smoke"}
    )
    assert approve.status_code == 200
    return source["id"]


def test_full_http_flow_period_generate_confirm_dashboard(auth_client, obligation_catalog) -> None:
    obligation = obligation_catalog["621"]
    client = _make_client(auth_client)
    _assign_obligation(auth_client, client["id"], str(obligation.id))
    _make_approved_schedule(auth_client, str(obligation.id))

    # 1) crear periodo mensual
    period_response = auth_client.post(
        "/api/v1/tax-periods",
        json={
            "period_type": "MONTHLY",
            "fiscal_year": 2026,
            "month": 1,
            "canonical_code": None,
            "schedule_tax_period_code": None,
            "display_name": None,
            "start_date": None,
            "end_date": None,
            "description": None,
        },
    )
    assert period_response.status_code == 201
    period = period_response.json()
    assert period["canonical_code"] == "2026-01"
    assert period["is_open"] is True

    # 2) previsualizar generacion masiva
    preview_response = auth_client.post(
        "/api/v1/declarations/generation-runs/preview",
        json={"tax_period_id": period["id"], "filters": None},
    )
    assert preview_response.status_code == 201
    run = preview_response.json()
    assert run["status"] == "PREVIEWED"
    apto_items = [i for i in run["items"] if i["classification"] == "APTO"]
    assert len(apto_items) == 1

    # 3) confirmar
    confirm_response = auth_client.post(f"/api/v1/declarations/generation-runs/{run['id']}/confirm")
    assert confirm_response.status_code == 200
    confirmed = confirm_response.json()
    assert confirmed["status"] == "COMPLETED"
    assert confirmed["total_created"] == 1
    declaration_id = confirmed["items"][0]["declaration_id"]
    assert declaration_id is not None

    # reconfirmar es idempotente
    reconfirm_response = auth_client.post(f"/api/v1/declarations/generation-runs/{run['id']}/confirm")
    assert reconfirm_response.status_code == 200
    assert reconfirm_response.json()["total_created"] == 1

    # 4) tablero mensual
    dashboard_response = auth_client.get(
        "/api/v1/dashboard/monthly", params={"tax_period_id": period["id"]}
    )
    assert dashboard_response.status_code == 200
    dashboard = dashboard_response.json()
    assert dashboard["total"] == 1
    row = dashboard["items"][0]
    assert row["id"] == declaration_id
    assert row["computed_risk_status"] is not None

    # 5) detalle de la declaracion + historial
    detail_response = auth_client.get(f"/api/v1/declarations/{declaration_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["workflow_status"] == "PENDING"
    row_version = detail["row_version"]

    history_response = auth_client.get(f"/api/v1/declarations/{declaration_id}/history")
    assert history_response.status_code == 200
    history = history_response.json()
    assert len(history["due_date_history"]) == 1
    assert len(history["status_history"]) == 4

    # 6) transicion de workflow via HTTP
    workflow_response = auth_client.patch(
        f"/api/v1/declarations/{declaration_id}/workflow-status",
        json={"target": "DOCUMENTS_REQUESTED", "reason": None, "row_version": row_version},
    )
    assert workflow_response.status_code == 200
    assert workflow_response.json()["workflow_status"] == "DOCUMENTS_REQUESTED"

    # 7) cierre normal debe estar bloqueado en 3.1
    close_response = auth_client.post(
        f"/api/v1/declarations/{declaration_id}/close",
        json={"force_close_with_pending": False, "close_override_reason": None},
    )
    assert close_response.status_code == 409

    force_close_response = auth_client.post(
        f"/api/v1/declarations/{declaration_id}/close",
        json={"force_close_with_pending": True, "close_override_reason": "forzado"},
    )
    assert force_close_response.status_code == 422
