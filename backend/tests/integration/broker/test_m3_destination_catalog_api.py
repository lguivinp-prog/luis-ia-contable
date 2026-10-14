"""M3: catalogo de destinos por API (broker REAL, mTLS REAL, boveda REAL, PostgreSQL REAL; datos FICTICIOS; sin SUNAT).
Los destinos nuevos estan DESHABILITADOS; su habilitacion en pruebas se simula parchando el catalogo (NO es validacion real)."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.core import sunat_destinations as sd
from app.models.audit_log import AuditLog
from app.models.sol_agent_access import SolAccessSession, SolAgentJob
from app.models.tax_period import TaxPeriod
from tests.integration.broker.test_m2_destination_flow import (  # noqa: F401 -- fixtures y ayudantes reutilizados
    add_user,
    db_row,
    drive_to_credential,
    start_destination,
    world,
)

NEW = ("SIRE", "SIRE_GENERACION_REGISTROS", "DECLARA_FACIL")  # conocidos pero DESHABILITADOS (RVIE, RCE y Declaraciones y Pagos: estructura validada en vivo)


@pytest.fixture
def validated_sire():
    """Compatibilidad de nombre: RVIE ya esta validado en el catalogo; lo que cada perfil necesita es SU prueba (`prove_profile`)."""
    return None


def _proven_rvie(w, sf):
    from tests.integration.broker.test_m3_acceptance_gate import prove_profile

    return prove_profile(w, sf, "SIRE_RVIE")


def _drop_periods(sf, tenant_ids) -> None:
    from sqlalchemy import delete

    s = sf()
    try:
        s.execute(delete(TaxPeriod).where(TaxPeriod.tenant_id.in_(tenant_ids)))
        s.commit()
    finally:
        s.close()


def _period(sf, tenant_id) -> uuid.UUID:
    s = sf()
    try:
        period = TaxPeriod(tenant_id=tenant_id, period_type="MONTHLY", fiscal_year=2026, month=9, canonical_code="2026-09",
                           start_date=date(2026, 9, 1), end_date=date(2026, 9, 30), is_open=True)
        s.add(period)
        s.commit()
        return period.id
    finally:
        s.close()


def test_catalog_endpoint_lists_every_known_destination_but_enables_only_the_validated_ones(world, running_broker):
    build, _mk, _sf = world
    w = build()
    body = w["api"].get("/api/v1/sol-access/destinations").json()
    assert body["destinations"] == ["MENU_SOL", "FICHA_RUC", "SIRE_RVIE", "SIRE_RCE", "DECLARACIONES_Y_PAGOS"]  # habilitados en el catalogo
    catalog = {c["id"]: c for c in body["catalog"]}
    assert list(catalog) == list(sd.all_ids()) and len(catalog) == 9
    assert all(catalog[d]["enabled"] and catalog[d]["validation"] == "LIVE_VALIDATED" for d in ("MENU_SOL", "FICHA_RUC", "TRAMITES_Y_CONSULTAS"))
    assert all(not catalog[d]["enabled"] and catalog[d]["validation"] == "NOT_VALIDATED" for d in NEW)
    # alias / contenido / solo apertura: la UI no duplica tarjetas y explica la equivalencia
    assert catalog["TRAMITES_Y_CONSULTAS"]["alias_of"] == "MENU_SOL" and catalog["TRAMITES_Y_CONSULTAS"]["offered"] is False
    assert catalog["DECLARA_FACIL"]["contained_in"] == "DECLARACIONES_Y_PAGOS" and catalog["DECLARA_FACIL"]["offered"] is False
    assert catalog["SIRE"]["offered"] is False and catalog["SIRE_GENERACION_REGISTROS"]["open_only"] is True
    assert catalog["SIRE_GENERACION_REGISTROS"]["evidence"] == "RECON_COMPLETE" and catalog["SIRE_GENERACION_REGISTROS"]["enabled"] is False
    for accepted in ("SIRE_RVIE", "SIRE_RCE", "DECLARACIONES_Y_PAGOS"):
        assert catalog[accepted]["evidence"] == "LIVE_ACCEPTED" and catalog[accepted]["enabled"] and catalog[accepted]["requires_profile_proof"] is True, accepted
    assert catalog["MENU_SOL"]["requires_profile_proof"] is False and catalog["FICHA_RUC"]["requires_profile_proof"] is False
    assert "BUZON_SUNAT" not in catalog and "SUNAFIL" not in catalog


@pytest.mark.parametrize("destination", NEW)
def test_disabled_destination_is_rejected_before_any_session_job_vault_or_agent_activity(world, running_broker, destination):
    build, _mk, sf = world
    w = build()
    r = start_destination(w, w["client_id"], destination)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "SOL_DESTINATION_DISABLED", r.text
    assert db_row(sf, SolAccessSession, tenant_id=w["tenant_id"]) is None and db_row(sf, SolAgentJob, tenant_id=w["tenant_id"]) is None
    assert w["agent"].jobs_next().json()["job_id"] is None
    s = sf()
    try:
        assert not [a for a in s.execute(select(AuditLog)).scalars() if a.action == "SUNAT_DESTINATION_REQUESTED"]
    finally:
        s.close()


def test_an_enabled_new_destination_creates_one_session_and_job_carrying_only_the_stable_identifier(world, running_broker, validated_sire):
    build, _mk, sf = world
    w = build()
    _proven_rvie(w, sf)
    r = start_destination(w, w["client_id"], "SIRE_RVIE")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["scope"] == "NAVIGATE_TO_DESTINATION" and body["destination"] == "SIRE_RVIE" and body["extract_ficha"] is False
    sid, job_id, advert = drive_to_credential(w, body)
    assert advert["destination"] == "SIRE_RVIE" and advert["extract_ficha"] is False and advert["acceptance"] is False  # perfil ya probado: sin autorizacion
    assert "url" not in advert and "selector" not in advert and "password" not in str(advert).lower()
    # SIRE no extrae datos: pedirlo es un error de contrato, incluso estando habilitado
    other = start_destination(w, w["client_id"], "SIRE_RVIE", extract=True)
    assert other.status_code in {403, 409, 422}
    # el ciclo de vida termina igual que los demas destinos: control humano y luego el cierre del navegador por el humano
    assert w["agent"].report_status(job_id, "HUMAN_CONTROL", session_id=sid).status_code == 200
    assert w["agent"].report_status(job_id, "COMPLETED", session_id=sid).status_code == 200
    final = w["api"].get(f"/api/v1/sol-access/sessions/{sid}").json()
    assert final["status"] == "SUCCEEDED" and final["operation_result"] == "DESTINATION_REACHED" and final["destination"] == "SIRE_RVIE"


def test_opening_a_destination_from_the_monthly_workspace_audits_the_access_context_and_changes_no_compliance_state(world, running_broker, validated_sire):
    build, _mk, sf = world
    w = build()
    _proven_rvie(w, sf)
    period_id = _period(sf, w["tenant_id"])
    r = w["api"].post("/api/v1/sol-access/sessions", json={"client_id": str(w["client_id"]), "idempotency_key": str(uuid.uuid4()),
                                                            "destination": "SIRE_RVIE", "origin_tax_period_id": str(period_id)})
    assert r.status_code == 201, r.text
    s = sf()
    try:
        event = [a for a in s.execute(select(AuditLog).where(AuditLog.tenant_id == w["tenant_id"])).scalars() if a.action == "SUNAT_DESTINATION_REQUESTED" and (a.log_metadata or {}).get("tax_period_id")][-1]
        meta = event.log_metadata
        from app.models.client_period_operation import ClientPeriodOperation
        from app.models.declaration import Declaration

        assert meta["origin"] == "COCKPIT_WORKSPACE" and meta["tax_period_id"] == str(period_id) and meta["destination"] == "SIRE_RVIE"
        assert s.execute(select(ClientPeriodOperation)).first() is None  # abrir SIRE NO crea ni cambia estados del periodo
        assert s.execute(select(Declaration)).first() is None  # ni marca nada como presentado
    finally:
        s.close()
    # el periodo de OTRO tenant o inexistente no sirve de contexto
    other = build(with_agent=False, with_credential=False)
    foreign = _period(sf, other["tenant_id"])
    bad = w["api"].post("/api/v1/sol-access/sessions", json={"client_id": str(w["client_id"]), "idempotency_key": str(uuid.uuid4()),
                                                              "destination": "MENU_SOL", "origin_tax_period_id": str(foreign)})
    _drop_periods(sf, [w["tenant_id"], other["tenant_id"]])  # los periodos de prueba no deben bloquear la limpieza de los tenants
    assert bad.status_code == 404 and bad.json()["detail"]["code"] == "TAX_PERIOD_NOT_FOUND"


def test_rbac_tenant_isolation_and_single_active_session_for_new_destinations(world, running_broker, validated_sire):
    build, mk, sf = world
    w = build()
    _proven_rvie(w, sf)
    other = build(with_agent=False, with_credential=False)
    asistente = mk(w["tenant_id"], add_user(sf, w["tenant_id"], "asistente"))
    denied = start_destination(w, w["client_id"], "SIRE_RVIE", api=asistente)
    assert denied.status_code == 403  # el asistente no tiene sol_session:create / sol_credential:read
    assert start_destination(other, w["client_id"], "SIRE_RVIE", api=other["api"]).status_code == 404  # otro tenant no ve este cliente
    first = start_destination(w, w["client_id"], "SIRE_RVIE")
    assert first.status_code == 201
    second = start_destination(w, w["client_id"], "MENU_SOL")
    assert second.status_code == 409 and second.json()["detail"]["code"] in {"SOL_SESSION_ALREADY_ACTIVE", "SOL_AGENT_UNAVAILABLE"}  # no se duplica la autenticacion
    s = sf()
    try:
        rows = list(s.execute(select(SolAccessSession).where(SolAccessSession.tenant_id == w["tenant_id"])).scalars())
        assert len(rows) == 2  # la prueba del perfil + la sesion nueva (una sola no terminal)
        assert len([r for r in rows if r.status not in {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"}]) == 1
    finally:
        s.close()
    assert w["api"].post("/api/v1/sol-access/sessions", json={"client_id": str(w["client_id"]), "idempotency_key": str(uuid.uuid4()),
                                                                "destination": "BUZON_SUNAT"}).status_code == 422
    no_csrf = mk(w["tenant_id"], w["admin_id"])
    no_csrf.headers.pop("X-CSRF-Token", None)
    assert no_csrf.post("/api/v1/sol-access/sessions", json={"client_id": str(w["client_id"]), "idempotency_key": str(uuid.uuid4()),
                                                              "destination": "SIRE_RVIE"}).status_code == 403


def test_tramites_y_consultas_opens_the_menu_sol_session_without_a_second_destination_or_authentication(world, running_broker):
    build, _mk, sf = world
    w = build()
    r = start_destination(w, w["client_id"], "TRAMITES_Y_CONSULTAS")
    assert r.status_code == 201, r.text
    assert r.json()["destination"] == "MENU_SOL"  # el alias se resuelve: una sola sesion, el destino funcional real
    s = sf()
    try:
        rows = list(s.execute(select(SolAccessSession).where(SolAccessSession.tenant_id == w["tenant_id"])).scalars())
        assert len(rows) == 1 and rows[0].destination == "MENU_SOL"
    finally:
        s.close()
    # un segundo intento (otro destino o el mismo alias) NO abre otra autenticacion mientras haya sesion activa
    again = start_destination(w, w["client_id"], "TRAMITES_Y_CONSULTAS")
    assert again.status_code == 409 and again.json()["detail"]["code"] in {"SOL_SESSION_ALREADY_ACTIVE", "SOL_AGENT_UNAVAILABLE"}


def test_alias_of_a_disabled_destination_stays_disabled(world, running_broker):
    build, _mk, sf = world
    w = build()
    assert sd.resolve("DECLARA_FACIL") == "DECLARA_FACIL"  # contenido en la plataforma, NO alias: se rechaza por si mismo
    r = start_destination(w, w["client_id"], "DECLARA_FACIL")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "SOL_DESTINATION_DISABLED"
    assert db_row(sf, SolAccessSession, tenant_id=w["tenant_id"]) is None
