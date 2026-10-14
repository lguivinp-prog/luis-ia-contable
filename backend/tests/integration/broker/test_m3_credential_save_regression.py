"""M3: regresion de la incidencia «No se pudo completar la operacion sobre la boveda SOL» al registrar una credencial PRINCIPAL junto a una secundaria.
Causa raiz: `authorization_basis` (texto largo) excedia `varchar(60)` y nada lo validaba -> StringDataRightTruncation -> 500 opaco. Broker REAL,
Boveda REAL, PostgreSQL REAL; datos FICTICIOS; sin SUNAT."""

from __future__ import annotations

import logging
import threading

from sqlalchemy import select
from sqlalchemy.exc import DataError

from app.models.audit_log import AuditLog
from app.models.client_sol_credential import ClientSolCredential
from tests.integration.broker.test_m2_destination_flow import add_user, world  # noqa: F401
from tests.integration.broker.test_m3_credential_profiles import PRIMARY_PASSWORD, PRIMARY_USER, rows
from tests.integration.broker.test_sol_access_api_flow import TEST_SOL_PASSWORD_DO_NOT_USE, TEST_SOL_USER

# texto del mismo orden de longitud que el que provoco la incidencia (sin datos reales)
LONG_BASIS = "Acceso autorizado por el titular para consultas y pruebas supervisadas de destinos, sin presentacion ni pago de ningun tipo (ficticio)."


def create(w, *, basis="Autorizado por el titular", kind="PRIMARY_OWN_RUC", name="SOL Principal", api=None, **extra):
    body = {"credential_kind": kind, "profile_name": name, "sol_username": PRIMARY_USER, "sol_password": PRIMARY_PASSWORD, "authorization_basis": basis, **extra}
    return (api or w["api"]).post(f"/api/v1/clients/{w['client_id']}/sol-credentials", json=body)


def snapshot(sf, tenant_id):
    return [(r.id, r.credential_kind, r.status, r.version, bytes(r.sol_username_encrypted), bytes(r.sol_password_encrypted), r.updated_at) for r in rows(sf, tenant_id)]


def test_long_authorization_basis_is_a_clear_422_and_leaves_the_secondary_untouched(world, running_broker):
    build, _mk, sf = world
    w = build()
    before = snapshot(sf, w["tenant_id"])
    assert len(LONG_BASIS) > 60 and len(before) == 1
    r = create(w, basis=LONG_BASIS)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail[0]["loc"][-1] == "authorization_basis" and detail[0]["type"] == "string_too_long"
    assert LONG_BASIS not in r.text and PRIMARY_PASSWORD not in r.text and PRIMARY_USER not in r.text  # el valor enviado no se refleja
    assert snapshot(sf, w["tenant_id"]) == before  # nada se crea, nada se modifica (id, estado, version, cifrado, updated_at)


def test_primary_coexists_with_the_secondary_with_the_long_detail_in_the_scope_description(world, running_broker):
    build, _mk, sf = world
    w = build()
    before = snapshot(sf, w["tenant_id"])
    enabled_before = w["api"].get("/api/v1/sol-access/destinations").json()["destinations"]
    r = create(w, basis="x" * 60, authorized_scope_description=LONG_BASIS)  # el limite exacto es valido
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["credential_kind"] == "PRIMARY_OWN_RUC" and body["profile_name"] == "SOL Principal" and body["status"] == "NEEDS_VALIDATION"
    assert "sol_password" not in body and "sol_username" not in body and PRIMARY_PASSWORD not in r.text and PRIMARY_USER not in r.text
    after = snapshot(sf, w["tenant_id"])
    assert len(after) == 2 and after[0] == before[0]  # la secundaria sigue identica byte a byte
    listed = w["api"].get(f"/api/v1/clients/{w['client_id']}/sol-credentials").json()
    assert {p["credential_kind"] for p in listed} == {"SECONDARY", "PRIMARY_OWN_RUC"}
    assert all("password" not in str(p).lower() or p.get("sol_password") is None for p in listed)
    # el cifrado es real: ninguno de los dos valores en claro esta en la base
    primary = next(x for x in after if x[1] == "PRIMARY_OWN_RUC")
    assert PRIMARY_PASSWORD.encode() not in primary[5] and PRIMARY_USER.encode() not in primary[4]
    # registrar un perfil NO habilita destinos ni inicia sesiones o autenticaciones externas
    assert w["api"].get("/api/v1/sol-access/destinations").json()["destinations"] == enabled_before  # el alta no cambia lo habilitado
    perms = w["api"].get(f"/api/v1/sol-access/clients/{w['client_id']}/credential-destinations").json()["credentials"]
    assert all(d["proven"] is False for c in perms for d in c["destinations"])  # un perfil nuevo NO hereda ninguna prueba
    assert w["api"].get(f"/api/v1/sol-access/clients/{w['client_id']}/session").json() is None


def test_other_field_limits_are_422_before_touching_the_vault(world, running_broker):
    build, _mk, sf = world
    w = build()
    before = snapshot(sf, w["tenant_id"])
    assert create(w, name="n" * 61).status_code == 422
    assert create(w, authorization_reference="r" * 256).status_code == 422
    assert create(w, authorized_scope_description="d" * 2001).status_code == 422
    assert create(w, basis="   ").status_code == 422
    assert snapshot(sf, w["tenant_id"]) == before


def test_double_submit_creates_a_single_profile_and_the_loser_gets_a_controlled_409(world, running_broker):
    build, _mk, sf = world
    w = build()
    codes: list[tuple[int, str | None]] = []

    def go() -> None:
        r = create(w)
        codes.append((r.status_code, None if r.status_code == 200 else r.json()["detail"].get("code") if isinstance(r.json()["detail"], dict) else "?"))

    threads = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert [c for c, _ in codes].count(200) == 1, codes
    assert all(code in {"SOL_CREDENTIAL_PROFILE_NAME_TAKEN", "SOL_PRIMARY_CREDENTIAL_ALREADY_EXISTS"} for status, code in codes if status != 200), codes
    assert all(status in {200, 409} for status, _ in codes), codes  # jamas un 500 opaco
    assert len(rows(sf, w["tenant_id"])) == 2


def test_a_database_failure_is_a_controlled_500_with_rollback_and_without_secrets_in_logs(world, running_broker, monkeypatch, caplog):
    from app.services import client_sol_credential_service as svc

    build, _mk, sf = world
    w = build()
    before = snapshot(sf, w["tenant_id"])

    class _Orig(Exception):
        pass

    def boom(*args, **kwargs):
        raise DataError("INSERT ...", {"sol_password_encrypted": PRIMARY_PASSWORD}, _Orig("value too long"))

    monkeypatch.setattr(svc, "create_credential", boom)
    with caplog.at_level(logging.DEBUG):
        r = create(w)
    assert r.status_code == 500 and r.json()["detail"] == {"code": "SOL_CREDENTIAL_STORE_FAILED"}
    assert PRIMARY_PASSWORD not in r.text and PRIMARY_USER not in r.text
    assert PRIMARY_PASSWORD not in caplog.text and PRIMARY_USER not in caplog.text and TEST_SOL_PASSWORD_DO_NOT_USE not in caplog.text
    monkeypatch.undo()
    assert snapshot(sf, w["tenant_id"]) == before  # nada a medias
    assert create(w).status_code == 200  # tras el fallo el reintento unico funciona


def test_vault_and_session_failures_are_distinct_controlled_errors(world, running_broker):
    build, mk, sf = world
    w = build()
    assert w["api"].post("/api/v1/sol-vault/lock").status_code == 200
    locked = create(w)
    assert locked.status_code == 409 and locked.json()["detail"]["code"] in {"SOL_VAULT_LOCKED", "SOL_VAULT_UNLOCK_EXPIRED"}
    assert len(rows(sf, w["tenant_id"])) == 1
    no_csrf = mk(w["tenant_id"], w["admin_id"])
    no_csrf.headers.pop("X-CSRF-Token", None)
    assert create(w, api=no_csrf).status_code == 403
    asistente = mk(w["tenant_id"], add_user(sf, w["tenant_id"], "asistente"))
    assert create(w, api=asistente).status_code == 403
    other = build(with_agent=False, with_credential=False)
    assert create(w, api=other["api"]).status_code == 404  # el cliente de otro tenant no existe para este usuario
    assert len(rows(sf, w["tenant_id"])) == 1


def test_update_route_also_rejects_oversized_values_without_corrupting_the_profile(world, running_broker):
    build, _mk, sf = world
    w = build()
    before = snapshot(sf, w["tenant_id"])
    only = before[0]
    r = w["api"].put(f"/api/v1/clients/{w['client_id']}/sol-credentials/{only[0]}", json={"expected_version": only[3], "authorization_basis": LONG_BASIS})
    assert r.status_code == 422, r.text
    assert snapshot(sf, w["tenant_id"]) == before


def test_audit_of_the_save_records_no_secrets(world, running_broker):
    build, _mk, sf = world
    w = build()
    assert create(w).status_code == 200
    s = sf()
    try:
        entries = [a for a in s.execute(select(AuditLog).where(AuditLog.tenant_id == w["tenant_id"])).scalars() if "CREDENTIAL" in a.action]
    finally:
        s.close()
    blob = " ".join(str(a.log_metadata) for a in entries)
    assert entries and PRIMARY_PASSWORD not in blob and PRIMARY_USER not in blob and TEST_SOL_USER not in blob
