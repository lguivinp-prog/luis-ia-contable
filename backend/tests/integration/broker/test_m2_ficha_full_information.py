"""M2 (cierre): informacion OFICIAL completa de la Ficha RUC, regimen oficial vs. valor por defecto, RBAC de datos personales y
Flujo B (actualizar un cliente existente). Datos SINTETICOS que reproducen la ESTRUCTURA del Reporte oficial; ningun dato real."""

from __future__ import annotations

from sqlalchemy import func, select

from app.models.audit_log import AuditLog
from app.models.client import Client
from app.models.obligation import ClientObligation
from app.models.client_ficha_ruc import ClientFichaRucSnapshot
from tests.integration._sol_agent_access_helpers import fake_ruc
from tests.integration.broker.test_m2_destination_flow import (  # noqa: F401 -- fixtures y ayudantes reutilizados
    _ficha_proposal,
    _start_onboarding,
    add_user,
    db_row,
    drive_to_credential,
    ficha_fields,
    start_destination,
    world,
)

FULL = {
    "dependencia_sunat": "0000 - DEPENDENCIA FICTICIA",
    "domicilio": {
        "departamento": "DEPARTAMENTO FICTICIO", "provincia": "PROVINCIA FICTICIA", "distrito": "DISTRITO FICTICIO",
        "zona": "URB. ZONA FICTICIA", "via": "AV. VIA FICTICIA", "numero": "123", "condicion_inmueble": "PROPIO",
    },
    "tributos_afectos": [
        {"tributo": "IGV - OPER. INT. - CTA. PROPIA", "afecto_desde": "2019-05-01"},
        {"tributo": "RENTA - REGIMEN MYPE TRIBUTARIO", "afecto_desde": "2019-05-01"},
        {"tributo": "SENCICO", "afecto_desde": "2019-05-01"},
    ],
    "representantes_legales": [
        {"nombre": "PERSONA FICTICIA UNO", "tipo_documento": "DOC. NACIONAL DE IDENTIDAD", "numero_documento": "00000001",
         "cargo": "TITULAR-GERENTE", "fecha_desde": "2019-02-25"},
    ],
    "establecimientos_anexos": [
        {"codigo": "0001", "tipo": "SUCURSAL", "denominacion": "ANEXO FICTICIO", "direccion": "CALLE FICTICIA 1", "condicion_legal": "ALQUILADO"},
    ],
}


def _refresh(w, sf, **override):
    """Sesion con extraccion de un cliente EXISTENTE (Flujo B). Devuelve (sid, job_id, snapshot|None, respuesta del agente)."""
    sid, job_id, _ = drive_to_credential(w, start_destination(w, w["client_id"], "FICHA_RUC", extract=True).json())
    ruc = db_row(sf, Client, id=w["client_id"]).ruc
    response = w["agent"].report_ficha(job_id, ficha_fields(ruc, **override), session_id=sid)
    assert response.json()["accepted"] is True
    snap = db_row(sf, ClientFichaRucSnapshot, session_id=sid)
    return sid, job_id, snap


def _finish(w, job_id):
    assert w["agent"].report_status(job_id, "HUMAN_CONTROL").status_code == 200
    assert w["agent"].report_status(job_id, "COMPLETED").status_code == 200


def test_official_regime_comes_only_from_the_tributos_and_is_proposed_not_written(world, running_broker):
    build, _mk, sf = world
    w = build()
    s = sf()
    try:
        s.get(Client, w["client_id"]).tax_regime = "GENERAL"  # el valor por defecto heredado del alta (no oficial)
        s.commit()
    finally:
        s.close()
    _sid, _job, snap = _refresh(w, sf, **FULL)
    diff = {d["field"]: d for d in snap.diff}
    assert diff["tax_regime"]["current"] == "GENERAL" and diff["tax_regime"]["proposed"] == "RMT" and diff["tax_regime"]["changed"]
    assert diff["department"]["proposed"] == "DEPARTAMENTO FICTICIO" and diff["district"]["proposed"] == "DISTRITO FICTICIO"
    assert diff["fiscal_address"]["proposed"] == "AV. VIA FICTICIA 123, URB. ZONA FICTICIA"
    assert db_row(sf, Client, id=w["client_id"]).tax_regime == "GENERAL"  # nada se escribe sin aceptacion humana
    overview = w["api"].get(f"/api/v1/clients/{w['client_id']}/ficha-ruc/overview").json()
    assert overview["official_regime"]["value"] is None and overview["latest_accepted"] is None  # sin snapshot aceptado: NO hay regimen oficial


def test_unknown_or_ambiguous_renta_rows_never_produce_a_regime(world, running_broker):
    from app.services.ficha_ruc_service import official_regime_from_tributos as regime

    assert regime([{"tributo": "RENTA - REGIMEN MYPE TRIBUTARIO"}]) == "RMT"
    assert regime([{"tributo": "renta - régimen mype tributario"}, {"tributo": "IGV"}]) == "RMT"
    assert regime([{"tributo": "IGV - OPER. INT. - CTA. PROPIA"}]) is None  # sin renta: no se infiere
    assert regime([{"tributo": "RENTA - ALGO NUEVO"}]) is None  # texto desconocido: no se infiere
    assert regime([{"tributo": "RENTA - REGIMEN MYPE TRIBUTARIO"}, {"tributo": "RENTA - ALGO NUEVO"}]) is None
    assert regime([]) is None and regime(None) is None


def test_accepting_the_regime_updates_it_and_overview_shows_source_and_date_without_creating_obligations(world, running_broker):
    build, _mk, sf = world
    w = build()
    s = sf()
    try:
        before_obligations = s.execute(select(func.count()).select_from(ClientObligation)).scalar_one()
    finally:
        s.close()
    _sid, _job, snap = _refresh(w, sf, **FULL)
    url = f"/api/v1/clients/{w['client_id']}/ficha-ruc/snapshots/{snap.id}"
    changed = [d["field"] for d in snap.diff if d["changed"]]
    r = w["api"].post(url + "/accept", json={"accepted_fields": changed, "expected_version": snap.version})
    assert r.status_code == 200, r.text
    client = db_row(sf, Client, id=w["client_id"])
    assert client.tax_regime == "RMT" and client.department == "DEPARTAMENTO FICTICIO"
    overview = w["api"].get(f"/api/v1/clients/{w['client_id']}/ficha-ruc/overview").json()
    assert overview["official_regime"]["value"] == "RMT" and overview["official_regime"]["source"] == "SUNAT_FICHA_RUC"
    assert overview["official_regime"]["as_of"] is not None and overview["latest_accepted"]["id"] == str(snap.id)
    assert [t["tributo"] for t in overview["latest_accepted"]["fields"]["tributos_afectos"]][1] == "RENTA - REGIMEN MYPE TRIBUTARIO"
    s = sf()
    try:
        after_obligations = s.execute(select(func.count()).select_from(ClientObligation)).scalar_one()
    finally:
        s.close()
    assert after_obligations == before_obligations  # los tributos se PRESENTAN: no se asignan obligaciones automaticamente


def test_legal_representatives_are_visible_only_to_roles_that_can_edit_the_client(world, running_broker):
    build, mk, sf = world
    w = build()
    _sid, _job, snap = _refresh(w, sf, **FULL)
    url = f"/api/v1/clients/{w['client_id']}/ficha-ruc/snapshots/{snap.id}"
    admin_view = w["api"].get(url).json()
    assert admin_view["fields"]["representantes_legales"][0]["nombre"] == "PERSONA FICTICIA UNO"
    assert any(d["field"] == "representantes_legales" for d in admin_view["diff"])
    asistente = mk(w["tenant_id"], add_user(sf, w["tenant_id"], "asistente"))
    seen = asistente.get(url)
    assert seen.status_code == 200
    body = seen.json()
    assert "representantes_legales" not in body["fields"] and all(d["field"] != "representantes_legales" for d in body["diff"])
    assert "PERSONA FICTICIA" not in seen.text and "00000001" not in seen.text
    assert body["fields"]["tributos_afectos"] and body["fields"]["establecimientos_anexos"]  # lo no personal si se ve
    overview = asistente.get(f"/api/v1/clients/{w['client_id']}/ficha-ruc/overview")
    assert overview.status_code == 200 and "PERSONA FICTICIA" not in overview.text and overview.json()["sensitive_sections_visible"] is False
    listing = asistente.get(f"/api/v1/clients/{w['client_id']}/ficha-ruc/snapshots")
    assert listing.status_code == 200 and "PERSONA FICTICIA" not in listing.text


def test_audit_trail_records_field_names_never_representative_or_ficha_values(world, running_broker):
    build, _mk, sf = world
    w = build()
    _sid, _job, snap = _refresh(w, sf, **FULL)
    w["api"].post(f"/api/v1/clients/{w['client_id']}/ficha-ruc/snapshots/{snap.id}/accept",
                  json={"accepted_fields": ["tax_regime", "representantes_legales"], "expected_version": snap.version})
    s = sf()
    try:
        blob = " ".join(str(a.log_metadata) for a in s.execute(select(AuditLog)).scalars())
    finally:
        s.close()
    for secret in ("PERSONA FICTICIA", "00000001", "AV. VIA FICTICIA", "CALLE FICTICIA", "ANEXO FICTICIO"):
        assert secret not in blob, secret


def test_flow_b_identical_ficha_is_verified_without_a_new_snapshot_or_fabricated_differences(world, running_broker):
    build, _mk, sf = world
    w = build()
    _sid, job_id, snap = _refresh(w, sf, **FULL)
    changed = [d["field"] for d in snap.diff if d["changed"]]
    url = f"/api/v1/clients/{w['client_id']}/ficha-ruc/snapshots/{snap.id}"
    assert w["api"].post(url + "/accept", json={"accepted_fields": changed, "expected_version": snap.version}).status_code == 200
    _finish(w, job_id)
    s = sf()
    try:
        count_before = s.execute(select(func.count()).select_from(ClientFichaRucSnapshot)).scalar_one()
    finally:
        s.close()
    _sid2, job2, again = _refresh(w, sf, **FULL)
    assert again is None  # ningun snapshot nuevo: se verifico sin cambios
    s = sf()
    try:
        assert s.execute(select(func.count()).select_from(ClientFichaRucSnapshot)).scalar_one() == count_before
        events = s.execute(select(AuditLog).where(AuditLog.action == "FICHA_VERIFIED_NO_CHANGES")).scalars().all()
    finally:
        s.close()
    assert len(events) == 1
    overview = w["api"].get(f"/api/v1/clients/{w['client_id']}/ficha-ruc/overview").json()
    assert overview["pending_snapshot_id"] is None and overview["last_unchanged_verification_at"] is not None
    assert db_row(sf, Client, id=w["client_id"]).last_verified_at is not None
    _finish(w, job2)


def test_flow_b_a_real_change_creates_one_proposal_with_only_the_changed_fields(world, running_broker):
    build, _mk, sf = world
    w = build()
    _sid, job_id, snap = _refresh(w, sf, **FULL)
    url = f"/api/v1/clients/{w['client_id']}/ficha-ruc/snapshots/{snap.id}"
    changed = [d["field"] for d in snap.diff if d["changed"]]
    assert w["api"].post(url + "/accept", json={"accepted_fields": changed, "expected_version": snap.version}).status_code == 200
    _finish(w, job_id)
    moved = dict(FULL, domicilio=dict(FULL["domicilio"], distrito="DISTRITO NUEVO FICTICIO"),
                 tributos_afectos=FULL["tributos_afectos"] + [{"tributo": "ITAN", "afecto_desde": "2024-01-01"}])
    _sid2, _job2, second = _refresh(w, sf, **moved)
    assert second is not None and second.review_status == "PENDING_REVIEW" and second.purpose == "REFRESH"
    changed2 = {d["field"] for d in second.diff if d["changed"]}
    assert {"district", "tributos_afectos"} <= changed2
    assert "business_name" not in changed2 and "tax_regime" not in changed2  # lo que no cambio no se ofrece
    detail = {d["field"]: d for d in second.diff}
    assert detail["district"]["current"] == "DISTRITO FICTICIO" and detail["district"]["proposed"] == "DISTRITO NUEVO FICTICIO"
    # aceptar solo el distrito: lo demas queda como estaba y los datos internos intactos
    url2 = f"/api/v1/clients/{w['client_id']}/ficha-ruc/snapshots/{second.id}"
    assert w["api"].post(url2 + "/accept", json={"accepted_fields": ["district"], "expected_version": second.version}).status_code == 200
    c = db_row(sf, Client, id=w["client_id"])
    assert c.district == "DISTRITO NUEVO FICTICIO" and c.tax_regime == "RMT" and c.phone is None


def test_provisional_client_never_gets_a_default_regime_that_looks_official(world, running_broker):
    build, _mk, sf = world
    w = build(with_credential=False)
    created = _start_onboarding(w, fake_ruc())
    assert created.status_code == 201, created.text
    from uuid import UUID

    client = db_row(sf, Client, id=UUID(created.json()["client_id"]))
    assert client.tax_regime == "SIN_DETERMINAR"
    shown = w["api"].get(f"/api/v1/clients/{client.id}")
    assert shown.status_code == 200 and shown.json()["tax_regime"] == "SIN_DETERMINAR"
    overview = w["api"].get(f"/api/v1/clients/{client.id}/ficha-ruc/overview").json()
    assert overview["official_regime"]["value"] is None and overview["internal_tax_regime"] == "SIN_DETERMINAR"


def test_a_large_ficha_with_many_establishments_is_not_rejected_by_the_message_limit(world, running_broker):
    build, _mk, sf = world
    w = build()
    big = dict(FULL, establecimientos_anexos=[
        {"codigo": f"{i:04d}", "tipo": "SUCURSAL", "denominacion": f"ANEXO FICTICIO {i} " + "X" * 100, "direccion": "CALLE FICTICIA " + "Y" * 200,
         "ubicacion": "LIMA LIMA LIMA", "condicion_legal": "ALQUILADO"} for i in range(1, 41)])
    _sid, _job, snap = _refresh(w, sf, **big)
    assert len(snap.fields["establecimientos_anexos"]) == 40


def test_review_api_enforces_csrf_tenant_isolation_and_single_resolution(world, running_broker):
    """La pantalla de revision depende de estos limites: CSRF en aceptar/rechazar, aislamiento por tenant (tambien en el resumen) y
    resolucion unica de una propuesta pendiente (una segunda confirmacion concurrente no la vuelve a aplicar)."""
    build, mk, sf = world
    w = build()
    other = build(with_agent=False, with_credential=False)
    _sid, _job, snap = _refresh(w, sf, **FULL)
    base = f"/api/v1/clients/{w['client_id']}/ficha-ruc"
    url = f"{base}/snapshots/{snap.id}"
    changed = [d["field"] for d in snap.diff if d["changed"]]
    no_csrf = mk(w["tenant_id"], w["admin_id"])
    no_csrf.headers.pop("X-CSRF-Token", None)
    assert no_csrf.post(url + "/accept", json={"accepted_fields": changed, "expected_version": snap.version}).status_code == 403
    assert no_csrf.post(url + "/reject", json={"expected_version": snap.version}).status_code == 403
    assert db_row(sf, ClientFichaRucSnapshot, id=snap.id).review_status == "PENDING_REVIEW"  # sin CSRF no cambia nada
    assert other["api"].get(base + "/overview").status_code == 404  # otro tenant: el resumen tampoco se filtra
    assert other["api"].get(url).status_code == 404 and other["api"].get(base + "/snapshots").status_code == 404
    first = w["api"].post(url + "/accept", json={"accepted_fields": ["tax_regime"], "expected_version": snap.version})
    second = w["api"].post(url + "/accept", json={"accepted_fields": ["tax_regime"], "expected_version": snap.version})
    assert first.status_code == 200 and second.status_code == 409
    assert db_row(sf, Client, id=w["client_id"]).tax_regime == "RMT"
    audited = [a for a in _audit(sf) if a.action == "FICHA_PROPOSAL_ACCEPTED"]
    assert len(audited) == 1  # una sola aceptacion auditada


def _audit(sf):
    s = sf()
    try:
        return list(s.execute(select(AuditLog)).scalars())
    finally:
        s.close()


def test_manual_regime_correction_is_traced_as_manual_and_never_attributed_to_sunat(world, running_broker):
    build, _mk, sf = world
    w = build()
    base = f"/api/v1/clients/{w['client_id']}"
    overview = w["api"].get(base + "/ficha-ruc/overview").json()
    assert overview["internal_regime_source"] == "NO_REGISTRADO" and overview["official_regime"]["value"] is None
    current = w["api"].get(base).json()
    payload = {k: v for k, v in current.items() if k in {
        "ruc", "business_name", "trade_name", "person_type", "taxpayer_type", "ruc_status", "fiscal_address_condition", "fiscal_address",
        "ubigeo_code", "department", "province", "district", "main_economic_activity", "secondary_economic_activities", "tax_regime",
        "activities_start_date", "issuance_system", "phone", "email", "legal_representative_name", "legal_representative_doc",
        "contact_person", "whatsapp_number", "monthly_fee", "payment_day", "onboarding_date", "notes", "tags"}}
    payload["tax_regime"] = "RMT"
    changed = w["api"].put(base, json=payload)
    assert changed.status_code == 200, changed.text
    assert w["api"].put(base, json=payload).status_code == 200  # solicitud duplicada: no se duplica ningun registro
    s = sf()
    try:
        events = [a for a in s.execute(select(AuditLog)).scalars() if a.action == "CLIENT_TAX_REGIME_MANUAL_CHANGE"]
    finally:
        s.close()
    s = sf()
    try:
        updates = [a for a in s.execute(select(AuditLog)).scalars() if a.action == "CLIENT_UPDATE"]
    finally:
        s.close()
    assert len(updates) == 1  # el reenvio identico NO repite la auditoria de actualizacion
    assert len(events) == 1 and events[0].log_metadata["to"] == "RMT" and events[0].log_metadata["source"] == "MANUAL" and events[0].user_id == w["admin_id"]
    overview = w["api"].get(base + "/ficha-ruc/overview").json()
    assert overview["internal_regime_source"] == "MANUAL" and overview["internal_regime_changed_at"] is not None
    assert overview["official_regime"]["value"] is None  # una correccion manual NO se presenta como dato oficial de SUNAT
    # luego una propuesta de la Ficha aceptada fija el regimen con otra procedencia
    s = sf()
    try:
        s.get(Client, w["client_id"]).tax_regime = "GENERAL"
        s.commit()
    finally:
        s.close()
    _sid, _job, snap = _refresh(w, sf, **FULL)
    w["api"].post(f"{base}/ficha-ruc/snapshots/{snap.id}/accept", json={"accepted_fields": ["tax_regime"], "expected_version": snap.version})
    after = w["api"].get(base + "/ficha-ruc/overview").json()
    assert after["internal_regime_source"] == "SUNAT_FICHA_RUC" and after["official_regime"]["value"] == "RMT"
