"""M4: expediente de control SIRE (RVIE/RCE) -- escenarios funcionales completos con clientes, periodos y archivos SINTETICOS.
Postgres real, almacenamiento temporal aislado. Sin SUNAT, sin DARWIN, sin CONTASIS."""

from __future__ import annotations

import hashlib
import io
import uuid
import zipfile
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from openpyxl import Workbook
from sqlalchemy import event, func, select, text

from app.core.file_storage import LocalFileStorageBackend
from app.models.client_period_operation import ClientPeriodOperation
from app.models.declaration import Declaration
from app.models.obligation import ClientObligation
from app.models.sire_control import SireControl, SireControlFile, SireControlReview, SireControlSource
from app.models.tax_payment import TaxPayment
from tests.integration._declaration_helpers import make_client, make_monthly_period, make_other_tenant_with_user
from tests.integration.test_cockpit_monthly import World

BASE = "/api/v1/sire-controls"
SOURCE_DATE = "2026-02-05"  # el reloj congelado es 2026-02-10


@pytest.fixture()
def env(db_session, tenant, admin_user, obligation_catalog, frozen_clock):
    frozen_clock["now"] = datetime(2026, 2, 10, 15, 0, tzinfo=UTC)
    world = World(db_session, tenant, admin_user, obligation_catalog)
    client, declarations = world.client(name="Cliente M4", codes=("621", "SIRE-RVIE", "SIRE-RCE"))
    db_session.commit()
    return SimpleNamespace(world=world, client=client, declarations=declarations, period=world.period, tenant=tenant)


def manual(http, env, component, origin, *, client=None, **values):
    body = {
        "client_id": str((client or env.client).id), "tax_period_id": str(env.period.id), "component": component, "origin": origin, "currency": "PEN",
        "source_label": f"{origin} {component} prueba", "source_date": SOURCE_DATE, **values,
    }
    return http.post(f"{BASE}/sources/manual", json=body)


def period_view(http, env, client=None):
    response = http.get(f"{BASE}/by-period", params={"client_id": str((client or env.client).id), "tax_period_id": str(env.period.id)})
    assert response.status_code == 200, response.text
    return {c["component"]: c for c in response.json()["controls"]}


EQUAL = {"record_count": 120, "base_amount": "10000.00", "igv_amount": "1800.00", "total_amount": "11800.00"}


def csv_upload(http, env, content: str, *, component="RVIE", origin="SUNAT", name="datos.csv", client=None, mime="text/csv"):
    return http.post(
        f"{BASE}/files/preview",
        data={"client_id": str((client or env.client).id), "tax_period_id": str(env.period.id), "component": component, "origin": origin},
        files={"file": (name, content.encode("utf-8") if isinstance(content, str) else content, mime)},
    )


def minimal_pdf() -> bytes:
    return b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 3 3]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def xlsx_bytes(rows, *, sheets=None) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = (sheets or ["Datos"])[0]
    for row in rows:
        ws.append(row)
    for extra in (sheets or [])[1:]:
        wb.create_sheet(extra).append(["x"])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ============================================================================================ Escenario 1 -- cuadre correcto


def test_scenario_1_exact_match_human_review_persistence_and_history(contador_client, env, db_session):
    http = contador_client
    assert manual(http, env, "RVIE", "SUNAT", **EQUAL).status_code == 201
    created = manual(http, env, "RVIE", "DARWIN", **EQUAL)
    assert created.status_code == 201
    control = created.json()["control"]
    assert control["comparison"]["status"] == "MATCH" and control["summary_code"] == "MATCH" and control["current_review"] is None
    assert all(c["exact"] is True and c["difference"] in ("0", "0.00") for c in control["comparison"]["concepts"])
    # revision humana: confirma que verifico la comparacion
    review = http.post(f"{BASE}/{control['id']}/reviews", json={"decision": "APPROVED", "comparison_hash": control["comparison"]["comparison_hash"], "notes": "Verificado contra el reporte"})
    assert review.status_code == 201, review.text
    assert review.json()["version"] == 1 and review.json()["status"] == "CURRENT" and review.json()["reviewed_by_name"]
    # persistencia: otra lectura (otro request) ve el cuadre, la revision y el historial
    after = period_view(http, env)["RVIE"]
    assert after["summary_code"] == "MATCH_REVIEWED" and after["review_stale"] is False and after["current_review"]["decision"] == "APPROVED"
    history = http.get(f"{BASE}/{control['id']}/history").json()
    assert sorted(i["kind"] for i in history["items"]) == ["REVIEW", "SOURCE", "SOURCE"]
    # el cuadre NO es una presentacion: nada de lo tributario cambio
    assert period_view(http, env)["RCE"]["exists"] is False


# ============================================================================================ Escenario 2 -- diferencia real


def test_scenario_2_real_difference_is_shown_not_corrected_and_the_accountant_observes(contador_client, env, db_session):
    http = contador_client
    manual(http, env, "RCE", "SUNAT", record_count=50, base_amount="5000.00", igv_amount="900.00", total_amount="5900.00")
    made = manual(http, env, "RCE", "DARWIN", record_count=48, base_amount="4800.00", igv_amount="864.00", total_amount="5664.00").json()["control"]
    comparison = made["comparison"]
    assert comparison["status"] == "DIFFERENCES"
    diffs = {c["concept"]: c["difference"] for c in comparison["concepts"]}
    assert diffs == {"record_count": "-2", "base_amount": "-200.00", "igv_amount": "-36.00", "total_amount": "-236.00"}  # DARWIN - SUNAT
    # las fuentes NO se corrigen
    assert made["sunat_source"]["total_amount"] == "5900.00" and made["darwin_source"]["total_amount"] == "5664.00"
    # aprobar un control con diferencias esta prohibido; observar exige texto
    bad = http.post(f"{BASE}/{made['id']}/reviews", json={"decision": "APPROVED", "comparison_hash": comparison["comparison_hash"], "notes": "ok"})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "REVIEW_APPROVAL_REQUIRES_MATCH"
    no_notes = http.post(f"{BASE}/{made['id']}/reviews", json={"decision": "OBSERVED", "comparison_hash": comparison["comparison_hash"]})
    assert no_notes.status_code == 422 and no_notes.json()["detail"]["code"] == "REVIEW_NOTES_REQUIRED"
    ok = http.post(f"{BASE}/{made['id']}/reviews", json={"decision": "REQUIRES_REGULARIZATION", "comparison_hash": comparison["comparison_hash"], "notes": "Faltan 2 facturas de agosto"})
    assert ok.status_code == 201 and ok.json()["comparison_snapshot"]["status"] == "DIFFERENCES"
    assert period_view(http, env)["RCE"]["summary_code"] == "DIFFERENCES_REVIEWED"


# ============================================================================================ Escenario 3 -- informacion incompleta


def test_scenario_3_single_source_is_pending_and_unknown_is_never_zero(contador_client, env):
    http = contador_client
    made = manual(http, env, "RVIE", "SUNAT", record_count=10, total_amount="118.00").json()
    source = made["source"]
    assert source["base_amount"] is None and source["igv_amount"] is None  # desconocido != "0.00"
    control = made["control"]
    assert control["comparison"]["status"] == "PARTIAL" and control["comparison"]["single_source"] == "SUNAT" and control["comparison"]["concepts"] == []
    assert control["summary_code"] == "PARTIAL" and control["darwin_source"] is None
    # sin ningun dato no se crea una fuente (no se interpreta como cero)
    empty = manual(http, env, "RVIE", "DARWIN")
    assert empty.status_code == 422 and empty.json()["detail"]["code"] == "SOURCE_HAS_NO_DATA"
    # revisar algo sin dos fuentes: aprobar no procede; observar si
    assert http.post(f"{BASE}/{control['id']}/reviews", json={"decision": "APPROVED", "comparison_hash": control["comparison"]["comparison_hash"], "notes": "x"}).status_code == 422
    # un concepto conocido solo de un lado deja la comparacion como parcial aunque lo demas cuadre
    both = manual(http, env, "RVIE", "DARWIN", record_count=10, base_amount="100.00", total_amount="118.00").json()["control"]
    assert both["comparison"]["status"] == "PARTIAL" and both["comparison"]["one_sided_concepts"] == ["base_amount"]


# ============================================================================================ Escenario 4 -- reemplazo


def test_scenario_4_replacement_keeps_history_and_requires_an_independent_review(contador_client, env):
    http = contador_client
    manual(http, env, "RVIE", "SUNAT", **EQUAL)
    first = manual(http, env, "RVIE", "DARWIN", **EQUAL).json()
    control = first["control"]
    approved = http.post(f"{BASE}/{control['id']}/reviews", json={"decision": "APPROVED", "comparison_hash": control["comparison"]["comparison_hash"]}).json()
    # llega una version nueva de DARWIN con otros importes
    changed = dict(EQUAL, total_amount="11700.00")
    replaced = manual(http, env, "RVIE", "DARWIN", replaces_source_id=first["source"]["id"], **changed)
    assert replaced.status_code == 201
    new_control = replaced.json()["control"]
    assert replaced.json()["source"]["version"] == 2 and new_control["darwin_source"]["version"] == 2
    # la revision anterior NO se altera, pero queda obsoleta: se exige otra
    assert new_control["current_review"]["id"] == approved["id"] and new_control["review_stale"] is True and new_control["summary_code"] == "NEEDS_REVIEW"
    assert new_control["comparison"]["status"] == "DIFFERENCES"
    stale = http.post(f"{BASE}/{control['id']}/reviews", json={"decision": "APPROVED", "comparison_hash": control["comparison"]["comparison_hash"]})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "COMPARISON_CHANGED"  # no se aprueba lo que ya no es lo mostrado
    second = http.post(f"{BASE}/{control['id']}/reviews", json={"decision": "OBSERVED", "notes": "Total de DARWIN difiere", "comparison_hash": new_control["comparison"]["comparison_hash"]})
    assert second.status_code == 201 and second.json()["version"] == 2
    history = http.get(f"{BASE}/{control['id']}/history").json()
    assert [r["version"] for r in history["reviews"]] == [2, 1] and {r["status"] for r in history["reviews"]} == {"CURRENT", "SUPERSEDED"}
    assert sorted((s["origin"], s["version"], s["status"]) for s in history["sources"]) == [("DARWIN", 1, "SUPERSEDED"), ("DARWIN", 2, "ACTIVE"), ("SUNAT", 1, "ACTIVE")]
    # reemplazar sobre una vista desactualizada falla (concurrencia optimista) y un doble clic exacto no crea otra version
    conflict = manual(http, env, "RVIE", "DARWIN", replaces_source_id=first["source"]["id"], **changed)
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "SOURCE_CHANGED"
    again = manual(http, env, "RVIE", "DARWIN", **changed)
    assert again.status_code == 200 and again.json()["created"] is False and again.json()["source"]["version"] == 2


def test_idempotency_key_makes_retries_safe_and_detects_misuse(contador_client, env):
    http = contador_client
    key = str(uuid.uuid4())
    one = manual(http, env, "RVIE", "SUNAT", idempotency_key=key, **EQUAL)
    two = manual(http, env, "RVIE", "SUNAT", idempotency_key=key, **EQUAL)
    assert one.status_code == 201 and two.status_code == 200 and two.json()["created"] is False and one.json()["source"]["id"] == two.json()["source"]["id"]
    misuse = manual(http, env, "RVIE", "SUNAT", idempotency_key=key, **dict(EQUAL, total_amount="1.00"))
    assert misuse.status_code == 409 and misuse.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
    control = one.json()["control"]
    rk = str(uuid.uuid4())
    manual(http, env, "RVIE", "DARWIN", **EQUAL)
    view = period_view(http, env)["RVIE"]
    body = {"decision": "APPROVED", "comparison_hash": view["comparison"]["comparison_hash"], "idempotency_key": rk}
    first = http.post(f"{BASE}/{control['id']}/reviews", json=body)
    retry = http.post(f"{BASE}/{control['id']}/reviews", json=body)
    assert first.status_code == 201 and retry.status_code == 200 and first.json()["id"] == retry.json()["id"]


# ============================================================================================ Escenario 5 -- seguridad multi-RUC


def test_scenario_5_two_clients_same_period_share_nothing_and_foreign_tenant_gets_404(contador_client, auth_client, env, db_session, asistente_client):
    http = contador_client
    other_client = make_client(db_session, env.tenant.id, name="Cliente gemelo")
    db_session.commit()
    mine = manual(http, env, "RVIE", "SUNAT", **EQUAL).json()["control"]
    twin = manual(http, env, "RVIE", "SUNAT", client=other_client, **dict(EQUAL, total_amount="1.00")).json()["control"]
    assert mine["id"] != twin["id"]
    up = csv_upload(http, env, "Total;Nota\n10.00;x\n", name="mismo.csv")
    twin_up = csv_upload(http, env, "Total;Nota\n10.00;x\n", name="mismo.csv", client=other_client)
    assert up.status_code == 200 and twin_up.status_code == 200 and up.json()["file"]["id"] != twin_up.json()["file"]["id"]  # mismo contenido, expedientes distintos
    assert period_view(http, env)["RVIE"]["sunat_source"]["total_amount"] == "11800.00"
    assert period_view(http, env, other_client)["RVIE"]["sunat_source"]["total_amount"] == "1.00"
    assert period_view(http, env, other_client)["RCE"]["exists"] is False
    # un expediente se consulta por id: el archivo de un cliente no se obtiene con el id del otro
    file_id = up.json()["file"]["id"]
    assert http.get(f"{BASE}/{twin['id']}/files/{file_id}/download").status_code == 404
    assert http.get(f"{BASE}/{mine['id']}/files/{file_id}/download").status_code == 200
    # tenant ajeno: 404 uniforme (inexistente == ajeno)
    foreign_tenant, foreign_user = make_other_tenant_with_user(db_session)
    foreign_client = make_client(db_session, foreign_tenant.id, name="Ajeno")
    foreign_period = make_monthly_period(db_session, foreign_tenant.id, fiscal_year=2026, month=1)
    db_session.commit()
    from tests.conftest import _login_new_client

    outsider = _login_new_client(foreign_user)
    for url in (f"{BASE}/{mine['id']}", f"{BASE}/{mine['id']}/history", f"{BASE}/{mine['id']}/report", f"{BASE}/{mine['id']}/files/{file_id}/download", f"{BASE}/{mine['id']}/files/{file_id}/verify"):
        assert outsider.get(url).status_code == 404, url
    assert outsider.get(f"{BASE}/by-period", params={"client_id": str(env.client.id), "tax_period_id": str(env.period.id)}).status_code == 404
    assert outsider.get(f"{BASE}/by-period", params={"client_id": str(foreign_client.id), "tax_period_id": str(env.period.id)}).status_code == 404  # periodo ajeno
    assert outsider.post(f"{BASE}/sources/manual", json={"client_id": str(env.client.id), "tax_period_id": str(env.period.id), "component": "RVIE", "origin": "SUNAT",
                                                         "currency": "PEN", "source_label": "x", "source_date": SOURCE_DATE, "record_count": 1}).status_code == 404
    assert outsider.post(f"{BASE}/{mine['id']}/reviews", json={"decision": "OBSERVED", "notes": "x", "comparison_hash": "0" * 64}).status_code == 404
    mixed = outsider.post(f"{BASE}/sources/manual", json={"client_id": str(foreign_client.id), "tax_period_id": str(env.period.id), "component": "RVIE", "origin": "SUNAT",
                                                         "currency": "PEN", "source_label": "x", "source_date": SOURCE_DATE, "record_count": 1})
    assert mixed.status_code == 404  # cliente propio + periodo de OTRO tenant
    assert foreign_period.id != env.period.id
    # permisos de lectura/escritura separados
    assert asistente_client.get(f"{BASE}/by-period", params={"client_id": str(env.client.id), "tax_period_id": str(env.period.id)}).status_code == 200
    assert asistente_client.post(f"{BASE}/sources/manual", json={"client_id": str(env.client.id), "tax_period_id": str(env.period.id), "component": "RVIE", "origin": "SUNAT",
                                                                "currency": "PEN", "source_label": "x", "source_date": SOURCE_DATE, "record_count": 1}).status_code == 403
    assert asistente_client.post(f"{BASE}/{mine['id']}/reviews", json={"decision": "OBSERVED", "notes": "x", "comparison_hash": "0" * 64}).status_code == 403
    assert auth_client.get(f"{BASE}/{mine['id']}").status_code == 200


def test_csrf_and_authentication_are_required(contador_client, client, env):
    body = {"client_id": str(env.client.id), "tax_period_id": str(env.period.id), "component": "RVIE", "origin": "SUNAT", "currency": "PEN", "source_label": "x",
            "source_date": SOURCE_DATE, "record_count": 1}
    no_csrf = contador_client
    saved = dict(no_csrf.headers)
    no_csrf.headers.pop("X-CSRF-Token", None)
    assert no_csrf.post(f"{BASE}/sources/manual", json=body).status_code == 403
    no_csrf.headers.update(saved)
    from fastapi.testclient import TestClient

    from app.main import app

    anonymous = TestClient(app)
    assert anonymous.get(f"{BASE}/by-period", params={"client_id": str(env.client.id), "tax_period_id": str(env.period.id)}).status_code == 401
    assert anonymous.post(f"{BASE}/sources/manual", json=body).status_code in (401, 403)


# ============================================================================================ Escenario 6 -- no afectacion tributaria


def _tax_snapshot(db_session):
    db_session.flush()
    tables = ("declarations", "tax_payments", "tax_assessments", "client_obligations", "client_period_operations", "client_period_operation_history", "tax_periods")
    snapshot = {}
    for table in tables:
        rows = db_session.execute(text(f"SELECT t::text FROM {table} t ORDER BY t::text")).scalars().all()
        snapshot[table] = hashlib.sha256("\n".join(rows).encode()).hexdigest()
        snapshot[table + "#count"] = len(rows)
    return snapshot


def test_scenario_6_the_whole_workflow_changes_no_tax_state(contador_client, env, db_session):
    http = contador_client
    assert db_session.execute(select(func.count()).select_from(Declaration).where(Declaration.tenant_id == env.tenant.id)).scalar_one() == 3  # (del tenant de la prueba) hay declaraciones SIRE reales en el periodo
    before = _tax_snapshot(db_session)
    control = manual(http, env, "RVIE", "SUNAT", **EQUAL).json()["control"]
    manual(http, env, "RVIE", "DARWIN", **EQUAL)
    view = period_view(http, env)["RVIE"]
    http.post(f"{BASE}/{control['id']}/reviews", json={"decision": "APPROVED", "comparison_hash": view["comparison"]["comparison_hash"]})
    manual(http, env, "RVIE", "DARWIN", **dict(EQUAL, record_count=121))  # reemplazo
    http.post(f"{BASE}/{control['id']}/tolerance", json={"amount": "1.00", "reason": "redondeo del sistema", "expected_row_version": period_view(http, env)["RVIE"]["row_version"]})
    http.post(f"{BASE}/{control['id']}/files", data={"origin": "SUNAT"}, files={"file": ("constancia.pdf", minimal_pdf(), "application/pdf")})
    csv_upload(http, env, "Total;Nota\n1.00;x\n", name="x.csv")
    http.get(f"{BASE}/{control['id']}/report")
    after = _tax_snapshot(db_session)
    assert before == after  # declaraciones (FILED/estados/importes), pagos, determinaciones, obligaciones, operaciones y periodos: INTACTOS
    assert db_session.execute(select(func.count()).select_from(Declaration).where(Declaration.tenant_id == env.tenant.id, Declaration.filing_status == "FILED")).scalar_one() == 0
    assert db_session.execute(select(func.count()).select_from(TaxPayment).where(TaxPayment.tenant_id == env.tenant.id)).scalar_one() == 0
    assert db_session.execute(select(func.count()).select_from(ClientPeriodOperation).where(ClientPeriodOperation.tenant_id == env.tenant.id)).scalar_one() == 0
    assert db_session.execute(select(func.count()).select_from(ClientObligation).where(ClientObligation.tenant_id == env.tenant.id)).scalar_one() == 3


def test_reading_never_creates_controls_and_no_sunat_or_vault_module_is_involved(contador_client, env, db_session):
    http = contador_client
    for _ in range(2):
        period_view(http, env)
        http.get("/api/v1/cockpit/monthly", params={"tax_period_id": str(env.period.id)})
        http.get(f"/api/v1/cockpit/clients/{env.client.id}/periods/{env.period.id}")
    assert db_session.execute(select(func.count()).select_from(SireControl)).scalar_one() == 0
    import inspect

    from app.services import sire_control_logic, sire_control_service, sire_import_parser

    sources = "\n".join(inspect.getsource(m) for m in (sire_control_logic, sire_control_service, sire_import_parser))
    for forbidden in ("sol_vault", "sol_access", "windows_agent", "playwright", "requests.", "httpx", "urllib.request", "subprocess", "buzon"):
        assert forbidden not in sources.lower(), forbidden


# ============================================================================================ Importacion CSV / XLSX


def test_csv_import_preview_mapping_commit_and_file_traceability(contador_client, env, db_session):
    http = contador_client
    content = "Periodo;Tipo;Base Imponible;IGV;Importe Total;Comprobante\n202601;Ventas;100,00;18,00;118,00;F001-1\n202601;Ventas;200,50;36,09;236,59;F001-2\n"
    preview = csv_upload(http, env, content, name="ventas.csv")
    assert preview.status_code == 200, preview.text
    p = preview.json()
    assert p["kind"] == "csv" and p["delimiter"] == ";" and p["headers"][2] == "Base Imponible" and p["decimal_style_detected"] == "COMMA"
    assert p["suggested_mapping"]["base_amount"] == "Base Imponible" and p["suggested_mapping"]["igv_amount"] == "IGV" and p["suggested_mapping"]["total_amount"] == "Importe Total"
    assert p["file"]["sha256_hash"] == hashlib.sha256(content.encode()).hexdigest() and p["file"]["status"] == "STAGED" and p["duplicate_of_existing"] is False
    for leak in ("storage_key", "document_storage", "\\\\", "/var/lib"):
        assert leak not in preview.text
    commit = http.post(f"{BASE}/sources/import", json={
        "file_id": p["file"]["id"], "mode": "DETAIL", "decimal_style": "COMMA", "currency": "PEN", "source_label": "Export SUNAT enero", "source_date": SOURCE_DATE,
        "mapping": {"base_amount": "Base Imponible", "igv_amount": "IGV", "total_amount": "Importe Total", "period": "Periodo", "record_type": "Tipo"},
    })
    assert commit.status_code == 201, commit.text
    source = commit.json()["source"]
    assert (source["record_count"], source["base_amount"], source["igv_amount"], source["total_amount"]) == (2, "300.50", "54.09", "354.59")
    assert source["entry_mode"] == "IMPORT" and source["file_id"] == p["file"]["id"] and source["import_summary"]["rows_used"] == 2
    stored = http.get(f"{BASE}/{commit.json()['control']['id']}").json()["files"][0]
    assert stored["status"] == "ACTIVE" and stored["source_id"] == source["id"]
    # el mismo archivo no se importa dos veces ni se duplica al re-subirlo
    again = http.post(f"{BASE}/sources/import", json={"file_id": p["file"]["id"], "mode": "DETAIL", "decimal_style": "COMMA", "currency": "PEN", "source_label": "otra", "source_date": SOURCE_DATE,
                                                      "mapping": {"total_amount": "Importe Total"}})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "FILE_ALREADY_IMPORTED"
    resubmit = csv_upload(http, env, content, name="renombrado.csv")
    assert resubmit.json()["duplicate_of_existing"] is True and resubmit.json()["file"]["id"] == p["file"]["id"]
    assert db_session.execute(select(func.count()).select_from(SireControlFile)).scalar_one() == 1


def test_import_rejections_are_precise_and_nothing_is_imported(contador_client, env, db_session):
    http = contador_client

    def try_import(content, mapping, **extra):
        preview = csv_upload(http, env, content, name=f"{uuid.uuid4().hex[:6]}.csv").json()
        return http.post(f"{BASE}/sources/import", json={"file_id": preview["file"]["id"], "mode": "DETAIL", "decimal_style": "DOT", "currency": "PEN",
                                                        "source_label": "x", "source_date": SOURCE_DATE, "mapping": mapping, **extra})

    mismatch = try_import("Periodo;Total\n202512;10.00\n", {"total_amount": "Total", "period": "Periodo"})
    assert mismatch.status_code == 422 and mismatch.json()["detail"]["errors"][0]["code"] == "IMPORT_PERIOD_MISMATCH"
    wrong_type = try_import("Tipo;Total\nCompras;10.00\n", {"total_amount": "Total", "record_type": "Tipo"})
    assert wrong_type.json()["detail"]["errors"][0]["code"] == "IMPORT_TYPE_MISMATCH"
    totals = try_import("Detalle;Total\na;10.00\nTOTAL;10.00\n", {"total_amount": "Total"})
    assert totals.json()["detail"]["errors"][0]["code"] == "IMPORT_TOTAL_ROW"
    ignored = try_import("Detalle;Total\nb;10.00\nTOTAL;10.00\n", {"total_amount": "Total"}, ignore_total_rows=True)
    assert ignored.status_code == 201 and ignored.json()["source"]["record_count"] == 1
    bad_number = try_import("Detalle;Total\nc;abc\n", {"total_amount": "Total"})
    assert bad_number.json()["detail"]["errors"][0]["code"] == "IMPORT_NUMBER_INVALID"
    unknown_col = try_import("Detalle;Total\nd;1.00\n", {"total_amount": "No existe"})
    assert unknown_col.status_code == 422 and unknown_col.json()["detail"]["code"] == "IMPORT_MAPPING_COLUMN_NOT_FOUND"
    ambiguous = csv_upload(http, env, "Total;Total\n1;2\n", name="amb.csv")
    assert ambiguous.status_code == 422 and ambiguous.json()["detail"]["code"] == "IMPORT_AMBIGUOUS_HEADERS"
    auto = try_import("Detalle;Total\ne;\"1,234\"\n", {"total_amount": "Total"}, decimal_style="AUTO")
    assert auto.status_code == 422 and auto.json()["detail"]["code"] == "IMPORT_DECIMAL_AMBIGUOUS"
    # solo la importacion valida creo fuentes: la de totales ignorados
    assert db_session.execute(select(func.count()).select_from(SireControlSource)).scalar_one() == 1


def test_xlsx_import_with_sheet_selection(contador_client, env):
    http = contador_client
    data = xlsx_bytes([["Base", "IGV", "Total"], [100.1, 18.02, 118.12], [0.2, 0.04, 0.24]], sheets=["Ventas", "Resumen"])
    preview = http.post(f"{BASE}/files/preview", data={"client_id": str(env.client.id), "tax_period_id": str(env.period.id), "component": "RVIE", "origin": "DARWIN", "sheet": "Ventas"},
                        files={"file": ("export.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert preview.status_code == 200, preview.text
    p = preview.json()
    assert p["kind"] == "xlsx" and p["sheets"] == ["Ventas", "Resumen"] and p["sheet"] == "Ventas" and p["headers"] == ["Base", "IGV", "Total"]
    done = http.post(f"{BASE}/sources/import", json={"file_id": p["file"]["id"], "mode": "DETAIL", "sheet": "Ventas", "currency": "PEN", "source_label": "Export DARWIN", "source_date": SOURCE_DATE,
                                                    "decimal_style": "DOT", "mapping": {"base_amount": "Base", "igv_amount": "IGV", "total_amount": "Total"}})
    assert done.status_code == 201, done.text
    assert done.json()["source"]["total_amount"] == "118.36" and done.json()["source"]["origin"] == "DARWIN"
    no_sheet = http.post(f"{BASE}/sources/import", json={"file_id": p["file"]["id"], "mode": "DETAIL", "currency": "PEN", "source_label": "x", "source_date": SOURCE_DATE,
                                                        "mapping": {"total_amount": "Total"}})
    assert no_sheet.status_code in (409, 422)


# ============================================================================================ Archivos maliciosos y evidencia


def test_malicious_and_unsupported_files_are_rejected_without_leaving_traces(contador_client, env, db_session, tmp_path):
    http = contador_client
    cases = {
        "exe.csv": (b"MZ\x90\x00\x03\x00\x00\x00\x04\x00" + b"\x00" * 100, 415),
        "script.csv": (b"<script>alert(1)</script>", 415),
        "falso.pdf": (b"esto no es un pdf", 415),
        "vacio.csv": (b"", 400),
        "docx.docx": (b"PK\x03\x04" + b"\x00" * 50, 415),
        "macro.xlsx": (b"", 0),
    }
    wb = io.BytesIO(xlsx_bytes([["Total"], [1]]))
    macro = io.BytesIO()
    with zipfile.ZipFile(wb) as src, zipfile.ZipFile(macro, "w") as dst:
        for item in src.infolist():
            dst.writestr(item, src.read(item.filename))
        dst.writestr("xl/vbaProject.bin", b"x")
    cases["macro.xlsx"] = (macro.getvalue(), 415)  # estructura OOXML inesperada (macros): rechazada por contenido
    for name, (content, expected) in cases.items():
        response = csv_upload(http, env, content, name=name)
        assert response.status_code == expected, (name, response.status_code, response.text)
    assert db_session.execute(select(func.count()).select_from(SireControlFile)).scalar_one() == 0
    storage_root = tmp_path / "document_storage"
    assert not storage_root.exists() or not [p for p in storage_root.rglob("*") if p.is_file()]  # ni temporales (.part) ni blobs huerfanos
    # un ZIP con compresion exagerada (zip bomb) se rechaza sin abrirse
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", b"<Types/>")
        zf.writestr("xl/workbook.xml", b"0" * 5_000_000)
    assert csv_upload(http, env, bomb.getvalue(), name="bomba.xlsx").status_code in (415, 422)


def test_oversized_files_are_rejected_with_413(contador_client, env, monkeypatch, tmp_path):
    from app.routers import sire_controls

    monkeypatch.setattr(sire_controls._settings, "sire_file_max_bytes", 1000)
    response = csv_upload(contador_client, env, "Total;Nota\n" + "1.00;xxxxxxxxxx\n" * 200, name="grande.csv")
    assert response.status_code == 413 and response.json()["detail"]["code"] == "FILE_TOO_LARGE"
    assert not [p for p in (tmp_path / "document_storage").rglob("*") if p.is_file()]


def test_evidence_files_sha256_duplicates_download_integrity_and_no_path_leaks(contador_client, env, tmp_path, db_session):
    http = contador_client
    control = manual(http, env, "RVIE", "SUNAT", **EQUAL).json()["control"]
    pdf = minimal_pdf()
    stored = http.post(f"{BASE}/{control['id']}/files", data={"origin": "SUNAT", "description": "Constancia de consulta"}, files={"file": ("../../etc/passwd.pdf", pdf, "application/pdf")})
    assert stored.status_code == 201, stored.text
    f = stored.json()["file"]
    assert f["sha256_hash"] == hashlib.sha256(pdf).hexdigest() and f["original_filename"] == "passwd.pdf" and f["mime_type"] == "application/pdf" and f["purpose"] == "EVIDENCE"
    for leak in ("storage_key", "document_storage", str(tmp_path), "sire/"):
        assert leak not in stored.text
    dup = http.post(f"{BASE}/{control['id']}/files", data={"origin": "SUNAT"}, files={"file": ("otra.pdf", pdf, "application/pdf")})
    assert dup.status_code == 200 and dup.json()["created"] is False and dup.json()["file"]["id"] == f["id"]
    other_origin = http.post(f"{BASE}/{control['id']}/files", data={"origin": "DARWIN"}, files={"file": ("otra.pdf", pdf, "application/pdf")})
    assert other_origin.status_code == 201 and other_origin.json()["file"]["id"] != f["id"]  # el mismo archivo bajo otro origen es evidencia distinta
    download = http.get(f"{BASE}/{control['id']}/files/{f['id']}/download")
    assert download.status_code == 200 and download.content == pdf
    assert "attachment" in download.headers["content-disposition"] and download.headers["x-content-type-options"] == "nosniff" and download.headers["cache-control"] == "no-store"
    assert http.get(f"{BASE}/{control['id']}/files/{f['id']}/verify").json() == {"file_id": f["id"], "stored_sha256": f["sha256_hash"], "computed_sha256": f["sha256_hash"], "intact": True, "present": True}
    # alteracion del blob => la verificacion de integridad la detecta
    key = db_session.execute(select(SireControlFile.storage_key).where(SireControlFile.id == uuid.UUID(f["id"]))).scalar_one()
    (tmp_path / "document_storage" / key).write_bytes(pdf + b"manipulado")
    tampered = http.get(f"{BASE}/{control['id']}/files/{f['id']}/verify").json()
    assert tampered["intact"] is False and tampered["computed_sha256"] != f["sha256_hash"]
    (tmp_path / "document_storage" / key).unlink()
    assert http.get(f"{BASE}/{control['id']}/files/{f['id']}/verify").json()["present"] is False
    assert http.get(f"{BASE}/{control['id']}/files/{f['id']}/download").status_code == 404


def test_storage_failures_leave_no_rows_and_no_blobs(contador_client, env, db_session, tmp_path, monkeypatch):
    http = contador_client
    control = manual(http, env, "RVIE", "SUNAT", **EQUAL).json()["control"]
    # (1) la promocion del temporal falla: ninguna fila, ningun archivo
    original = LocalFileStorageBackend.promote
    monkeypatch.setattr(LocalFileStorageBackend, "promote", lambda self, a, b: (_ for _ in ()).throw(OSError("disco lleno")))
    with pytest.raises(OSError):
        http.post(f"{BASE}/{control['id']}/files", data={"origin": "SUNAT"}, files={"file": ("a.pdf", minimal_pdf(), "application/pdf")})
    monkeypatch.setattr(LocalFileStorageBackend, "promote", original)
    assert db_session.execute(select(func.count()).select_from(SireControlFile)).scalar_one() == 0
    root = tmp_path / "document_storage"
    assert not [p for p in root.rglob("*") if p.is_file() and p.suffix != ".part"] and not [p for p in root.rglob("*.pdf")]
    # (2) la auditoria falla DESPUES de escribir el blob: el blob se compensa
    from app.services import sire_control_service as svc

    monkeypatch.setattr(svc.audit_service, "record", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("auditoria caida")))
    with pytest.raises(RuntimeError):
        http.post(f"{BASE}/{control['id']}/files", data={"origin": "SUNAT"}, files={"file": ("a.pdf", minimal_pdf(), "application/pdf")})
    assert not [p for p in root.rglob("*.pdf")]
    assert db_session.execute(select(func.count()).select_from(SireControlFile)).scalar_one() == 0


# ============================================================================================ Tolerancia, reporte y versionado


def test_tolerance_is_explicit_traceable_never_hides_differences_and_marks_reviews_stale(contador_client, env):
    http = contador_client
    manual(http, env, "RCE", "SUNAT", record_count=5, total_amount="100.00")
    control = manual(http, env, "RCE", "DARWIN", record_count=5, total_amount="100.40").json()["control"]
    assert control["comparison"]["status"] == "DIFFERENCES" and control["tolerance_amount"] is None  # exacto por defecto
    no_reason = http.post(f"{BASE}/{control['id']}/tolerance", json={"amount": "0.50", "expected_row_version": control["row_version"]})
    assert no_reason.status_code == 422 and no_reason.json()["detail"]["code"] == "TOLERANCE_REASON_REQUIRED"
    stale = http.post(f"{BASE}/{control['id']}/tolerance", json={"amount": "0.50", "reason": "redondeo", "expected_row_version": control["row_version"] + 5})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "VERSION_CONFLICT"
    applied = http.post(f"{BASE}/{control['id']}/tolerance", json={"amount": "0.50", "reason": "Redondeo del sistema contable", "expected_row_version": control["row_version"]})
    assert applied.status_code == 200
    view = applied.json()
    total = next(c for c in view["comparison"]["concepts"] if c["concept"] == "total_amount")
    assert view["comparison"]["status"] == "MATCH" and view["comparison"]["tolerance_applied"] is True and total["difference"] == "0.40" and total["exact"] is False
    assert view["tolerance_amount"] == "0.50" and view["tolerance_reason"] == "Redondeo del sistema contable" and view["tolerance_set_at"]
    reviewed = http.post(f"{BASE}/{control['id']}/reviews", json={"decision": "APPROVED", "comparison_hash": view["comparison"]["comparison_hash"]}).json()
    fresh = period_view(http, env)["RCE"]  # la revision tambien incrementa la version del expediente
    cleared = http.post(f"{BASE}/{control['id']}/tolerance", json={"amount": None, "expected_row_version": fresh["row_version"]}).json()
    assert cleared["tolerance_amount"] is None and cleared["comparison"]["status"] == "DIFFERENCES"
    assert cleared["review_stale"] is True and cleared["current_review"]["id"] == reviewed["id"]  # cambiar la tolerancia exige una nueva revision
    assert http.post(f"{BASE}/{control['id']}/tolerance", json={"amount": "-1.00", "reason": "x", "expected_row_version": cleared["row_version"]}).status_code == 422


def test_control_report_is_compact_verifiable_and_disclaims_official_filing(contador_client, env):
    http = contador_client
    manual(http, env, "RVIE", "SUNAT", **EQUAL)
    control = manual(http, env, "RVIE", "DARWIN", **dict(EQUAL, total_amount="11790.00")).json()["control"]
    http.post(f"{BASE}/{control['id']}/files", data={"origin": "SUNAT", "description": "Constancia"}, files={"file": ("c.pdf", minimal_pdf(), "application/pdf")})
    http.post(f"{BASE}/{control['id']}/reviews", json={"decision": "OBSERVED", "notes": "Revisar diferencia de 10.00", "comparison_hash": period_view(http, env)["RVIE"]["comparison"]["comparison_hash"]})
    report = http.get(f"{BASE}/{control['id']}/report")
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["identification"]["component"] == "RVIE" and body["identification"]["client_business_name"] == "Cliente M4" and body["identification"]["tax_period_code"] == "2026-01"
    assert "NO constituye constancia oficial" in body["disclaimer"] and "NO está acreditada" in body["status_legend"]["official_filing"]
    assert body["sources"]["SUNAT"]["total_amount"] == "11800.00" and body["sources"]["DARWIN"]["total_amount"] == "11790.00"
    assert body["differences"][0]["concept"] == "total_amount" and body["differences"][0]["difference"] == "-10.00"
    assert body["files"][0]["sha256"] == hashlib.sha256(minimal_pdf()).hexdigest() and body["review"]["decision"] == "OBSERVED" and body["review"]["reviewed_by_name"]
    assert len(body["report_hash"]) == 64 and body["report_version"] == 1
    for leak in ("storage_key", "document_storage", "C:\\\\", "/var/lib"):
        assert leak not in report.text
    # el hash cambia solo si cambia el contenido (verificable) y es estable entre lecturas
    again = http.get(f"{BASE}/{control['id']}/report").json()
    assert again["report_hash"] == body["report_hash"]
    manual(http, env, "RVIE", "DARWIN", **EQUAL)
    assert http.get(f"{BASE}/{control['id']}/report").json()["report_hash"] != body["report_hash"]


def test_audit_entries_exist_and_contain_no_amounts_or_file_contents(contador_client, env, db_session):
    http = contador_client
    manual(http, env, "RVIE", "SUNAT", **EQUAL)
    from app.models.audit_log import AuditLog

    entries = db_session.execute(select(AuditLog).where(AuditLog.action.like("SIRE_%"))).scalars().all()
    assert {e.action for e in entries} >= {"SIRE_CONTROL_CREATED", "SIRE_SOURCE_ADDED"}
    dumped = str([e.log_metadata for e in entries])
    for secret in ("11800", "10000.00", "1800.00", "120"):
        assert secret not in dumped.replace("tax_period_id", "")  # sin importes en la auditoria


def test_validation_of_manual_input(contador_client, env):
    http = contador_client
    for bad, code in (
        (dict(base_amount="1.234"), "AMOUNT_INVALID"),
        (dict(base_amount="abc"), "AMOUNT_INVALID"),
        (dict(total_amount="1e30"), "AMOUNT_INVALID"),
        (dict(source_label="   "), "SOURCE_LABEL_REQUIRED"),
        (dict(source_date="2026-03-01"), "SOURCE_DATE_IN_FUTURE"),
    ):
        response = manual(http, env, "RVIE", "SUNAT", **{"total_amount": "1.00", **bad})
        assert response.status_code == 422, (bad, response.text)
        detail = response.json()["detail"]
        assert (detail["code"] if isinstance(detail, dict) else "PYDANTIC") in {code, "PYDANTIC"}, (bad, detail)
    for bad in ({"record_count": -1}, {"currency": "EUR"}, {"component": "OTRO"}, {"origin": "CONTASIS"}):
        body = {"client_id": str(env.client.id), "tax_period_id": str(env.period.id), "component": "RVIE", "origin": "SUNAT", "currency": "PEN", "source_label": "x",
                "source_date": SOURCE_DATE, "record_count": 1, **bad}
        assert http.post(f"{BASE}/sources/manual", json=body).status_code == 422, bad
    float_body = {"client_id": str(env.client.id), "tax_period_id": str(env.period.id), "component": "RVIE", "origin": "SUNAT", "currency": "PEN", "source_label": "x",
                  "source_date": SOURCE_DATE, "total_amount": 12.5}
    assert http.post(f"{BASE}/sources/manual", json=float_body).status_code == 422  # un importe JSON float se rechaza


def test_only_monthly_periods_and_client_period_pairs_of_the_same_tenant(contador_client, env, db_session):
    from app.models.tax_period import TaxPeriod

    http = contador_client
    ghost = http.post(f"{BASE}/sources/manual", json={"client_id": str(uuid.uuid4()), "tax_period_id": str(env.period.id), "component": "RVIE", "origin": "SUNAT", "currency": "PEN",
                                                     "source_label": "x", "source_date": SOURCE_DATE, "record_count": 1})
    assert ghost.status_code == 404 and ghost.json()["detail"]["code"] == "CLIENT_NOT_FOUND"
    ghost_period = http.post(f"{BASE}/sources/manual", json={"client_id": str(env.client.id), "tax_period_id": str(uuid.uuid4()), "component": "RVIE", "origin": "SUNAT", "currency": "PEN",
                                                            "source_label": "x", "source_date": SOURCE_DATE, "record_count": 1})
    assert ghost_period.status_code == 404 and ghost_period.json()["detail"]["code"] == "PERIOD_NOT_FOUND"
    assert db_session.get(TaxPeriod, env.period.id) is not None


# ============================================================================================ Escenario 7 -- cockpit, workspace y M3


def test_cockpit_row_summarises_each_component_without_replacing_legal_indicators(contador_client, env, db_session):
    http = contador_client

    def row():
        body = http.get("/api/v1/cockpit/monthly", params={"tax_period_id": str(env.period.id)}).json()
        return next(r for r in body["items"] if r["client_id"] == str(env.client.id))

    first = row()
    assert first["sire_control_rvie"] == "NO_CONTROL" and first["sire_control_rce"] == "NO_CONTROL"
    legal = {k: first[k] for k in ("sire_status", "sire_rvie_status", "sire_rce_status", "overall_risk", "nearest_due_date", "filing_progress", "declaration_count", "filed_declaration_count",
                                   "tax_total_assessed", "tax_total_payable", "tax_total_paid")}
    manual(http, env, "RVIE", "SUNAT", **EQUAL)
    assert row()["sire_control_rvie"] == "PARTIAL" and row()["sire_control_rce"] == "NO_CONTROL"
    made = manual(http, env, "RVIE", "DARWIN", **EQUAL).json()["control"]
    assert row()["sire_control_rvie"] == "MATCH"
    http.post(f"{BASE}/{made['id']}/reviews", json={"decision": "APPROVED", "comparison_hash": made["comparison"]["comparison_hash"]})
    manual(http, env, "RCE", "SUNAT", record_count=1, total_amount="10.00")
    manual(http, env, "RCE", "DARWIN", record_count=1, total_amount="9.00")
    final = row()
    assert (final["sire_control_rvie"], final["sire_control_rce"]) == ("MATCH_REVIEWED", "DIFFERENCES")
    assert {k: final[k] for k in legal} == legal  # los indicadores juridicos (presentacion, riesgo, vencimiento) NO cambian
    assert len([r for r in http.get("/api/v1/cockpit/monthly", params={"tax_period_id": str(env.period.id)}).json()["items"] if r["client_id"] == str(env.client.id)]) == 1  # una fila por cliente


def test_cockpit_query_count_does_not_grow_with_the_number_of_clients(contador_client, env, db_session):
    http = contador_client
    statements: list[str] = []
    engine = db_session.get_bind()

    @event.listens_for(engine, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        if "sire_control" in statement:
            statements.append(statement)

    try:
        http.get("/api/v1/cockpit/monthly", params={"tax_period_id": str(env.period.id)})
        small = len(statements)
        for i in range(6):
            client = make_client(db_session, env.tenant.id, name=f"Extra {i}")
            client.service_status = "activo"
        db_session.commit()
        statements.clear()
        http.get("/api/v1/cockpit/monthly", params={"tax_period_id": str(env.period.id)})
        assert len(statements) == small <= 1  # sin expedientes: una sola consulta de controles, sin N+1
        manual(http, env, "RVIE", "SUNAT", **EQUAL)
        statements.clear()
        http.get("/api/v1/cockpit/monthly", params={"tax_period_id": str(env.period.id)})
        assert len(statements) <= 3
    finally:
        event.remove(engine, "before_cursor_execute", _count)


def test_workspace_contract_and_m3_destinations_remain_available(contador_client, env, db_session):
    from app.core import sunat_destinations as sd

    workspace = contador_client.get(f"/api/v1/cockpit/clients/{env.client.id}/periods/{env.period.id}")
    assert workspace.status_code == 200 and workspace.json()["summary"]["sire_rvie_status"] is not None
    assert "sire_control_rvie" in workspace.json()["summary"]
    destinations = contador_client.get("/api/v1/sol-access/destinations").json()
    assert {"SIRE_RVIE", "SIRE_RCE", "DECLARACIONES_Y_PAGOS"} <= set(destinations["destinations"])  # M3 intacto
    assert sd.enabled_ids()[:2] == ["MENU_SOL", "FICHA_RUC"]


# ============================================================================================ Migracion y restricciones de BD


def test_database_constraints_protect_the_integrity_of_controls(db_session, env, contador_user):
    from sqlalchemy.exc import IntegrityError

    def attempt(model):
        with pytest.raises(IntegrityError):
            with db_session.begin_nested():
                db_session.add(model)
                db_session.flush()

    attempt(SireControl(tenant_id=env.tenant.id, client_id=env.client.id, tax_period_id=env.period.id, component="OTRO", row_version=1, created_by=contador_user.id))
    control = SireControl(tenant_id=env.tenant.id, client_id=env.client.id, tax_period_id=env.period.id, component="RVIE", row_version=1, created_by=contador_user.id)
    db_session.add(control)
    db_session.flush()
    attempt(SireControl(tenant_id=env.tenant.id, client_id=env.client.id, tax_period_id=env.period.id, component="RVIE", row_version=1, created_by=contador_user.id))  # unico por alcance
    from datetime import date

    def source(**kw):
        base = dict(tenant_id=env.tenant.id, control_id=control.id, client_id=env.client.id, tax_period_id=env.period.id, component="RVIE", origin="SUNAT", entry_mode="MANUAL", version=1,
                    status="ACTIVE", record_count=1, currency="PEN", source_label="x", source_date=date(2026, 2, 1), content_hash="h", created_by=contador_user.id,
                    created_at=datetime.now(UTC))
        base.update(kw)
        return SireControlSource(**base)

    attempt(source(record_count=None))  # sin ningun dato
    attempt(source(currency="EUR"))
    attempt(source(record_count=-1))
    attempt(source(client_id=uuid.uuid4()))  # alcance incoherente (FK compuesta)
    db_session.add(source())
    db_session.flush()
    attempt(source(version=2))  # dos fuentes ACTIVE del mismo origen
    attempt(source(status="SUPERSEDED", version=1))  # version repetida
    review = dict(tenant_id=env.tenant.id, control_id=control.id, client_id=env.client.id, tax_period_id=env.period.id, component="RVIE", version=1, status="CURRENT",
                  decision="OBSERVED", notes=None, comparison_hash="h", comparison_status="MATCH", comparison_snapshot={}, reviewed_by=contador_user.id, reviewed_at=datetime.now(UTC))
    attempt(SireControlReview(**review))  # observar sin texto
    assert db_session.execute(select(func.count()).select_from(SireControlReview)).scalar_one() == 0


def test_m4_operations_never_change_the_calendar_derived_monthly_phase(contador_client, env, db_session):
    """La fase mensual la calcula M1 (`monthly_phase`) solo con la fecha de trabajo y el fin del periodo; ninguna operacion M4 la altera."""
    from datetime import date

    from app.services.cockpit_logic import monthly_phase

    # regla M1 (septiembre de 2026): el trabajo del periodo empieza en octubre; desde el 15 de octubre es CIERRE
    assert [monthly_phase(date(2026, 9, 30), d) for d in (date(2026, 9, 30), date(2026, 10, 2), date(2026, 10, 14), date(2026, 10, 15), date(2026, 11, 3))] == [
        "PRE_APERTURA", "APERTURA", "OPERACION", "CIERRE", "CIERRE"]
    http = contador_client

    def phase():
        body = http.get("/api/v1/cockpit/monthly", params={"tax_period_id": str(env.period.id)}).json()
        return next(r for r in body["items"] if r["client_id"] == str(env.client.id))["monthly_phase"]

    before = phase()
    control = manual(http, env, "RVIE", "SUNAT", **EQUAL).json()["control"]
    manual(http, env, "RVIE", "DARWIN", **EQUAL)
    view = period_view(http, env)["RVIE"]
    http.post(f"{BASE}/{control['id']}/reviews", json={"decision": "APPROVED", "comparison_hash": view["comparison"]["comparison_hash"]})
    manual(http, env, "RVIE", "DARWIN", **dict(EQUAL, record_count=121))
    assert phase() == before


def test_a_corrected_review_is_a_new_version_the_report_uses_the_current_one_and_history_is_never_edited(contador_client, env, db_session):
    http = contador_client
    manual(http, env, "RCE", "SUNAT", record_count=50, base_amount="5000.00", igv_amount="900.00", total_amount="5900.00")
    made = manual(http, env, "RCE", "DARWIN", record_count=48, base_amount="4800.00", igv_amount="864.00", total_amount="5664.00").json()["control"]
    digest = made["comparison"]["comparison_hash"]
    first = http.post(f"{BASE}/{made['id']}/reviews", json={"decision": "OBSERVED", "comparison_hash": digest, "notes": "Referencia equivocada a RVIE"})
    second = http.post(f"{BASE}/{made['id']}/reviews", json={"decision": "OBSERVED", "comparison_hash": digest, "notes": "Observacion del RCE corregida"})
    assert (first.status_code, second.status_code) == (201, 201) and second.json()["version"] == first.json()["version"] + 1
    report = http.get(f"{BASE}/{made['id']}/report").json()
    assert report["review"]["version"] == 2 and "RCE" in report["review"]["notes"] and report["review"]["decision"] == "OBSERVED"  # la decision NO se cambia sola
    versions = {r["version"]: r for r in report["review_history"]}
    assert set(versions) == {1, 2} and "RVIE" in versions[1]["notes"]  # la anterior se conserva sin editar
    rows = db_session.execute(text("SELECT version, status FROM sire_control_reviews WHERE control_id = :c ORDER BY version"), {"c": made["id"]}).all()
    assert [(r.version, r.status) for r in rows] == [(1, "SUPERSEDED"), (2, "CURRENT")]
