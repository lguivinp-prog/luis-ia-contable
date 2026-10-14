"""M3: aceptacion real controlada. Broker REAL, mTLS REAL, boveda REAL, PostgreSQL REAL; datos FICTICIOS; sin SUNAT.
Un destino RECON_COMPLETE sigue DESHABILITADO para todos; solo el propietario puede autorizar UNA prueba para UN cliente, UN perfil y UN destino."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from app.core import sunat_destinations as sd
from app.models.audit_log import AuditLog
from app.models.client_sol_credential import ClientSolCredential
from app.models.sol_agent_access import SolAccessSession, SolAgentJob
from app.models.sol_destination_acceptance import SolDestinationAcceptance
from tests.integration.broker.test_m2_destination_flow import (  # noqa: F401 -- fixtures y ayudantes reutilizados
    add_user,
    db_row,
    drive_to_credential,
    world,
)


def _credential_id(sf, tenant_id) -> uuid.UUID:
    s = sf()
    try:
        return s.execute(select(ClientSolCredential.id).where(ClientSolCredential.tenant_id == tenant_id)).scalars().first()
    finally:
        s.close()


def _grant(api, client_id, credential_id, destination="SIRE_RVIE"):
    return api.post("/api/v1/sol-access/acceptances", json={
        "client_id": str(client_id), "credential_id": str(credential_id), "destination": destination, "confirm_real_access": True})


def _start(api, client_id, credential_id, destination="SIRE_RVIE", key=None):
    return api.post("/api/v1/sol-access/sessions", json={
        "client_id": str(client_id), "credential_id": str(credential_id), "idempotency_key": str(key or uuid.uuid4()), "destination": destination})


def prove_profile(w, sf, destination, credential_id=None):
    """Prueba REAL (flujo de producto, sin atajos de BD) de un perfil para un destino: autorizacion -> sesion -> llegada -> control humano -> PASS."""
    cid = credential_id or _credential_id(sf, w["tenant_id"])
    g = _grant(w["api"], w["client_id"], cid, destination)
    assert g.status_code == 201, g.text
    r = _start(w["api"], w["client_id"], cid, destination)
    assert r.status_code == 201, r.text
    sid, job_id, _advert = drive_to_credential(w, r.json())
    assert w["agent"].report_status(job_id, "HUMAN_CONTROL", session_id=sid).status_code == 200
    assert w["agent"].report_status(job_id, "COMPLETED", session_id=sid).status_code == 200
    ok = w["api"].post(f"/api/v1/sol-access/acceptances/{g.json()['id']}/result", json={"result": "PASS"})
    assert ok.status_code == 200, ok.text
    return cid


def _audit(sf, action, tenant_id=None):
    s = sf()
    try:
        query = select(AuditLog) if tenant_id is None else select(AuditLog).where(AuditLog.tenant_id == tenant_id)
        return [a for a in s.execute(query).scalars() if a.action == action]
    finally:
        s.close()


def test_without_a_proof_or_an_authorization_an_unproven_profile_cannot_open_the_destination(world, running_broker):
    build, _mk, sf = world
    w = build()
    cid = _credential_id(sf, w["tenant_id"])
    r = _start(w["api"], w["client_id"], cid)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "SOL_PROFILE_NOT_PROVEN"  # la estructura esta validada, pero NO este perfil
    assert db_row(sf, SolAccessSession, tenant_id=w["tenant_id"]) is None and db_row(sf, SolAgentJob, tenant_id=w["tenant_id"]) is None
    assert sd.enabled_ids() == ["MENU_SOL", "FICHA_RUC", "SIRE_RVIE", "SIRE_RCE", "DECLARACIONES_Y_PAGOS"]


def test_owner_authorization_allows_exactly_one_real_test_for_that_client_profile_and_destination(world, running_broker):
    build, _mk, sf = world
    w = build()
    cid = _credential_id(sf, w["tenant_id"])
    g = _grant(w["api"], w["client_id"], cid)
    assert g.status_code == 201, g.text
    assert g.json()["status"] == "AUTHORIZED" and g.json()["destination"] == "SIRE_RVIE"
    assert _grant(w["api"], w["client_id"], cid).json()["id"] == g.json()["id"]  # idempotente: no duplica
    # otro destino NO queda autorizado por la autorizacion de RVIE
    other = _start(w["api"], w["client_id"], cid, "SIRE_RCE")
    assert other.status_code == 422 and other.json()["detail"]["code"] == "SOL_PROFILE_NOT_PROVEN"
    key = uuid.uuid4()
    r = _start(w["api"], w["client_id"], cid, key=key)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["destination"] == "SIRE_RVIE" and body["acceptance_test"] is True
    sid, job_id, advert = drive_to_credential(w, body)
    assert advert["destination"] == "SIRE_RVIE" and advert["acceptance"] is True  # el agente recibe la marca desde el servidor
    assert "url" not in advert and "selector" not in advert
    # consumida: un solo uso; el reintento IDEMPOTENTE de la misma sesion sigue funcionando y no abre otra
    assert _start(w["api"], w["client_id"], cid, key=key).status_code == 200
    assert w["agent"].report_status(job_id, "HUMAN_CONTROL", session_id=sid).status_code == 200
    assert w["agent"].report_status(job_id, "COMPLETED", session_id=sid).status_code == 200
    # sin PASS el perfil sigue sin prueba: la autorizacion consumida no se reutiliza
    again = _start(w["api"], w["client_id"], cid)
    assert again.status_code == 422 and again.json()["detail"]["code"] == "SOL_PROFILE_NOT_PROVEN"
    listed = w["api"].get(f"/api/v1/sol-access/clients/{w['client_id']}/acceptances").json()["acceptances"]
    assert listed[0]["status"] == "CONSUMED" and listed[0]["session_id"] == str(sid) and listed[0]["result"] is None
    # PASS solo con la llegada real; el propietario lo confirma
    ok = w["api"].post(f"/api/v1/sol-access/acceptances/{g.json()['id']}/result", json={"result": "PASS", "note": "pantalla oficial RVIE verificada"})
    assert ok.status_code == 200 and ok.json()["result"] == "PASS"
    assert w["api"].post(f"/api/v1/sol-access/acceptances/{g.json()['id']}/result", json={"result": "FAIL"}).status_code == 409
    # el PASS prueba ESTE perfil para ESTE cliente y destino (y solo eso): el catalogo global no cambia
    proven = w["api"].get(f"/api/v1/sol-access/clients/{w['client_id']}/credential-destinations").json()["credentials"][0]["destinations"]
    flags = {d["destination"]: (d["proven"], d["available"]) for d in proven}
    assert flags["SIRE_RVIE"] == (True, True) and flags["SIRE_RCE"] == (False, False) and flags["DECLARACIONES_Y_PAGOS"] == (False, False)
    reopened = _start(w["api"], w["client_id"], cid)  # probado: ya no necesita autorizacion
    assert reopened.status_code == 201 and reopened.json()["acceptance_test"] is False
    tid = w["tenant_id"]
    actions = [a.action for a in _audit(sf, "SOL_ACCEPTANCE_GRANTED", tid) + _audit(sf, "SOL_ACCEPTANCE_CONSUMED", tid) + _audit(sf, "SOL_ACCEPTANCE_RESULT_RECORDED", tid)]
    assert sorted(actions) == ["SOL_ACCEPTANCE_CONSUMED", "SOL_ACCEPTANCE_GRANTED", "SOL_ACCEPTANCE_RESULT_RECORDED"]
    assert all("password" not in str(a.log_metadata).lower() for a in _audit(sf, "SOL_ACCEPTANCE_GRANTED", tid))


def test_pass_requires_a_real_arrival_and_fail_can_always_be_recorded(world, running_broker):
    build, _mk, sf = world
    w = build()
    cid = _credential_id(sf, w["tenant_id"])
    g = _grant(w["api"], w["client_id"], cid).json()
    early = w["api"].post(f"/api/v1/sol-access/acceptances/{g['id']}/result", json={"result": "PASS"})
    assert early.status_code == 409 and early.json()["detail"]["code"] == "ACCEPTANCE_NOT_USED"
    r = _start(w["api"], w["client_id"], cid)
    sid, job_id, _advert = drive_to_credential(w, r.json())
    assert w["agent"].report_status(job_id, "FAILED", session_id=sid, reason_code="PORTAL_CHANGED").status_code == 200
    bad = w["api"].post(f"/api/v1/sol-access/acceptances/{g['id']}/result", json={"result": "PASS"})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "ACCEPTANCE_PASS_REQUIRES_REACHED_DESTINATION"
    fail = w["api"].post(f"/api/v1/sol-access/acceptances/{g['id']}/result", json={"result": "FAIL", "note": "no llego"})
    assert fail.status_code == 200 and fail.json()["result"] == "FAIL"


def test_only_the_owner_administrator_can_authorize_and_only_for_eligible_destinations(world, running_broker):
    build, mk, sf = world
    w = build()
    cid = _credential_id(sf, w["tenant_id"])
    contador = mk(w["tenant_id"], add_user(sf, w["tenant_id"], "contador"))
    assert _grant(contador, w["client_id"], cid).status_code == 403
    assert contador.get(f"/api/v1/sol-access/clients/{w['client_id']}/acceptances").status_code == 403
    # Generacion (solo apertura), Menu SOL (ya habilitado), alias y contenido NO son elegibles
    for destination in ("SIRE_GENERACION_REGISTROS", "MENU_SOL", "FICHA_RUC", "DECLARA_FACIL", "SIRE"):
        r = _grant(w["api"], w["client_id"], cid, destination)
        assert r.status_code == 422 and r.json()["detail"]["code"] == "ACCEPTANCE_DESTINATION_NOT_ELIGIBLE", destination
    # sin la confirmacion explicita no hay autorizacion
    assert w["api"].post("/api/v1/sol-access/acceptances", json={"client_id": str(w["client_id"]), "credential_id": str(cid),
                                                                  "destination": "SIRE_RVIE", "confirm_real_access": False}).status_code == 422
    # CSRF obligatorio
    no_csrf = mk(w["tenant_id"], w["admin_id"])
    no_csrf.headers.pop("X-CSRF-Token", None)
    assert _grant(no_csrf, w["client_id"], cid).status_code == 403
    # un contador no puede consumir la autorizacion del propietario
    g = _grant(w["api"], w["client_id"], cid)
    assert g.status_code == 201
    assert _start(contador, w["client_id"], cid).status_code in {403, 422}


def test_authorization_is_isolated_by_tenant_client_and_profile(world, running_broker):
    build, _mk, sf = world
    w = build()
    other = build(with_agent=False)
    cid = _credential_id(sf, w["tenant_id"])
    other_cid = _credential_id(sf, other["tenant_id"])
    # credencial/cliente de OTRO tenant: no existe para este
    assert _grant(w["api"], w["client_id"], other_cid).status_code == 409
    assert _grant(w["api"], other["client_id"], cid).status_code == 404
    assert _grant(w["api"], w["client_id"], cid).status_code == 201
    # el otro tenant no ve ni usa la autorizacion
    assert other["api"].get(f"/api/v1/sol-access/clients/{w['client_id']}/acceptances").status_code == 404
    assert _start(other["api"], w["client_id"], cid).status_code in {404, 422}
    s = sf()
    try:
        assert s.execute(select(SolDestinationAcceptance).where(SolDestinationAcceptance.tenant_id == other["tenant_id"])).first() is None
    finally:
        s.close()


def test_revoked_or_expired_authorizations_cannot_be_used(world, running_broker):
    build, _mk, sf = world
    w = build()
    cid = _credential_id(sf, w["tenant_id"])
    g = _grant(w["api"], w["client_id"], cid).json()
    rv = w["api"].post(f"/api/v1/sol-access/acceptances/{g['id']}/revoke")
    assert rv.status_code == 200 and rv.json()["status"] == "REVOKED"
    assert w["api"].post(f"/api/v1/sol-access/acceptances/{g['id']}/revoke").status_code == 409
    assert _start(w["api"], w["client_id"], cid).status_code == 422
    g2 = _grant(w["api"], w["client_id"], cid).json()
    assert g2["id"] != g["id"]
    s = sf()
    try:
        s.execute(text("UPDATE sol_destination_acceptances SET expires_at = now() - interval '1 minute' WHERE id = :i"), {"i": g2["id"]})
        s.commit()
    finally:
        s.close()
    assert _start(w["api"], w["client_id"], cid).status_code == 422  # vencida
    assert w["api"].get(f"/api/v1/sol-access/clients/{w['client_id']}/acceptances").json()["acceptances"][0]["status"] == "EXPIRED"


def test_concurrent_consumption_creates_a_single_session(world, running_broker):
    import threading

    build, _mk, sf = world
    w = build()
    cid = _credential_id(sf, w["tenant_id"])
    assert _grant(w["api"], w["client_id"], cid).status_code == 201
    codes: list[int] = []

    def go() -> None:
        codes.append(_start(w["api"], w["client_id"], cid).status_code)

    threads = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert codes.count(201) == 1, codes
    s = sf()
    try:
        assert len(list(s.execute(select(SolAccessSession).where(SolAccessSession.tenant_id == w["tenant_id"])).scalars())) == 1
        assert len(_audit(sf, "SOL_ACCEPTANCE_CONSUMED", w["tenant_id"])) == 1
    finally:
        s.close()


def test_the_migration_adds_the_acceptance_table_with_its_constraints(world, running_broker):
    _build, _mk, sf = world
    s = sf()
    try:
        names = {r[0] for r in s.execute(text("SELECT conname FROM pg_constraint WHERE conrelid = 'sol_destination_acceptances'::regclass"))}
        assert {"ck_sol_dest_acceptance_status", "ck_sol_dest_acceptance_result_after_consumption", "ck_sol_dest_acceptance_consumption_coherence",
                "fk_sol_dest_acceptance_credential_client_tenant"} <= names
        assert s.execute(text("SELECT 1 FROM pg_indexes WHERE indexname = 'ux_sol_dest_acceptance_one_authorized'")).first() is not None
    finally:
        s.close()


def test_a_failed_session_is_settled_as_failure_with_its_diagnostic_and_can_never_become_a_pass(world, running_broker):
    """Reproduce la incidencia real de Declaraciones y Pagos: la autenticacion se estanco (AUTH_NAVIGATION_STALLED) y la sesion termino FAILED."""
    build, _mk, sf = world
    w = build()
    cid = _credential_id(sf, w["tenant_id"])
    g = _grant(w["api"], w["client_id"], cid, "DECLARACIONES_Y_PAGOS").json()
    r = _start(w["api"], w["client_id"], cid, "DECLARACIONES_Y_PAGOS")
    sid, job_id, _advert = drive_to_credential(w, r.json())
    # mientras la sesion sigue en curso no hay confirmacion posible, ni positiva ni negativa
    running = w["api"].post(f"/api/v1/sol-access/acceptances/{g['id']}/result", json={"result": "PASS"})
    assert running.status_code == 409 and running.json()["detail"]["code"] == "ACCEPTANCE_SESSION_STILL_RUNNING"
    assert w["api"].get(f"/api/v1/sol-access/clients/{w['client_id']}/acceptances").json()["acceptances"][0]["phase"] == "CONSUMED_RUNNING"
    assert w["agent"].report_status(job_id, "FAILED", reason_code="PORTAL_CHANGED", session_id=sid, diagnostic_code="AUTH_NAVIGATION_STALLED").status_code == 200
    # llamada DIRECTA a la API: una sesion fallida no puede confirmarse como correcta
    direct = w["api"].post(f"/api/v1/sol-access/acceptances/{g['id']}/result", json={"result": "PASS", "note": "intento directo"})
    assert direct.status_code == 422 and direct.json()["detail"]["code"] == "ACCEPTANCE_PASS_REQUIRES_REACHED_DESTINATION"
    # el historial distingue el fallo: el sistema lo asienta como FAIL con el diagnostico saneado
    row = w["api"].get(f"/api/v1/sol-access/clients/{w['client_id']}/acceptances").json()["acceptances"][0]
    assert row["phase"] == "CONFIRMED_FAIL" and row["result"] == "FAIL" and row["result_source"] == "SYSTEM"
    assert row["session_status"] == "FAILED" and row["diagnostic_code"] == "AUTH_NAVIGATION_STALLED" and row["session_operation_result"] is None
    assert "password" not in str(row).lower()
    # ni doble aceptacion ni reutilizacion del gate
    again = w["api"].post(f"/api/v1/sol-access/acceptances/{g['id']}/result", json={"result": "PASS"})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "ACCEPTANCE_RESULT_ALREADY_RECORDED"
    assert _start(w["api"], w["client_id"], cid, "DECLARACIONES_Y_PAGOS").status_code == 422
    # asentarlo es idempotente (una sola auditoria del sistema) y un nuevo intento exige una NUEVA autorizacion
    w["api"].get(f"/api/v1/sol-access/clients/{w['client_id']}/acceptances")
    settled = [a for a in _audit(sf, "SOL_ACCEPTANCE_RESULT_RECORDED") if (a.log_metadata or {}).get("source") == "SYSTEM"]
    assert len(settled) == 1 and settled[0].log_metadata["diagnostic_code"] == "AUTH_NAVIGATION_STALLED"
    assert _grant(w["api"], w["client_id"], cid, "DECLARACIONES_Y_PAGOS").status_code == 201


def test_history_phases_distinguish_authorization_consumption_arrival_and_human_confirmation(world, running_broker):
    build, _mk, sf = world
    w = build()
    cid = _credential_id(sf, w["tenant_id"])
    g = _grant(w["api"], w["client_id"], cid).json()
    phases = lambda: w["api"].get(f"/api/v1/sol-access/clients/{w['client_id']}/acceptances").json()["acceptances"][0]["phase"]  # noqa: E731
    assert phases() == "AUTHORIZED"
    sid, job_id, _advert = drive_to_credential(w, _start(w["api"], w["client_id"], cid).json())
    assert phases() == "CONSUMED_RUNNING"
    assert w["agent"].report_status(job_id, "HUMAN_CONTROL", session_id=sid).status_code == 200
    assert w["agent"].report_status(job_id, "COMPLETED", session_id=sid).status_code == 200
    assert phases() == "ARRIVAL_AWAITING_CONFIRMATION"
    assert w["api"].post(f"/api/v1/sol-access/acceptances/{g['id']}/result", json={"result": "PASS"}).status_code == 200
    assert phases() == "CONFIRMED_PASS"
