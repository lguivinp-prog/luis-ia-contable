"""M3: perfiles de credencial SOL (varias credenciales cifradas por cliente: principal y secundarias con autorizaciones distintas). Broker REAL, mTLS
REAL, Boveda REAL, PostgreSQL REAL; datos FICTICIOS; sin SUNAT. Verifica: conservacion de la credencial secundaria existente, aislamiento
RBAC/tenant, seleccion EXPLICITA, ausencia de reintentos con otra credencial, auditoria sin secretos, revocacion de un perfil sin tocar los demas y
los CUATRO estados (reconocido / habilitado / permitido / completado) que nunca son equivalentes."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.client_sol_credential import ClientSolCredential
from app.models.sol_agent_access import SolAccessSession, SolAgentJob
from tests.integration.broker.test_m2_destination_flow import add_user, db_row, drive_to_credential, start_destination, world  # noqa: F401
from tests.integration.broker.test_sol_access_api_flow import TEST_SOL_PASSWORD_DO_NOT_USE, TEST_SOL_USER

SECOND_USER, SECOND_PASSWORD = "SEGUNDOUSR9", "Segunda-Clave-Ficticia-9"
PRIMARY_USER, PRIMARY_PASSWORD = "PRINCIPALUSR8", "Principal-Clave-Ficticia-8"


def add_profile(w, kind="SECONDARY", name=None, user=SECOND_USER, password=SECOND_PASSWORD, api=None):
    body = {"credential_kind": kind, "sol_username": user, "sol_password": password, "authorization_basis": "Autorizacion de prueba M3"}
    if name is not None:
        body["profile_name"] = name
    return (api or w["api"]).post(f"/api/v1/clients/{w['client_id']}/sol-credentials", json=body)


def rows(sf, tenant_id):
    s = sf()
    try:
        out = list(s.execute(select(ClientSolCredential).where(ClientSolCredential.tenant_id == tenant_id)).scalars())
        for r in out:
            s.expunge(r)
        return out
    finally:
        s.close()


def start_with(w, destination, credential_id=None, api=None):
    body = {"client_id": str(w["client_id"]), "idempotency_key": str(uuid.uuid4()), "destination": destination}
    if credential_id is not None:
        body["credential_id"] = str(credential_id)
    return (api or w["api"]).post("/api/v1/sol-access/sessions", json=body)


def test_existing_secondary_credential_is_preserved_when_profiles_are_added(world, running_broker):
    build, _mk, sf = world
    w = build()
    before = rows(sf, w["tenant_id"])
    assert len(before) == 1 and before[0].profile_name == "Credencial secundaria" and before[0].credential_kind == "SECONDARY"
    snapshot = (before[0].id, bytes(before[0].sol_username_encrypted), bytes(before[0].sol_password_encrypted), before[0].version, before[0].status)
    created = add_profile(w, name="Contador delegado")
    assert created.status_code == 200, created.text
    assert created.json()["profile_name"] == "Contador delegado" and created.json()["credential_kind"] == "SECONDARY"
    after = {r.id: r for r in rows(sf, w["tenant_id"])}
    kept = after[snapshot[0]]
    assert (kept.id, bytes(kept.sol_username_encrypted), bytes(kept.sol_password_encrypted), kept.version, kept.status) == snapshot  # intacta
    assert len(after) == 2
    listed = w["api"].get(f"/api/v1/clients/{w['client_id']}/sol-credentials").json()
    assert {c["profile_name"] for c in listed} == {"Credencial secundaria", "Contador delegado"}
    body = w["api"].get(f"/api/v1/clients/{w['client_id']}/sol-credentials").text
    for secret in (TEST_SOL_USER, TEST_SOL_PASSWORD_DO_NOT_USE, SECOND_USER, SECOND_PASSWORD):
        assert secret not in body  # la API jamas devuelve el secreto


def test_profile_names_are_unique_and_only_one_primary_credential_is_allowed(world, running_broker):
    build, _mk, sf = world
    w = build()
    assert add_profile(w, name="Contador delegado").status_code == 200
    dup = add_profile(w, name="  contador DELEGADO ", user="OTROUSR77")
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "SOL_CREDENTIAL_PROFILE_NAME_TAKEN"
    first = add_profile(w, kind="PRIMARY_OWN_RUC", name="Principal", user=PRIMARY_USER, password=PRIMARY_PASSWORD)
    assert first.status_code == 200 and first.json()["credential_kind"] == "PRIMARY_OWN_RUC"
    second = add_profile(w, kind="PRIMARY_EXCEPTIONAL", name="Otra principal", user="OTRAPRIN66")
    assert second.status_code == 409 and second.json()["detail"]["code"] == "SOL_PRIMARY_CREDENTIAL_ALREADY_EXISTS"
    assert len(rows(sf, w["tenant_id"])) == 3


def test_session_requires_explicit_credential_selection_when_there_are_several_profiles(world, running_broker):
    build, _mk, sf = world
    w = build()
    # con UN perfil nada cambia respecto a M2 (compatibilidad): no hace falta credential_id
    one = start_with(w, "MENU_SOL")
    assert one.status_code == 201 and one.json()["credential_id"]
    cancelled = w["api"].post(f"/api/v1/sol-access/sessions/{one.json()['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
    assert add_profile(w, name="Contador delegado").status_code == 200
    ambiguous = start_with(w, "MENU_SOL")
    assert ambiguous.status_code == 409 and ambiguous.json()["detail"]["code"] == "SOL_CREDENTIAL_SELECTION_REQUIRED"
    unknown = start_with(w, "MENU_SOL", credential_id=uuid.uuid4())
    assert unknown.status_code == 404 and unknown.json()["detail"]["code"] == "SOL_CREDENTIAL_NOT_FOUND"


def test_session_uses_exactly_the_selected_profile_audits_it_and_never_retries_with_another(world, running_broker):
    build, _mk, sf = world
    w = build()
    original = rows(sf, w["tenant_id"])[0]
    second = add_profile(w, name="Contador delegado").json()
    r = start_with(w, "MENU_SOL", credential_id=second["id"])
    assert r.status_code == 201, r.text
    assert r.json()["credential_id"] == second["id"]
    sid, job_id, _adv = drive_to_credential(w, r.json())
    assert w["agent"].report_status(job_id, "FAILED", reason_code="PORTAL_CHANGED", session_id=sid, diagnostic_code="DESTINATION_ENTRY_NOT_FOUND").status_code in {200, 409}
    s = sf()
    try:
        sessions = list(s.execute(select(SolAccessSession).where(SolAccessSession.tenant_id == w["tenant_id"])).scalars())
        jobs = list(s.execute(select(SolAgentJob).where(SolAgentJob.tenant_id == w["tenant_id"])).scalars())
        assert len(sessions) == 1 and str(sessions[0].credential_id) == second["id"] and str(sessions[0].credential_id) != str(original.id)
        assert len(jobs) == 1  # ni siquiera tras el fallo se crea un intento con la otra credencial (ni mas privilegiada)
        events = [a for a in s.execute(select(AuditLog).where(AuditLog.tenant_id == w["tenant_id"])).scalars() if a.action in {"SOL_SESSION_CREATED", "SUNAT_DESTINATION_REQUESTED"}]
        assert events and all(e.log_metadata.get("credential_profile") == "Contador delegado" for e in events)
        dumped = str([e.log_metadata for e in events])
        for secret in (TEST_SOL_USER, TEST_SOL_PASSWORD_DO_NOT_USE, SECOND_USER, SECOND_PASSWORD):
            assert secret not in dumped
    finally:
        s.close()
    assert w["agent"].jobs_next().json()["job_id"] is None


def test_disabling_one_profile_does_not_corrupt_the_others_and_a_disabled_profile_cannot_start(world, running_broker):
    build, _mk, sf = world
    w = build()
    base = rows(sf, w["tenant_id"])[0]
    second = add_profile(w, name="Contador delegado").json()
    keep = (bytes(base.sol_username_encrypted), bytes(base.sol_password_encrypted), base.version, base.status)
    off = w["api"].post(f"/api/v1/clients/{w['client_id']}/sol-credentials/{second['id']}/disable", json={"expected_version": second["version"]})
    assert off.status_code == 200 and off.json()["status"] == "DISABLED"
    after = {r.id: r for r in rows(sf, w["tenant_id"])}
    assert (bytes(after[base.id].sol_username_encrypted), bytes(after[base.id].sol_password_encrypted), after[base.id].version, after[base.id].status) == keep
    gone = start_with(w, "MENU_SOL", credential_id=second["id"])
    assert gone.status_code == 404  # un perfil deshabilitado no inicia accesos
    ok = start_with(w, "MENU_SOL")  # con un solo perfil activo vuelve la compatibilidad M2
    assert ok.status_code == 201 and ok.json()["credential_id"] == str(base.id)
    again = w["api"].post(f"/api/v1/clients/{w['client_id']}/sol-credentials/{second['id']}/disable", json={"expected_version": second["version"]})
    assert again.status_code == 404  # ya no figura entre los perfiles activos


def test_legacy_single_credential_routes_refuse_to_choose_for_the_user(world, running_broker):
    build, _mk, sf = world
    w = build()
    assert add_profile(w, name="Contador delegado").status_code == 200
    legacy = w["api"].put(f"/api/v1/clients/{w['client_id']}/sol-credential", json={"expected_version": 1, "sol_password": "Nueva-Clave-Ficticia-1"})
    assert legacy.status_code == 409 and legacy.json()["detail"]["code"] == "SOL_CREDENTIAL_SELECTION_REQUIRED"
    legacy_off = w["api"].post(f"/api/v1/clients/{w['client_id']}/sol-credential/disable", json={"expected_version": 1})
    assert legacy_off.status_code == 409 and legacy_off.json()["detail"]["code"] == "SOL_CREDENTIAL_SELECTION_REQUIRED"
    assert w["api"].get(f"/api/v1/clients/{w['client_id']}/sol-credential/status").json()["profile_name"] == "Credencial secundaria"  # el original (mas antiguo)


def test_rbac_csrf_and_tenant_isolation_for_profiles(world, running_broker):
    build, mk, sf = world
    w = build()
    other = build(with_agent=False, with_credential=False)
    asistente = mk(w["tenant_id"], add_user(sf, w["tenant_id"], "asistente"))
    assert add_profile(w, api=asistente).status_code == 403  # sin sol_credential:create
    assert asistente.get(f"/api/v1/clients/{w['client_id']}/sol-credentials").status_code == 403
    assert other["api"].get(f"/api/v1/clients/{w['client_id']}/sol-credentials").status_code == 404  # otro tenant no ve el cliente
    no_csrf = mk(w["tenant_id"], w["admin_id"])
    no_csrf.headers.pop("X-CSRF-Token", None)
    assert add_profile(w, api=no_csrf, name="Sin csrf").status_code == 403
    assert len(rows(sf, w["tenant_id"])) == 1
    foreign = start_with(other, "MENU_SOL", credential_id=rows(sf, w["tenant_id"])[0].id, api=other["api"])
    assert foreign.status_code in {404, 409}  # el perfil de otro tenant/cliente nunca sirve


def test_the_four_states_are_reported_separately_and_never_inferred_across_profiles(world, running_broker):
    build, _mk, sf = world
    w = build()
    second = add_profile(w, name="Contador delegado").json()
    first_id = str(rows(sf, w["tenant_id"])[0].id)
    url = f"/api/v1/sol-access/clients/{w['client_id']}/credential-destinations"

    def by_profile():
        body = w["api"].get(url).json()["credentials"]
        return {c["credential_id"]: {d["destination"]: d for d in c["destinations"]} for c in body}

    state = by_profile()
    assert set(state) == {first_id, second["id"]}
    for cid in state:
        assert set(state[cid]) == {"MENU_SOL", "FICHA_RUC", "SIRE_RVIE", "SIRE_RCE", "SIRE_GENERACION_REGISTROS", "DECLARACIONES_Y_PAGOS"}  # solo las ofrecidas
        assert all(d["permitted"] == "UNKNOWN" and d["completed"] is False for d in state[cid].values())  # nada observado todavia
    sire = state[first_id]["SIRE_RVIE"]
    assert (sire["recognized"], sire["enabled"], sire["permitted"], sire["completed"]) == ("LIVE_ACCEPTED", True, "UNKNOWN", False)  # 4 estados distintos
    assert (sire["proven"], sire["available"]) == (False, False)  # estructura validada != este perfil autorizado
    assert state[first_id]["MENU_SOL"]["enabled"] is True and state[first_id]["MENU_SOL"]["permitted"] == "UNKNOWN"  # habilitado != permitido
    # el agente completa Menu SOL con el perfil ORIGINAL
    r = start_with(w, "MENU_SOL", credential_id=first_id)
    sid, job_id, _ = drive_to_credential(w, r.json())
    assert w["agent"].report_status(job_id, "HUMAN_CONTROL", session_id=sid).status_code == 200
    assert w["agent"].report_status(job_id, "COMPLETED", session_id=sid).status_code == 200
    state = by_profile()
    assert state[first_id]["MENU_SOL"]["permitted"] == "OBSERVED_ALLOWED" and state[first_id]["MENU_SOL"]["completed"] is True
    assert state[second["id"]]["MENU_SOL"]["permitted"] == "UNKNOWN" and state[second["id"]]["MENU_SOL"]["completed"] is False  # NO se extiende al otro perfil
    assert state[first_id]["FICHA_RUC"]["permitted"] == "UNKNOWN"  # ni a otro destino


def test_entry_not_found_is_observed_only_for_the_profile_that_failed(world, running_broker):
    """Ausencia de la entrada en el menu de UNA cuenta: se registra solo para ese perfil (sin afirmar 'no autorizado' ni extenderlo a otros)."""
    build, _mk, sf = world
    w = build()
    second = add_profile(w, name="Contador delegado").json()
    first_id = str(rows(sf, w["tenant_id"])[0].id)
    r = start_with(w, "FICHA_RUC", credential_id=second["id"])
    assert r.status_code == 201, r.text
    sid, job_id, _ = drive_to_credential(w, r.json())
    assert w["agent"].report_status(job_id, "FAILED", reason_code="PORTAL_CHANGED", session_id=sid, diagnostic_code="DESTINATION_ENTRY_NOT_FOUND").status_code == 200
    body = w["api"].get(f"/api/v1/sol-access/clients/{w['client_id']}/credential-destinations").json()["credentials"]
    by = {c["credential_id"]: {d["destination"]: d for d in c["destinations"]} for c in body}
    assert by[second["id"]]["FICHA_RUC"]["permitted"] == "OBSERVED_NOT_IN_MENU" and by[second["id"]]["FICHA_RUC"]["completed"] is False
    assert by[first_id]["FICHA_RUC"]["permitted"] == "UNKNOWN"
    # tras la denegacion NO se abre ningun intento automatico con la otra credencial
    assert len(list(sf().execute(select(SolAccessSession).where(SolAccessSession.tenant_id == w["tenant_id"])).scalars())) == 1
