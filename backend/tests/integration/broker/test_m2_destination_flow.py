"""M2: destinos de SOL validados + Ficha RUC (propuesta/revision humana) + alta asistida, de extremo a extremo con broker REAL,
mTLS REAL, boveda REAL (KDF rapido SOLO en pruebas) y PostgreSQL REAL. Datos y credenciales FICTICIOS; sin SUNAT."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.core.security import hash_password
from app.models.audit_log import AuditLog
from app.models.client import Client
from app.models.client_ficha_ruc import ClientFichaRucSnapshot, ClientOnboarding
from app.models.client_sol_credential import ClientSolCredential
from app.models.sol_agent_access import SolAccessSession, SolAgentJob
from app.models.user import User
from tests.integration._sol_agent_access_helpers import fake_ruc
from tests.integration.broker.test_sol_access_api_flow import (  # noqa: F401 -- fixtures reutilizados
    _VAULT_PASSWORD,
    TEST_SOL_PASSWORD_DO_NOT_USE,
    TEST_SOL_USER,
    world,
)

FICHA_TEXT_SECRETS = ["EMPRESA FICTICIA M2 S.A.C.", "AV. FICTICIA M2 999", "6201 - PROGRAMACION M2"]


def ficha_fields(ruc: str, **override) -> dict:
    fields = {
        "ruc": ruc,
        "razon_social": "EMPRESA FICTICIA M2 S.A.C.",
        "tipo_contribuyente": "SOCIEDAD ANONIMA CERRADA",
        "fecha_inicio_actividades": "2015-04-01",
        "estado_contribuyente": "ACTIVO",
        "condicion_contribuyente": "HABIDO",
        "domicilio_fiscal": "AV. FICTICIA M2 999 - LIMA",
        "actividades_economicas": [
            {"tipo": "PRINCIPAL", "ciiu": "6201", "descripcion": "PROGRAMACION M2"},
            {"tipo": "SECUNDARIA", "ciiu": "7020", "descripcion": "CONSULTORIA M2"},
        ],
    }
    fields.update(override)
    return fields


def start_destination(w, client_id, destination, extract=False, api=None):
    return (api or w["api"]).post("/api/v1/sol-access/sessions", json={
        "client_id": str(client_id), "idempotency_key": str(uuid.uuid4()), "destination": destination,
        "extract_ficha": extract})


def drive_to_credential(w, session_json):
    """Agente: claim -> PREAUTH_READY -> credencial (un solo uso). Devuelve (sid, job_id)."""
    sid = uuid.UUID(session_json["id"])
    agent = w["agent"]
    nxt = agent.jobs_next().json()
    job_id = uuid.UUID(nxt["job_id"])
    assert agent.claim(job_id).status_code == 200
    assert agent.report_status(job_id, "PREAUTH_READY", session_id=sid).status_code == 200
    assert agent.request_credential(job_id).status_code == 200
    return sid, job_id, nxt


def db_row(sf, model, **where):
    s = sf()
    try:
        stmt = select(model)
        for k, v in where.items():
            stmt = stmt.where(getattr(model, k) == v)
        row = s.execute(stmt).scalars().first()
        if row is not None:
            s.expunge(row)
        return row
    finally:
        s.close()


def client_ruc(sf, client_id):
    return db_row(sf, Client, id=client_id).ruc


def add_user(sf, tenant_id, role):
    s = sf()
    try:
        user = User(tenant_id=tenant_id, email=f"{role}-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev", full_name=role.title(),
                    password_hash=hash_password("TestPassword123!"), role=role, is_active=True)
        s.add(user)
        s.commit()
        return user.id
    finally:
        s.close()


# --------------------------------------------------------------------------------------------- sesiones con destino


def test_ficha_session_full_lifecycle_through_human_control(world, running_broker):
    build, _mk, sf = world
    w = build()
    r = start_destination(w, w["client_id"], "FICHA_RUC", extract=True)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["scope"] == "NAVIGATE_TO_DESTINATION" and body["destination"] == "FICHA_RUC" and body["extract_ficha"] is True
    sid, job_id, advert = drive_to_credential(w, body)
    assert advert["destination"] == "FICHA_RUC" and advert["extract_ficha"] is True and advert["scope"] == "NAVIGATE_TO_DESTINATION"

    # HUMAN_CONTROL antes de reportar la Ficha pedida => rechazado (la extraccion debe ir ANTES del traspaso)
    assert w["agent"].report_status(job_id, "HUMAN_CONTROL", session_id=sid).status_code == 409

    ruc = client_ruc(sf, w["client_id"])
    reported = w["agent"].report_ficha(job_id, ficha_fields(ruc), session_id=sid)
    assert reported.status_code == 200 and reported.json()["accepted"] is True
    assert w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()["status"] == "IN_PROGRESS"

    before = db_row(sf, SolAccessSession, id=sid)
    assert w["agent"].report_status(job_id, "HUMAN_CONTROL", session_id=sid).status_code == 200
    session = w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()
    assert session["status"] == "HUMAN_CONTROL" and session["is_terminal"] is False  # NO terminal
    assert session["operation_result"] == "DESTINATION_REACHED" and session["human_control_at"] is not None
    after = db_row(sf, SolAccessSession, id=sid)
    assert after.expires_at >= before.expires_at  # el control humano tiene su propio limite, nunca mas corto
    job = db_row(sf, SolAgentJob, id=job_id)
    assert job.status == "CLAIMED" and job.expires_at == after.expires_at  # el job sigue vivo durante el control humano
    # idempotente
    assert w["agent"].report_status(job_id, "HUMAN_CONTROL", session_id=sid).status_code == 200
    # el humano cierra el navegador => el agente reporta COMPLETED
    assert w["agent"].report_status(job_id, "COMPLETED", session_id=sid).status_code == 200
    final = w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()
    assert final["status"] == "SUCCEEDED" and final["is_terminal"] and final["operation_result"] == "DESTINATION_REACHED"
    assert db_row(sf, SolAgentJob, id=job_id).status == "COMPLETED"


def test_menu_sol_session_needs_no_snapshot_and_human_control_is_required_to_succeed(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid, job_id, _ = drive_to_credential(w, start_destination(w, w["client_id"], "MENU_SOL").json())
    # una sesion con destino NO puede completarse sin pasar por el control humano
    assert w["agent"].report_status(job_id, "COMPLETED", session_id=sid).status_code == 409
    assert w["agent"].report_status(job_id, "HUMAN_CONTROL", session_id=sid).status_code == 200
    assert w["agent"].report_status(job_id, "COMPLETED", session_id=sid).status_code == 200
    assert w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()["status"] == "SUCCEEDED"
    s = sf()
    try:
        assert s.execute(select(ClientFichaRucSnapshot.id).where(ClientFichaRucSnapshot.session_id == sid)).first() is None
    finally:
        s.close()


def test_landing_only_sessions_cannot_enter_human_control(world, running_broker):
    build, _mk, sf = world
    w = build()
    r = w["api"].post("/api/v1/sol-access/sessions", json={"client_id": str(w["client_id"]), "idempotency_key": str(uuid.uuid4())})
    assert r.status_code == 201 and r.json()["scope"] == "AUTHENTICATE_TO_LANDING_ONLY" and r.json()["destination"] is None
    sid, job_id, _ = drive_to_credential(w, r.json())
    assert w["agent"].report_status(job_id, "HUMAN_CONTROL", session_id=sid).status_code == 409
    assert w["agent"].report_ficha(job_id, ficha_fields(client_ruc(sf, w["client_id"])), session_id=sid).status_code in {404, 409}
    assert w["agent"].report_status(job_id, "COMPLETED", session_id=sid).status_code == 200  # flujo original intacto


@pytest.mark.parametrize("payload", [
    {"destination": "SIRE"}, {"destination": "DECLARACIONES_Y_PAGOS"}, {"destination": "DECLARA_FACIL"},
    {"destination": "TRAMITES_Y_CONSULTAS"}, {"destination": "https://evil.example/x"}, {"destination": "BUZON"},
    {"destination": "MENU_SOL", "url": "https://x"}, {"destination": "MENU_SOL", "selector": "#a"}, {"destination": "MENU_SOL", "script": "1"},
])
def test_destination_allowlist_rejects_everything_outside_the_closed_enum(world, running_broker, payload):
    build, _mk, sf = world
    w = build()
    r = w["api"].post("/api/v1/sol-access/sessions", json={"client_id": str(w["client_id"]), "idempotency_key": str(uuid.uuid4()), **payload})
    assert r.status_code == 422, (payload, r.status_code)
    assert db_row(sf, SolAccessSession, tenant_id=w["tenant_id"]) is None  # nada se creo
    assert w["agent"].jobs_next().json()["job_id"] is None  # nada se despacho


def test_extraction_is_only_allowed_for_ficha_ruc_and_requires_client_write(world, running_broker):
    build, mk, sf = world
    w = build()
    assert start_destination(w, w["client_id"], "MENU_SOL", extract=True).status_code in {403, 422}
    assert start_destination(w, w["client_id"], None, extract=True).status_code in {403, 422}
    assert w["api"].get("/api/v1/sol-access/destinations").json() == {"destinations": ["MENU_SOL", "FICHA_RUC"]}


def test_idempotent_start_with_different_destination_conflicts(world, running_broker):
    build, _mk, _sf = world
    w = build()
    key = str(uuid.uuid4())
    body = {"client_id": str(w["client_id"]), "idempotency_key": key, "destination": "MENU_SOL"}
    assert w["api"].post("/api/v1/sol-access/sessions", json=body).status_code == 201
    assert w["api"].post("/api/v1/sol-access/sessions", json=body).status_code == 200
    assert w["api"].post("/api/v1/sol-access/sessions", json={**body, "destination": "FICHA_RUC"}).status_code == 409


def test_wrong_taxpayer_fails_the_session_and_stores_nothing(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid, job_id, _ = drive_to_credential(w, start_destination(w, w["client_id"], "FICHA_RUC", extract=True).json())
    other = fake_ruc()
    r = w["agent"].report_ficha(job_id, ficha_fields(other), session_id=sid)
    assert r.status_code == 200 and r.json()["accepted"] is False
    session = w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()
    assert session["status"] == "FAILED" and session["terminal_reason_code"] == "WRONG_TAXPAYER_CONTEXT"
    assert db_row(sf, SolAgentJob, id=job_id).status == "FAILED"
    assert db_row(sf, ClientFichaRucSnapshot, session_id=sid) is None
    assert w["agent"].report_status(job_id, "HUMAN_CONTROL", session_id=sid).status_code in {404, 409}


@pytest.mark.parametrize("bad", [
    {"ruc": "123"}, {"campo_extra": "x"}, {"razon_social": ""}, {"fecha_inicio_actividades": "01/04/2015"},
    {"actividades_economicas": [{"tipo": "OTRA", "descripcion": "x"}]}, {"padrones": ["x"] * 31},
])
def test_invalid_ficha_fields_are_rejected_strictly(world, running_broker, bad):
    build, _mk, sf = world
    w = build()
    sid, job_id, _ = drive_to_credential(w, start_destination(w, w["client_id"], "FICHA_RUC", extract=True).json())
    fields = ficha_fields(client_ruc(sf, w["client_id"]))
    fields.update(bad)
    assert w["agent"].report_ficha(job_id, fields, session_id=sid).status_code == 409
    assert db_row(sf, ClientFichaRucSnapshot, session_id=sid) is None
    assert w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()["status"] == "IN_PROGRESS"  # una Ficha invalida no falla la sesion


def test_ficha_report_is_idempotent_and_conflicting_replays_are_rejected(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid, job_id, _ = drive_to_credential(w, start_destination(w, w["client_id"], "FICHA_RUC", extract=True).json())
    fields = ficha_fields(client_ruc(sf, w["client_id"]))
    assert w["agent"].report_ficha(job_id, fields, session_id=sid).json()["accepted"] is True
    assert w["agent"].report_ficha(job_id, fields, session_id=sid).json()["accepted"] is True  # reintento seguro
    assert w["agent"].report_ficha(job_id, {**fields, "razon_social": "OTRA RAZON"}, session_id=sid).status_code == 409
    s = sf()
    try:
        assert len(s.execute(select(ClientFichaRucSnapshot).where(ClientFichaRucSnapshot.session_id == sid)).scalars().all()) == 1
    finally:
        s.close()


def test_ficha_report_from_another_agent_or_session_is_not_found(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid, job_id, _ = drive_to_credential(w, start_destination(w, w["client_id"], "FICHA_RUC", extract=True).json())
    fields = ficha_fields(client_ruc(sf, w["client_id"]))
    assert w["agent"].report_ficha(job_id, fields, session_id=uuid.uuid4()).status_code == 404
    assert w["agent"].report_ficha(uuid.uuid4(), fields).status_code in {400, 404}


def test_cancel_during_human_control_cancels_session_and_job_and_agent_is_notified(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid, job_id, _ = drive_to_credential(w, start_destination(w, w["client_id"], "MENU_SOL").json())
    assert w["agent"].report_status(job_id, "HUMAN_CONTROL", session_id=sid).status_code == 200
    assert w["api"].post(f"/api/v1/sol-access/sessions/{sid}/cancel").status_code == 200
    assert w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()["status"] == "CANCELLED"
    assert db_row(sf, SolAgentJob, id=job_id).status == "CANCELLED"
    assert w["agent"].heartbeat().json().get("cancel_notice") is not None  # el agente cierra el navegador
    # el agente ya no puede resucitar la sesion
    assert w["agent"].report_status(job_id, "COMPLETED", session_id=sid).status_code in {404, 409}


def test_human_control_expiry_is_enforced_no_immortal_sessions(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid, job_id, _ = drive_to_credential(w, start_destination(w, w["client_id"], "MENU_SOL").json())
    assert w["agent"].report_status(job_id, "HUMAN_CONTROL", session_id=sid).status_code == 200
    s = sf()
    try:
        s.execute(text("UPDATE sol_access_sessions SET expires_at = now() - interval '1 minute' WHERE id = :i"), {"i": sid})
        s.commit()
    finally:
        s.close()
    session = w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()  # expiracion perezosa por la autoridad existente
    assert session["status"] == "EXPIRED" and session["terminal_reason_code"] == "SESSION_EXPIRED"
    assert db_row(sf, SolAgentJob, id=job_id).status == "CANCELLED"
    assert w["agent"].report_status(job_id, "COMPLETED", session_id=sid).status_code in {404, 409}


def test_credential_is_never_redelivered_during_or_after_human_control(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid, job_id, _ = drive_to_credential(w, start_destination(w, w["client_id"], "MENU_SOL").json())
    assert w["agent"].report_status(job_id, "HUMAN_CONTROL", session_id=sid).status_code == 200
    assert w["agent"].request_credential(job_id).status_code in {404, 409}
    assert w["agent"].report_status(job_id, "PREAUTH_READY", session_id=sid).status_code in {404, 409}


def test_rbac_asistente_cannot_start_destination_sessions_and_contador_can(world, running_broker):
    build, mk, sf = world
    w = build()
    asistente = mk(w["tenant_id"], add_user(sf, w["tenant_id"], "asistente"))
    assert start_destination(w, w["client_id"], "MENU_SOL", api=asistente).status_code == 403
    contador = mk(w["tenant_id"], add_user(sf, w["tenant_id"], "contador"))
    # el contador PASA la autorizacion (client:write + sol_session:create + sol_credential:read); la boveda la desbloqueo
    # el administrador, no el contador, asi que el preflight autoritativo responde SOL_VAULT_LOCKED (409), nunca 403
    denied_or_ok = start_destination(w, w["client_id"], "FICHA_RUC", extract=True, api=contador)
    assert denied_or_ok.status_code == 409 and denied_or_ok.json()["detail"]["code"] == "SOL_VAULT_LOCKED"


# ------------------------------------------------------------------------------------- revision humana de la Ficha


def _ficha_proposal(w, sf, **override):
    sid, job_id, _ = drive_to_credential(w, start_destination(w, w["client_id"], "FICHA_RUC", extract=True).json())
    ruc = client_ruc(sf, w["client_id"])
    assert w["agent"].report_ficha(job_id, ficha_fields(ruc, **override), session_id=sid).json()["accepted"] is True
    snap = db_row(sf, ClientFichaRucSnapshot, session_id=sid)
    return sid, job_id, snap


def test_ficha_snapshot_is_a_pending_proposal_with_a_diff_and_never_touches_the_client(world, running_broker):
    build, _mk, sf = world
    w = build()
    before = db_row(sf, Client, id=w["client_id"])
    _sid, _job, snap = _ficha_proposal(w, sf)
    assert snap.review_status == "PENDING_REVIEW" and snap.source == "SUNAT_FICHA_RUC" and snap.schema_version == 1
    assert snap.purpose == "REFRESH"
    diff = {d["field"]: d for d in snap.diff}
    assert diff["business_name"]["proposed"] == "EMPRESA FICTICIA M2 S.A.C." and diff["business_name"]["changed"] is True
    assert diff["fiscal_address_condition"]["changed"] is False  # HABIDO ya coincide
    assert diff["main_economic_activity"]["proposed"] == "6201 - PROGRAMACION M2"
    after = db_row(sf, Client, id=w["client_id"])
    assert after.business_name == before.business_name  # NADA se escribio sin aceptacion humana


def test_accept_applies_only_selected_official_fields_and_never_internal_metadata(world, running_broker):
    build, _mk, sf = world
    w = build()
    s = sf()
    try:
        c = s.get(Client, w["client_id"])
        c.phone, c.email, c.whatsapp_number, c.monthly_fee, c.notes, c.tags = "999111222", "interno@falso.pe", "51999111222", 450, "nota interna", ["vip"]
        s.commit()
    finally:
        s.close()
    _sid, _job, snap = _ficha_proposal(w, sf)
    url = f"/api/v1/clients/{w['client_id']}/ficha-ruc/snapshots/{snap.id}"
    detail = w["api"].get(url).json()
    assert detail["review_status"] == "PENDING_REVIEW" and any(d["field"] == "business_name" for d in detail["diff"])
    accepted = w["api"].post(url + "/accept", json={"accepted_fields": ["business_name", "fiscal_address"], "expected_version": snap.version})
    assert accepted.status_code == 200, accepted.text
    c = db_row(sf, Client, id=w["client_id"])
    assert c.business_name == "EMPRESA FICTICIA M2 S.A.C." and c.fiscal_address.startswith("AV. FICTICIA M2 999")
    assert c.main_economic_activity is None and c.taxpayer_type is None  # lo NO aprobado no cambia
    assert (c.phone, c.email, c.whatsapp_number, float(c.monthly_fee), c.notes, c.tags) == (
        "999111222", "interno@falso.pe", "51999111222", 450.0, "nota interna", ["vip"])  # metadatos internos intactos
    saved = db_row(sf, ClientFichaRucSnapshot, id=snap.id)
    assert saved.review_status == "ACCEPTED" and saved.accepted_fields == ["business_name", "fiscal_address"] and saved.reviewed_by == w["admin_id"]
    assert w["api"].post(url + "/accept", json={"accepted_fields": ["business_name"], "expected_version": saved.version}).status_code == 409


def test_accepted_secondary_activities_keep_the_client_serializable_as_text_list(world, running_broker):
    """Regresion hallada en la aceptacion por UI: las actividades secundarias se guardaban como objetos y el esquema del
    cliente (lista de textos) rompia `GET /clients` con 500."""
    build, _mk, sf = world
    w = build()
    _sid, _job, snap = _ficha_proposal(w, sf)
    url = f"/api/v1/clients/{w['client_id']}/ficha-ruc/snapshots/{snap.id}"
    proposed = {d["field"]: d["proposed"] for d in snap.diff}["secondary_economic_activities"]
    assert proposed == ["7020 - CONSULTORIA M2"]
    r = w["api"].post(url + "/accept", json={"accepted_fields": ["secondary_economic_activities", "main_economic_activity"],
                                              "expected_version": snap.version})
    assert r.status_code == 200, r.text
    one = w["api"].get(f"/api/v1/clients/{w['client_id']}")
    assert one.status_code == 200 and one.json()["secondary_economic_activities"] == ["7020 - CONSULTORIA M2"]
    assert one.json()["main_economic_activity"] == "6201 - PROGRAMACION M2"
    listing = w["api"].get("/api/v1/clients?limit=50")
    assert listing.status_code == 200 and listing.json()["total"] >= 1


def test_accept_validation_conflicts_and_reject(world, running_broker):
    build, _mk, sf = world
    w = build()
    _sid, _job, snap = _ficha_proposal(w, sf)
    url = f"/api/v1/clients/{w['client_id']}/ficha-ruc/snapshots/{snap.id}"
    assert w["api"].post(url + "/accept", json={"accepted_fields": ["monthly_fee"], "expected_version": snap.version}).status_code == 422
    assert w["api"].post(url + "/accept", json={"accepted_fields": ["fiscal_address_condition"], "expected_version": snap.version}).status_code == 422  # sin cambio
    assert w["api"].post(url + "/accept", json={"accepted_fields": ["business_name"], "expected_version": 99}).status_code == 409
    assert w["api"].post(url + "/accept", json={"accepted_fields": ["business_name"], "expected_version": snap.version, "extra": 1}).status_code == 422
    assert w["api"].post(url + "/reject", json={"expected_version": snap.version}).status_code == 200
    assert db_row(sf, ClientFichaRucSnapshot, id=snap.id).review_status == "REJECTED"
    assert db_row(sf, Client, id=w["client_id"]).business_name != "EMPRESA FICTICIA M2 S.A.C."  # rechazar no escribe
    assert w["api"].post(url + "/reject", json={"expected_version": snap.version + 1}).status_code == 409


def test_a_new_proposal_supersedes_the_pending_one(world, running_broker):
    build, _mk, sf = world
    w = build()
    _sid, job_id, first = _ficha_proposal(w, sf)
    assert w["agent"].report_status(job_id, "HUMAN_CONTROL").status_code == 200
    assert w["agent"].report_status(job_id, "COMPLETED").status_code == 200
    _sid2, _job2, second = _ficha_proposal(w, sf, razon_social="EMPRESA FICTICIA M2 S.A.C. 2")
    assert db_row(sf, ClientFichaRucSnapshot, id=first.id).review_status == "SUPERSEDED"
    assert db_row(sf, ClientFichaRucSnapshot, id=second.id).review_status == "PENDING_REVIEW"


def test_review_endpoints_enforce_rbac_and_tenant_isolation(world, running_broker):
    build, mk, sf = world
    w = build()
    other = build(with_agent=False, with_credential=False)
    _sid, _job, snap = _ficha_proposal(w, sf)
    url = f"/api/v1/clients/{w['client_id']}/ficha-ruc/snapshots/{snap.id}"
    asistente = mk(w["tenant_id"], add_user(sf, w["tenant_id"], "asistente"))
    assert asistente.get(url).status_code == 200  # client:read
    assert asistente.post(url + "/accept", json={"accepted_fields": ["business_name"], "expected_version": snap.version}).status_code == 403
    assert asistente.post(url + "/reject", json={"expected_version": snap.version}).status_code == 403
    assert other["api"].get(url).status_code == 404  # otro tenant
    assert other["api"].post(url + "/accept", json={"accepted_fields": ["business_name"], "expected_version": snap.version}).status_code == 404


# ------------------------------------------------------------------------------------------------- alta asistida


def _start_onboarding(w, ruc, api=None):
    return (api or w["api"]).post("/api/v1/client-onboardings", json={"ruc": ruc})


def _store_credential(w, client_id):
    r = w["api"].post(f"/api/v1/clients/{client_id}/sol-credential", json={
        "credential_kind": "SECONDARY", "sol_username": TEST_SOL_USER, "sol_password": TEST_SOL_PASSWORD_DO_NOT_USE,
        "authorization_basis": "Autorizacion de prueba M2"})
    assert r.status_code in {200, 201}, r.text


def test_assisted_onboarding_end_to_end_with_human_confirmation(world, running_broker):
    build, _mk, sf = world
    w = build(with_credential=False)
    ruc = fake_ruc()
    r = _start_onboarding(w, ruc)
    assert r.status_code == 201, r.text
    onboarding = r.json()
    assert onboarding["status"] == "IN_PROGRESS" and onboarding["ruc"] == ruc
    client_id = uuid.UUID(onboarding["client_id"])
    provisional = db_row(sf, Client, id=client_id)
    assert provisional.service_status == "prospecto" and provisional.business_name.startswith("Alta asistida pendiente")
    assert _start_onboarding(w, ruc).json()["id"] == onboarding["id"]  # idempotente: la misma alta en curso

    _store_credential(w, client_id)
    started = start_destination(w, client_id, "FICHA_RUC", extract=True)
    assert started.status_code == 201, started.text
    sid, job_id, _ = drive_to_credential(w, started.json())
    assert w["agent"].report_ficha(job_id, ficha_fields(ruc), session_id=sid).json()["accepted"] is True
    assert w["agent"].report_status(job_id, "HUMAN_CONTROL", session_id=sid).status_code == 200

    snap = db_row(sf, ClientFichaRucSnapshot, session_id=sid)
    assert snap.purpose == "ONBOARDING"
    state = w["api"].get(f"/api/v1/client-onboardings/{onboarding['id']}").json()
    assert state["status"] == "AWAITING_REVIEW" and state["snapshot_id"] == str(snap.id)
    # sin confirmacion humana el cliente NO esta finalizado
    assert db_row(sf, Client, id=client_id).service_status == "prospecto"

    done = w["api"].post(f"/api/v1/client-onboardings/{onboarding['id']}/complete", json={
        "accepted_fields": ["business_name", "fiscal_address", "fiscal_address_condition"],
        "overrides": {"business_name": "EMPRESA FICTICIA M2 S.A.C. (corregida)"}, "activate": True,
        "expected_version": state["version"]})
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "COMPLETED"
    final = db_row(sf, Client, id=client_id)
    assert final.service_status == "activo" and final.business_name == "EMPRESA FICTICIA M2 S.A.C. (corregida)"
    assert final.fiscal_address_condition == "HABIDO" and final.main_economic_activity is None  # solo lo aprobado
    assert db_row(sf, ClientFichaRucSnapshot, id=snap.id).review_status == "ACCEPTED"
    assert db_row(sf, ClientSolCredential, client_id=client_id).status != "DISABLED"  # el alta finalizada conserva la credencial
    assert w["agent"].report_status(job_id, "COMPLETED", session_id=sid).status_code == 200  # el humano cierra el navegador


def test_onboarding_cancel_disables_credential_and_cancels_session_and_is_retryable(world, running_broker):
    build, _mk, sf = world
    w = build(with_credential=False)
    ruc = fake_ruc()
    onboarding = _start_onboarding(w, ruc).json()
    client_id = uuid.UUID(onboarding["client_id"])
    _store_credential(w, client_id)
    started = start_destination(w, client_id, "FICHA_RUC", extract=True).json()
    sid, job_id, _ = drive_to_credential(w, started)
    cancelled = w["api"].post(f"/api/v1/client-onboardings/{onboarding['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
    assert w["api"].post(f"/api/v1/client-onboardings/{onboarding['id']}/cancel").json()["status"] == "CANCELLED"  # idempotente
    assert db_row(sf, SolAccessSession, id=sid).status == "CANCELLED"
    assert db_row(sf, ClientSolCredential, client_id=client_id).status == "DISABLED"  # sin credencial viva huerfana
    assert db_row(sf, Client, id=client_id).service_status == "inactivo"
    # completar una alta cancelada es imposible
    assert w["api"].post(f"/api/v1/client-onboardings/{onboarding['id']}/complete", json={
        "accepted_fields": ["business_name"], "expected_version": 1}).status_code == 409
    # reintento seguro con el mismo RUC: se reutiliza el cliente provisional
    again = _start_onboarding(w, ruc)
    assert again.status_code == 201 and again.json()["client_id"] == onboarding["client_id"] and again.json()["id"] != onboarding["id"]
    assert db_row(sf, Client, id=client_id).service_status == "prospecto"


def test_onboarding_rejects_existing_clients_invalid_rucs_and_unsupported_prefixes(world, running_broker):
    build, _mk, sf = world
    w = build()
    assert _start_onboarding(w, client_ruc(sf, w["client_id"])).status_code == 409  # cliente real existente
    assert _start_onboarding(w, "20123456780").status_code == 422  # digito verificador invalido
    assert w["api"].post("/api/v1/client-onboardings", json={"ruc": "abc"}).status_code == 422
    assert w["api"].post("/api/v1/client-onboardings", json={"ruc": fake_ruc(), "password": "x"}).status_code == 422  # sin secretos
    assert _start_onboarding(w, "15" + fake_ruc()[2:10] + "00").status_code == 422


def test_onboarding_rbac_and_tenant_isolation(world, running_broker):
    build, mk, sf = world
    w = build(with_credential=False)
    other = build(with_agent=False, with_credential=False)
    asistente = mk(w["tenant_id"], add_user(sf, w["tenant_id"], "asistente"))
    assert _start_onboarding(w, fake_ruc(), api=asistente).status_code == 403
    onboarding = _start_onboarding(w, fake_ruc()).json()
    assert other["api"].get(f"/api/v1/client-onboardings/{onboarding['id']}").status_code == 404
    assert other["api"].post(f"/api/v1/client-onboardings/{onboarding['id']}/cancel").status_code == 404
    assert asistente.post(f"/api/v1/client-onboardings/{onboarding['id']}/cancel").status_code == 403
    contador = mk(w["tenant_id"], add_user(sf, w["tenant_id"], "contador"))
    assert _start_onboarding(w, fake_ruc(), api=contador).status_code == 201


def test_rejecting_the_onboarding_proposal_returns_it_to_in_progress(world, running_broker):
    build, _mk, sf = world
    w = build(with_credential=False)
    ruc = fake_ruc()
    onboarding = _start_onboarding(w, ruc).json()
    client_id = uuid.UUID(onboarding["client_id"])
    _store_credential(w, client_id)
    sid, job_id, _ = drive_to_credential(w, start_destination(w, client_id, "FICHA_RUC", extract=True).json())
    assert w["agent"].report_ficha(job_id, ficha_fields(ruc), session_id=sid).json()["accepted"] is True
    snap = db_row(sf, ClientFichaRucSnapshot, session_id=sid)
    r = w["api"].post(f"/api/v1/clients/{client_id}/ficha-ruc/snapshots/{snap.id}/reject", json={"expected_version": snap.version})
    assert r.status_code == 200
    assert w["api"].get(f"/api/v1/client-onboardings/{onboarding['id']}").json()["status"] == "IN_PROGRESS"


# -------------------------------------------------------------------------------------------- higiene de secretos


def test_audit_trail_has_the_events_and_never_credentials_or_ficha_values(world, running_broker):
    build, _mk, sf = world
    w = build(with_credential=False)
    ruc = fake_ruc()
    onboarding = _start_onboarding(w, ruc).json()
    client_id = uuid.UUID(onboarding["client_id"])
    _store_credential(w, client_id)
    sid, job_id, _ = drive_to_credential(w, start_destination(w, client_id, "FICHA_RUC", extract=True).json())
    assert w["agent"].report_ficha(job_id, ficha_fields(ruc), session_id=sid).json()["accepted"] is True
    assert w["agent"].report_status(job_id, "HUMAN_CONTROL", session_id=sid).status_code == 200
    state = w["api"].get(f"/api/v1/client-onboardings/{onboarding['id']}").json()
    assert w["api"].post(f"/api/v1/client-onboardings/{onboarding['id']}/complete", json={
        "accepted_fields": ["business_name"], "expected_version": state["version"]}).status_code == 200
    s = sf()
    try:
        rows = s.execute(select(AuditLog).where(AuditLog.tenant_id == w["tenant_id"])).scalars().all()
        actions = {r.action for r in rows}
        for expected in ("SUNAT_DESTINATION_REQUESTED", "SOL_HUMAN_CONTROL_STARTED", "FICHA_RUC_RETRIEVED", "FICHA_PROPOSAL_CREATED",
                         "FICHA_PROPOSAL_ACCEPTED", "ONBOARDING_STARTED", "ONBOARDING_COMPLETED"):
            assert expected in actions, expected
        blob = json.dumps([r.log_metadata for r in rows], default=str)
        for secret in (TEST_SOL_USER, TEST_SOL_PASSWORD_DO_NOT_USE, _VAULT_PASSWORD, *FICHA_TEXT_SECRETS):
            assert secret not in blob
        # el proposito de la sesion queda trazado sin valores
        created = [r for r in rows if r.action == "FICHA_PROPOSAL_CREATED"][0]
        assert "changed_fields" in json.dumps(created.log_metadata, default=str)
    finally:
        s.close()


def test_api_responses_never_carry_secrets_or_raw_html(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid, _job, snap = _ficha_proposal(w, sf)
    bodies = [
        w["api"].get(f"/api/v1/clients/{w['client_id']}/ficha-ruc/snapshots").text,
        w["api"].get(f"/api/v1/clients/{w['client_id']}/ficha-ruc/snapshots/{snap.id}").text,
        w["api"].get(f"/api/v1/sol-access/sessions/{sid}").text,
    ]
    for body in bodies:
        for secret in (TEST_SOL_USER, TEST_SOL_PASSWORD_DO_NOT_USE, _VAULT_PASSWORD):
            assert secret not in body
        assert "<html" not in body.lower() and "<table" not in body.lower()
    keys = set(json.loads(bodies[1]))
    assert keys == {"id", "client_id", "session_id", "purpose", "source", "schema_version", "retrieved_at", "review_status", "fields",
                    "malformed", "diff", "accepted_fields", "reviewed_at", "version"}
