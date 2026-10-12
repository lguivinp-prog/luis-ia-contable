"""M2: reconciliacion de sesiones HUMAN_CONTROL huerfanas con broker REAL, mTLS REAL y PostgreSQL REAL.
Datos y credenciales FICTICIOS; sin SUNAT. El tiempo se retrocede con SQL (el reloj autoritativo es PostgreSQL)."""

from __future__ import annotations

import uuid

from sqlalchemy import text

from app.models.sol_agent_access import SolAccessSession, SolAgentJob
from tests.integration.broker.test_m2_destination_flow import db_row, drive_to_credential, start_destination
from tests.integration.broker.test_sol_access_api_flow import world  # noqa: F401 -- fixture reutilizado


def _hold_in_human_control(w):
    sid, job_id, _ = drive_to_credential(w, start_destination(w, w["client_id"], "MENU_SOL").json())
    assert w["agent"].report_status(job_id, "HUMAN_CONTROL", session_id=sid).status_code == 200
    return sid, job_id


def _age(sf, *, session_id=None, agent_seconds=None, hc_seconds=None):
    s = sf()
    try:
        if hc_seconds is not None:
            s.execute(
                text("update sol_access_sessions set human_control_at = now() - make_interval(secs => :n) where id = :i"),
                {"n": hc_seconds, "i": session_id},
            )
        if agent_seconds is not None:
            s.execute(text("update sol_agents set last_seen_at = now() - make_interval(secs => :n)"), {"n": agent_seconds})
        s.commit()
    finally:
        s.close()


def _agent_available(w) -> bool:
    return w["api"].get("/api/v1/sol-access/agents").json()["agents"][0]["available"]


def test_existing_client_reuses_the_stored_vault_credential_without_any_secret_in_the_request(world, running_broker):
    build, _mk, sf = world
    w = build()
    smuggled = w["api"].post("/api/v1/sol-access/sessions", json={
        "client_id": str(w["client_id"]), "idempotency_key": str(uuid.uuid4()), "destination": "FICHA_RUC",
        "extract_ficha": True, "sol_password": "no-debe-aceptarse"})
    assert smuggled.status_code == 422  # el contrato rechaza cualquier secreto SOL en la peticion
    started = start_destination(w, w["client_id"], "FICHA_RUC", extract=True)
    assert started.status_code == 201
    sid, job_id, _ = drive_to_credential(w, started.json())  # la credencial sale de la boveda, no de la peticion
    assert db_row(sf, SolAgentJob, id=job_id).credential_delivered_at is not None
    assert w["agent"].request_credential(job_id).status_code in {404, 409}  # un solo uso: nunca se reentrega


def test_live_agent_that_holds_its_session_is_never_released(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid, job_id = _hold_in_human_control(w)
    _age(sf, session_id=sid, hc_seconds=20 * 60)  # lleva mucho tiempo en control humano pero SIGUE sostenido
    for _ in range(2):
        assert w["agent"].heartbeat(holding_job_id=job_id).json()["cancel_notice"] is None
    assert db_row(sf, SolAccessSession, id=sid).status == "HUMAN_CONTROL"
    assert db_row(sf, SolAgentJob, id=job_id).status == "CLAIMED"
    assert _agent_available(w) is False  # legitimamente ocupado


def test_restarted_agent_that_no_longer_holds_the_session_releases_it(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid, job_id = _hold_in_human_control(w)
    _age(sf, session_id=sid, hc_seconds=60)
    assert _agent_available(w) is False
    # el proceso del agente se reinicio: nuevo HELLO y heartbeat SIN job sostenido
    assert w["agent"].hello().status_code == 200
    assert w["agent"].heartbeat().status_code == 200
    session = db_row(sf, SolAccessSession, id=sid)
    assert session.status == "FAILED" and session.terminal_reason_code == "AGENT_CRASHED"
    assert db_row(sf, SolAgentJob, id=job_id).status == "FAILED"
    assert _agent_available(w) is True  # reserva liberada
    # sin resurreccion ni nueva entrega de credencial para el job anterior
    assert w["agent"].request_credential(job_id).status_code in {404, 409}
    assert w["agent"].jobs_next().json()["job_id"] is None
    assert w["agent"].report_status(job_id, "COMPLETED", session_id=sid).status_code in {404, 409}
    # el agente puede aceptar una sesion NUEVA
    again = start_destination(w, w["client_id"], "MENU_SOL")
    assert again.status_code == 201 and w["agent"].jobs_next().json()["job_id"] is not None


def test_heartbeat_right_after_human_control_starts_does_not_race(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid, job_id = _hold_in_human_control(w)
    assert w["agent"].heartbeat().status_code == 200  # dentro del margen de carrera: no se toca
    assert db_row(sf, SolAccessSession, id=sid).status == "HUMAN_CONTROL"


def test_agent_that_vanished_is_released_after_the_bounded_lease_only(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid, job_id = _hold_in_human_control(w)
    _age(sf, session_id=sid, hc_seconds=600, agent_seconds=60)  # silencio corto: aun dentro del lease
    w["api"].get("/api/v1/sol-access/agents")
    assert db_row(sf, SolAccessSession, id=sid).status == "HUMAN_CONTROL"
    _age(sf, agent_seconds=10 * 60)  # silencio prolongado: el control humano se da por perdido
    agents = w["api"].get("/api/v1/sol-access/agents").json()["agents"]
    session = db_row(sf, SolAccessSession, id=sid)
    assert session.status == "FAILED" and session.terminal_reason_code == "AGENT_CRASHED"
    assert db_row(sf, SolAgentJob, id=job_id).status == "FAILED"
    assert agents[0]["available"] is False  # sigue sin heartbeat => offline, pero ya sin reserva obsoleta


def test_job_is_readopted_after_the_broker_lost_its_in_memory_state(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid, job_id = _hold_in_human_control(w)
    agent_id = db_row(sf, SolAgentJob, id=job_id).agent_id
    running_broker.connections.set_active_job(agent_id=agent_id, job_id=None)  # el broker perdio su estado en memoria
    assert w["agent"].heartbeat(holding_job_id=job_id).json()["cancel_notice"] is None
    assert running_broker.connections.get(agent_id=agent_id).active_job_id == job_id  # readoptado
    # y una cancelacion posterior vuelve a llegar al agente por el camino normal
    assert w["api"].post(f"/api/v1/sol-access/sessions/{sid}/cancel", json={}).status_code == 200
    assert w["agent"].heartbeat().json()["cancel_notice"]["job_id"] == str(job_id)


def test_cancelled_session_notifies_an_agent_that_still_holds_it(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid, job_id = _hold_in_human_control(w)
    cancelled = w["api"].post(f"/api/v1/sol-access/sessions/{sid}/cancel", json={})
    assert cancelled.status_code == 200
    notice = w["agent"].heartbeat(holding_job_id=job_id).json()["cancel_notice"]
    assert notice is not None and notice["job_id"] == str(job_id)  # el agente cierra el navegador
    # una sesion terminal no se revive porque el agente siga declarandola
    assert db_row(sf, SolAccessSession, id=sid).status == "CANCELLED"
    assert w["agent"].heartbeat().json()["cancel_notice"] is None
    assert uuid.UUID(notice["job_id"]) == job_id


# --------------------------------------------------------------- sesiones estancadas (ejecutor que murio sin reportar)


def _in_progress(w, extract=True):
    sid, job_id, _ = drive_to_credential(w, start_destination(w, w["client_id"], "FICHA_RUC", extract=extract).json())
    return sid, job_id


def _sql(sf, statement, **params):
    s = sf()
    try:
        s.execute(text(statement), params)
        s.commit()
    finally:
        s.close()


def test_in_progress_session_that_never_progresses_is_closed_even_if_the_agent_keeps_declaring_the_job(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid, job_id = _in_progress(w)
    _sql(sf, "update sol_access_sessions set updated_at = now() - interval '10 minutes' where id = :i", i=sid)
    # el ejecutor murio sin reportar pero el agente sigue latiendo y "sosteniendo" el job (el defecto de la aceptacion en vivo)
    heartbeat = w["agent"].heartbeat(holding_job_id=job_id)
    assert heartbeat.status_code == 200
    session = db_row(sf, SolAccessSession, id=sid)
    assert session.status == "FAILED" and session.terminal_reason_code == "JOB_EXPIRED"
    assert db_row(sf, SolAgentJob, id=job_id).status == "FAILED"
    assert _agent_available(w) is True  # reserva liberada
    # el agente recibe el aviso de cierre (suelta el job) y no hay nueva entrega de credencial ni reanimacion
    assert w["agent"].heartbeat(holding_job_id=job_id).json()["cancel_notice"]["job_id"] == str(job_id)
    assert w["agent"].request_credential(job_id).status_code in {404, 409}
    assert w["agent"].report_status(job_id, "COMPLETED", session_id=sid).status_code in {404, 409}


def test_stalled_in_progress_session_is_closed_when_the_ui_reads_it(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid, job_id = _in_progress(w)
    assert w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()["status"] == "IN_PROGRESS"  # reciente: intacta
    _sql(sf, "update sol_access_sessions set updated_at = now() - interval '10 minutes' where id = :i", i=sid)
    body = w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()
    assert body["status"] == "FAILED" and body["terminal_reason_code"] == "JOB_EXPIRED" and body["is_terminal"] is True
    assert db_row(sf, SolAgentJob, id=job_id).status == "FAILED"


def test_waiting_for_user_and_fresh_sessions_are_never_considered_stalled(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid, job_id = _in_progress(w)
    assert w["agent"].heartbeat(holding_job_id=job_id).status_code == 200  # reciente: no se toca
    assert db_row(sf, SolAccessSession, id=sid).status == "IN_PROGRESS"
    assert w["agent"].report_status(job_id, "WAITING_FOR_USER", session_id=sid).status_code == 200  # aviso de SUNAT: manda el humano
    _sql(sf, "update sol_access_sessions set updated_at = now() - interval '1 hour' where id = :i", i=sid)
    assert w["agent"].heartbeat(holding_job_id=job_id).json()["cancel_notice"] is None
    assert w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()["status"] == "WAITING_FOR_USER"
    assert db_row(sf, SolAgentJob, id=job_id).status == "CLAIMED"


def test_in_progress_orphan_is_released_when_the_agent_no_longer_holds_the_job(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid, job_id = _in_progress(w)
    assert w["agent"].heartbeat().status_code == 200  # recien reclamado: margen de carrera, no se toca
    assert db_row(sf, SolAccessSession, id=sid).status == "IN_PROGRESS"
    _sql(sf, "update sol_agent_jobs set claimed_at = now() - interval '2 minutes' where id = :i", i=job_id)
    assert w["agent"].heartbeat().status_code == 200  # agente reiniciado: ya no declara el job
    session = db_row(sf, SolAccessSession, id=sid)
    assert session.status == "FAILED" and session.terminal_reason_code == "AGENT_CRASHED"
    assert db_row(sf, SolAgentJob, id=job_id).status == "FAILED"


def test_ficha_is_accepted_after_the_human_resolved_an_intermediate_notice(world, running_broker):
    from tests.integration.broker.test_m2_destination_flow import client_ruc, ficha_fields

    build, _mk, sf = world
    w = build()
    sid, job_id = _in_progress(w)
    assert w["agent"].report_status(job_id, "WAITING_FOR_USER", session_id=sid).status_code == 200
    assert db_row(sf, SolAccessSession, id=sid).status == "WAITING_FOR_USER"
    reported = w["agent"].report_ficha(job_id, ficha_fields(client_ruc(sf, w["client_id"])), session_id=sid)
    assert reported.status_code == 200 and reported.json()["accepted"] is True  # el humano ya cerro el aviso: retomo y llego
    assert db_row(sf, SolAccessSession, id=sid).status == "IN_PROGRESS"
    assert w["agent"].report_status(job_id, "HUMAN_CONTROL", session_id=sid).status_code == 200
    assert w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()["status"] == "HUMAN_CONTROL"


def test_onboarding_retry_after_a_stalled_session_reuses_client_and_credential_without_duplicates(world, running_broker):
    from sqlalchemy import func, select

    from app.models.client import Client
    from app.models.client_sol_credential import ClientSolCredential
    from tests.integration._sol_agent_access_helpers import fake_ruc
    from tests.integration.broker.test_m2_destination_flow import _start_onboarding, _store_credential

    build, _mk, sf = world
    w = build(with_credential=False)
    ruc = fake_ruc()
    onboarding = _start_onboarding(w, ruc).json()
    client_id = uuid.UUID(onboarding["client_id"])
    _store_credential(w, client_id)
    started = start_destination(w, client_id, "FICHA_RUC", extract=True).json()
    sid, job_id, _ = drive_to_credential(w, started)
    # el ejecutor murio sin reportar: la sesion queda estancada y la UI la consulta
    _sql(sf, "update sol_access_sessions set updated_at = now() - interval '10 minutes' where id = :i", i=sid)
    assert w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()["status"] == "FAILED"
    # sin Ficha validada NO se puede confirmar el alta
    state = w["api"].get(f"/api/v1/client-onboardings/{onboarding['id']}").json()
    done = w["api"].post(f"/api/v1/client-onboardings/{onboarding['id']}/complete", json={"accepted_fields": ["business_name"], "expected_version": state["version"]})
    assert done.status_code in {409, 422}
    # reintento seguro: nueva sesion supervisada, mismo cliente provisional, misma credencial (sin duplicados, sin reenviar la consumida)
    again = _start_onboarding(w, ruc)
    assert again.status_code == 201 and again.json()["client_id"] == onboarding["client_id"]
    s = sf()
    try:
        assert s.execute(select(func.count()).select_from(Client).where(Client.ruc == ruc)).scalar_one() == 1
        assert s.execute(select(func.count()).select_from(ClientSolCredential).where(ClientSolCredential.client_id == client_id)).scalar_one() == 1
    finally:
        s.close()
    retried = start_destination(w, client_id, "FICHA_RUC", extract=True)
    assert retried.status_code == 201 and retried.json()["id"] != str(sid)
    assert w["agent"].request_credential(job_id).status_code in {404, 409}  # el job consumido no recibe nunca otra credencial


# --------------------------------------------------------------------------- diagnostico estructural del fallo (sin migracion)


def test_failed_report_carries_a_closed_diagnostic_code_that_the_ui_can_read(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid, job_id = _in_progress(w)
    assert w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()["diagnostic_code"] is None  # no fallido: sin codigo
    failed = w["agent"].report_status(job_id, "FAILED", "PORTAL_CHANGED", session_id=sid, diagnostic_code="LOGIN_CONTROL_MISMATCH")
    assert failed.status_code == 200
    body = w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()
    assert body["status"] == "FAILED" and body["terminal_reason_code"] == "PORTAL_CHANGED"
    assert body["diagnostic_code"] == "LOGIN_CONTROL_MISMATCH"
    assert w["api"].get(f"/api/v1/sol-access/clients/{w['client_id']}/session").json()["diagnostic_code"] == "LOGIN_CONTROL_MISMATCH"
    # el codigo vive en la auditoria del cierre (nunca texto libre ni contenido del portal)
    s = sf()
    try:
        meta = s.execute(text("select metadata from audit_logs where entity_id = :i and action = 'SESSION_FAILED'"), {"i": sid}).scalar_one()
    finally:
        s.close()
    assert meta == {"terminal_reason_code": "PORTAL_CHANGED", "diagnostic_code": "LOGIN_CONTROL_MISMATCH"}


def test_unknown_or_misplaced_diagnostic_codes_are_never_stored_or_shown(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid, job_id = _in_progress(w)
    # forma invalida o con estado no FAILED: el esquema estricto lo rechaza
    assert w["agent"].report_status(job_id, "COMPLETED", session_id=sid, diagnostic_code="LOGIN_CONTROL_MISMATCH").status_code in {400, 422}  # solo FAILED o WAITING_FOR_USER
    # un motivo de espera que NO pertenece a las categorias de espera humana se acepta pero no se guarda ni se muestra
    assert w["agent"].report_status(job_id, "WAITING_FOR_USER", session_id=sid, diagnostic_code="LOGIN_CONTROL_MISMATCH").status_code == 200
    assert w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()["diagnostic_code"] is None
    assert w["agent"].report_status(job_id, "FAILED", "PORTAL_CHANGED", session_id=sid, diagnostic_code="no es un codigo <script>").status_code in {400, 422}
    # forma valida pero FUERA de la lista cerrada: el fallo se registra, el codigo no
    assert w["agent"].report_status(job_id, "FAILED", "PORTAL_CHANGED", session_id=sid, diagnostic_code="TEXTO_LIBRE_DEL_PORTAL_999").status_code == 200
    body = w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()
    assert body["status"] == "FAILED" and body["diagnostic_code"] is None


def test_waiting_for_user_carries_a_closed_reason_category_the_contador_can_read(world, running_broker):
    build, _mk, sf = world
    w = build()
    sid, job_id = _in_progress(w)
    waiting = w["agent"].report_status(job_id, "WAITING_FOR_USER", session_id=sid, diagnostic_code="CONTACT_VALIDATION_INTERSTITIAL")
    assert waiting.status_code == 200
    body = w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()
    assert body["status"] == "WAITING_FOR_USER" and body["is_terminal"] is False and body["diagnostic_code"] == "CONTACT_VALIDATION_INTERSTITIAL"
    assert w["agent"].report_status(job_id, "WAITING_FOR_USER", session_id=sid, diagnostic_code="CONTACT_VALIDATION_INTERSTITIAL").status_code == 200  # idempotente
    # la espera humana legitima no se considera estancada (ni siquiera pasada una hora)
    _sql(sf, "update sol_access_sessions set updated_at = now() - interval '1 hour' where id = :i", i=sid)
    assert w["agent"].heartbeat(holding_job_id=job_id).json()["cancel_notice"] is None
    assert w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()["status"] == "WAITING_FOR_USER"
    # tras la intervencion humana el agente retoma, entrega la Ficha y el control humano es legal desde esa espera
    from tests.integration.broker.test_m2_destination_flow import client_ruc, ficha_fields

    assert w["agent"].report_ficha(job_id, ficha_fields(client_ruc(sf, w["client_id"])), session_id=sid).status_code == 200
    assert w["agent"].report_status(job_id, "HUMAN_CONTROL", session_id=sid).status_code == 200
    assert w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()["diagnostic_code"] is None  # ya no hay espera: sin motivo
