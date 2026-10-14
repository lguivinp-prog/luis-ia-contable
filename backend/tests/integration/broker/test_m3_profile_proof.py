"""M3: la ESTRUCTURA de RVIE, RCE y Declaraciones y Pagos esta validada en el catalogo, pero la autorizacion de una credencial NO se extrapola: cada perfil de
cada cliente necesita su propia aceptacion real (PASS) o una autorizacion explicita del propietario, y jamas se prueba otra credencial automaticamente.
Broker REAL, mTLS REAL, Boveda REAL, PostgreSQL REAL; datos FICTICIOS; sin SUNAT."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.client_sol_credential import ClientSolCredential
from app.models.sol_agent_access import SolAccessSession, SolAgentJob
from tests.integration.broker.test_m2_destination_flow import add_user, db_row, drive_to_credential, world  # noqa: F401
from tests.integration.broker.test_m3_acceptance_gate import _credential_id, _grant, _start, prove_profile
from tests.integration.broker.test_m3_credential_profiles import SECOND_PASSWORD, SECOND_USER, add_profile


def _states(w, client_id=None):
    body = w["api"].get(f"/api/v1/sol-access/clients/{client_id or w['client_id']}/credential-destinations").json()["credentials"]
    return {c["credential_id"]: {d["destination"]: d for d in c["destinations"]} for c in body}


def test_a_proven_profile_opens_the_destination_directly_and_an_unproven_sibling_profile_cannot(world, running_broker):
    build, _mk, sf = world
    w = build()
    first = prove_profile(w, sf, "SIRE_RVIE")  # perfil original probado de punta a punta
    second = add_profile(w, kind="PRIMARY_OWN_RUC", name="Principal", user=SECOND_USER, password=SECOND_PASSWORD).json()
    state = _states(w)
    assert (state[str(first)]["SIRE_RVIE"]["proven"], state[str(first)]["SIRE_RVIE"]["available"]) == (True, True)
    assert (state[second["id"]]["SIRE_RVIE"]["proven"], state[second["id"]]["SIRE_RVIE"]["available"]) == (False, False)  # no se extrapola entre perfiles
    assert state[str(first)]["SIRE_RCE"]["available"] is False  # ni entre destinos
    # el perfil probado abre SIN autorizacion adicional; el hermano, no (ni siquiera siendo administrador)
    assert _start(w["api"], w["client_id"], first).status_code == 201
    denied = _start(w["api"], w["client_id"], uuid.UUID(second["id"]))
    assert denied.status_code in {409, 422}  # 409 si la sesion del primero sigue activa; 422 si no
    assert denied.status_code == 409 or denied.json()["detail"]["code"] == "SOL_PROFILE_NOT_PROVEN"


def test_an_unproven_profile_needs_an_explicit_owner_authorization_and_becomes_proven_only_after_its_own_pass(world, running_broker):
    build, _mk, sf = world
    w = build()
    first = prove_profile(w, sf, "SIRE_RVIE")
    second = uuid.UUID(add_profile(w, kind="PRIMARY_OWN_RUC", name="Principal", user=SECOND_USER, password=SECOND_PASSWORD).json()["id"])
    refused = _start(w["api"], w["client_id"], second)
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "SOL_PROFILE_NOT_PROVEN"
    s = sf()
    try:
        assert not [x for x in s.execute(select(SolAccessSession).where(SolAccessSession.credential_id == second)).scalars()]  # ninguna sesion para el perfil no probado
    finally:
        s.close()
    assert _states(w)[str(second)]["SIRE_RVIE"]["available"] is False
    prove_profile(w, sf, "SIRE_RVIE", credential_id=second)  # su PROPIA prueba real
    state = _states(w)
    assert state[str(second)]["SIRE_RVIE"]["proven"] is True and state[str(second)]["SIRE_RCE"]["proven"] is False
    assert _grant(w["api"], w["client_id"], second, "SIRE_RVIE").status_code == 409  # ya probado: no necesita otra autorizacion
    assert str(first) != str(second)


def test_a_failed_attempt_never_proves_a_profile_and_a_later_pass_does_without_erasing_the_history(world, running_broker):
    build, _mk, sf = world
    w = build()
    cid = _credential_id(sf, w["tenant_id"])
    g1 = _grant(w["api"], w["client_id"], cid, "DECLARACIONES_Y_PAGOS").json()
    sid, job_id, _a = drive_to_credential(w, _start(w["api"], w["client_id"], cid, "DECLARACIONES_Y_PAGOS").json())
    assert w["agent"].report_status(job_id, "FAILED", reason_code="PORTAL_CHANGED", session_id=sid, diagnostic_code="AUTH_NAVIGATION_STALLED").status_code == 200
    rows = w["api"].get(f"/api/v1/sol-access/clients/{w['client_id']}/acceptances").json()["acceptances"]
    assert rows[0]["phase"] == "CONFIRMED_FAIL" and rows[0]["result_source"] == "SYSTEM"
    assert _states(w)[str(cid)]["DECLARACIONES_Y_PAGOS"]["proven"] is False  # un fallo (incluso el del sistema) no prueba nada
    assert _start(w["api"], w["client_id"], cid, "DECLARACIONES_Y_PAGOS").status_code == 422
    prove_profile(w, sf, "DECLARACIONES_Y_PAGOS")  # segundo intento satisfactorio
    history = w["api"].get(f"/api/v1/sol-access/clients/{w['client_id']}/acceptances").json()["acceptances"]
    phases = [(r["phase"], r["destination"]) for r in history]
    assert phases == [("CONFIRMED_PASS", "DECLARACIONES_Y_PAGOS"), ("CONFIRMED_FAIL", "DECLARACIONES_Y_PAGOS")]  # el fallo historico se conserva, el exito vigente va primero
    assert history[1]["id"] == g1["id"] and history[1]["diagnostic_code"] == "AUTH_NAVIGATION_STALLED"
    assert _states(w)[str(cid)]["DECLARACIONES_Y_PAGOS"]["available"] is True


def test_proof_is_isolated_by_client_and_by_tenant(world, running_broker):
    build, _mk, sf = world
    w = build()
    prove_profile(w, sf, "SIRE_RCE")
    other_tenant = build(with_agent=False)
    # otro tenant: su perfil no hereda nada, y no puede ver ni usar las aceptaciones ajenas
    ot = _credential_id(sf, other_tenant["tenant_id"])
    assert _states(other_tenant, other_tenant["client_id"])[str(ot)]["SIRE_RCE"]["proven"] is False
    assert other_tenant["api"].get(f"/api/v1/sol-access/clients/{w['client_id']}/acceptances").status_code == 404
    assert _start(other_tenant["api"], other_tenant["client_id"], ot, "SIRE_RCE").status_code == 422
    # un contador no puede aprovechar la prueba de otro perfil ni una autorizacion del propietario
    contador = _mk(w["tenant_id"], add_user(sf, w["tenant_id"], "contador"))
    second = uuid.UUID(add_profile(w, kind="PRIMARY_OWN_RUC", name="Principal", user=SECOND_USER, password=SECOND_PASSWORD).json()["id"])
    assert _grant(w["api"], w["client_id"], second, "SIRE_RCE").status_code == 201
    blocked = _start(contador, w["client_id"], second, "SIRE_RCE")
    assert blocked.status_code == 422 and blocked.json()["detail"]["code"] == "SOL_PROFILE_NOT_PROVEN"  # la autorizacion es del propietario que la concedio


def test_generation_registros_and_the_historic_sire_stay_closed_for_every_profile(world, running_broker):
    build, _mk, sf = world
    w = build()
    cid = _credential_id(sf, w["tenant_id"])
    for destination in ("SIRE_GENERACION_REGISTROS", "SIRE", "DECLARA_FACIL"):
        r = _start(w["api"], w["client_id"], cid, destination)
        assert r.status_code == 422 and r.json()["detail"]["code"] == "SOL_DESTINATION_DISABLED", destination
        assert _grant(w["api"], w["client_id"], cid, destination).status_code == 422  # ni siquiera con una autorizacion del propietario
    assert db_row(sf, SolAccessSession, tenant_id=w["tenant_id"]) is None
    s = sf()
    try:
        assert len(list(s.execute(select(ClientSolCredential).where(ClientSolCredential.tenant_id == w["tenant_id"])).scalars())) == 1
    finally:
        s.close()
