"""HARNESS E3-LIVE-B (lado broker, SOLO ACEPTACION -- no es codigo de producto ni prueba de la suite).

Corre DENTRO de un contenedor Docker DESECHABLE con PostgreSQL real, lo lanza `harness_live_b_windows.py`.
Recibe UNA SOLA VEZ, por STDIN (un pipe desde el proceso de Windows; nunca argv, entorno ni archivo), la
credencial SOL que el HUMANO tecleo en su terminal. Crea una boveda DESECHABLE con una contrasena aleatoria en
memoria, guarda la credencial cifrada con el servicio de producto (Bloque 5.1), y sirve el broker REAL (mTLS)
para que el Agente Windows nativo la reciba por el contrato E1-B existente (autorizacion + entrega de un solo uso).

Nunca imprime ni registra la credencial; los resultados que escribe (`res-N.json`) solo contienen estados.
EXCEPCION DE ARNES: el listener se enlaza a 0.0.0.0 dentro del contenedor (solo para publicar puertos en
127.0.0.1 del host); el broker de producto no se modifica. Todo se borra al terminar (`cleanup_tenant`)."""

import hashlib
import json
import pathlib
import secrets
import sys
import time

from cryptography.hazmat.primitives import serialization

import app.broker.server as server_mod
from app.broker.dispatch import dispatch_job_to_agent
from app.broker.server import BrokerServer
from app.core import sol_vault_crypto as crypto
from app.core.sol_vault_registry import vault_unlock_registry
from app.models.client import Client
from app.models.sol_agent_access import SolAccessSession, SolAgent, SolAgentJob
from app.models.user import User
from app.services import client_sol_credential_service, sol_vault_service
from app.services import sol_agent_credential_delivery_service as delivery_service
from tests.integration.broker._secret_delivery_helpers import (
    FAST_ARGON2_PARAMS,
    commit_sol_access_session,
    commit_tenant_admin_with_app_session,
    get_user_row,
)
from tests.integration.broker.conftest import cleanup_tenant, create_pairing_over_service

OUT = pathlib.Path("/harness/out")
PAIRING_PORT, OPERATIONAL_PORT = 18443, 18444
_SELFTEST_FAKE_RUC = "20999999990"  # RUC ficticio del --selftest (tests.e3_portal.scenarios.FAKE_RUC)


def _fp(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def _serve_impl(session_factory, broker_ca, pairing_key, monkeypatch):
    monkeypatch.setattr(server_mod, "_ALLOWED_HOSTS", {"127.0.0.1", "localhost", "0.0.0.0"})
    credential_requests = {"n": 0}
    handler_cls = server_mod._BrokerHttpHandler
    original = handler_cls._handle_job_credential_request

    def _counting(self, job_id):
        credential_requests["n"] += 1
        return original(self, job_id)

    monkeypatch.setattr(handler_cls, "_handle_job_credential_request", _counting)

    # --- UNICA lectura de secretos: stdin (pipe desde el proceso de Windows) ---
    secrets_in = json.loads(sys.stdin.readline())
    selftest = bool(secrets_in.pop("selftest", False))
    ruc, sol_user, sol_password = secrets_in["ruc"], secrets_in["user"], secrets_in["password"]
    del secrets_in

    # --- KDF de la boveda: PRODUCCION para la credencial real; parametros rapidos SOLO en --selftest ---
    # Los parametros rapidos solo se aceptan si la bandera `selftest` viene Y el RUC es el ficticio del selftest:
    # una credencial real jamas se cifra con el KDF reducido, aunque la bandera se pase por error.
    if selftest:
        if ruc != _SELFTEST_FAKE_RUC:
            raise RuntimeError("live_b_selftest_flag_with_non_fake_credentials")
        monkeypatch.setattr(crypto, "production_argon2_params", lambda: FAST_ARGON2_PARAMS)
        kdf_mode = "FAST_SELFTEST"
    else:
        kdf_mode = "PRODUCTION"  # `crypto.production_argon2_params` SIN parchear (la usa sol_vault_service)
    params = crypto.production_argon2_params()
    if kdf_mode == "PRODUCTION":
        expected = crypto.Argon2Params(
            time_cost=crypto.PRODUCTION_ARGON2_TIME_COST,
            memory_cost_kib=crypto.PRODUCTION_ARGON2_MEMORY_COST_KIB,
            parallelism=crypto.PRODUCTION_ARGON2_PARALLELISM,
            hash_len=crypto.PRODUCTION_ARGON2_HASH_LEN,
        )
        if params != expected or params == FAST_ARGON2_PARAMS:
            raise RuntimeError("live_b_kdf_is_not_the_production_kdf")  # ANTES de cifrar cualquier secreto
    (OUT / "kdf.json").write_text(json.dumps({
        "mode": kdf_mode, "time_cost": params.time_cost, "memory_cost_kib": params.memory_cost_kib,
        "parallelism": params.parallelism,
    }))

    actor_setup = commit_tenant_admin_with_app_session(session_factory)
    tenant_id, admin_id, app_session_id = actor_setup["tenant_id"], actor_setup["user_id"], actor_setup["app_session_id"]
    state: dict = {"server": None, "job": None, "session_id": None}
    try:
        # boveda desechable con contrasena ALEATORIA en memoria (no es la boveda real de nadie)
        db = session_factory()
        try:
            actor = db.get(User, admin_id)
            init = sol_vault_service.initialize_vault(
                db, vault_unlock_registry, tenant_id=tenant_id, actor=actor, app_session_id=app_session_id,
                personal_vault_password="Aa1!" + secrets.token_urlsafe(24),
            )
            db.commit()
            vault_id, recovery_code = init.vault.id, init.recovery_code
        finally:
            db.close()
        db = session_factory()
        try:
            sol_vault_service.confirm_recovery(db, tenant_id=tenant_id, vault_id=vault_id, recovery_code=recovery_code)
            db.commit()
        finally:
            db.close()
        del recovery_code

        db = session_factory()
        try:
            client = Client(tenant_id=tenant_id, ruc=ruc, business_name="Cliente aceptacion LIVE-B",
                            person_type="juridica", ruc_status="ACTIVO", fiscal_address_condition="HABIDO",
                            tax_regime="GENERAL", service_status="activo")
            db.add(client)
            db.flush()
            db.commit()
            client_id = client.id
        finally:
            db.close()
        db = session_factory()
        try:
            actor = db.get(User, admin_id)
            credential = client_sol_credential_service.create_credential(
                db, vault_unlock_registry, tenant_id=tenant_id, vault_id=vault_id, client_id=client_id, actor=actor,
                app_session_id=app_session_id, credential_kind="SECONDARY", sol_username=sol_user,
                sol_password=sol_password, authorization_basis="Aceptacion LIVE-B autorizada por el titular",
            )
            db.commit()
            credential_id = credential.id
        finally:
            db.close()
        del ruc, sol_user, sol_password  # referencias soltadas (el cifrado ya esta en la BD desechable)

        def start_broker():
            server = BrokerServer(
                host="0.0.0.0", pairing_port=PAIRING_PORT, operational_port=OPERATIONAL_PORT, ca=broker_ca,
                session_factory=session_factory, handshake_timeout_seconds=10.0, idle_timeout_seconds=300.0,
                read_timeout_seconds=15.0)
            server.start()
            state["server"] = server

        def agent_id():
            q = session_factory()
            try:
                row = q.query(SolAgent).filter(SolAgent.tenant_id == tenant_id).first()
                return row.id if row else None
            finally:
                q.close()

        def handle(cmd):
            op = cmd["op"]
            if op == "new_pairing":
                pid, code = create_pairing_over_service(session_factory, tenant_id=tenant_id,
                                                        requested_by_user_id=admin_id)
                (OUT / "pairing.json").write_text(json.dumps({"pairing_id": str(pid), "code": code}))
                return {"ok": True}
            if op == "dispatch":
                session_id = commit_sol_access_session(session_factory, tenant_id=tenant_id, client_id=client_id,
                                                       credential_id=credential_id, requester_id=admin_id)
                ds = session_factory()
                try:
                    job_id = dispatch_job_to_agent(ds, tenant_id=tenant_id, session_id=session_id, agent_id=agent_id(),
                                                   pending_dispatch=state["server"].pending_dispatch)
                    ds.commit()
                finally:
                    ds.close()
                state["job"], state["session_id"] = job_id, session_id
                return {"job_id": str(job_id)}
            if op == "authorize":
                delivery_service.authorize_credential_delivery(
                    session_factory, grant_registry=state["server"].secret_delivery_grants,
                    vault_registry=vault_unlock_registry, tenant_id=tenant_id, session_id=state["session_id"],
                    job_id=state["job"], actor=get_user_row(session_factory, admin_id), app_session_id=app_session_id)
                return {"ok": True}
            if op == "session":
                q = session_factory()
                try:
                    s = q.query(SolAccessSession).filter(SolAccessSession.id == state["session_id"]).one()
                    j = q.query(SolAgentJob).filter(SolAgentJob.id == state["job"]).one()
                    return {"session_status": s.status, "terminal_reason_code": s.terminal_reason_code,
                            "operation_result": s.operation_result, "job_status": j.status,
                            "credential_delivered": j.credential_delivered_at is not None,
                            "credential_requests": credential_requests["n"]}
                finally:
                    q.close()
            raise ValueError(op)

        start_broker()
        (OUT / "ca.pem").write_bytes(broker_ca.certificate.public_bytes(serialization.Encoding.PEM))
        (OUT / "ready").write_text("1")
        done, deadline, n = False, time.time() + 3600, 0
        while not done and time.time() < deadline:
            cmd_path = OUT / f"cmd-{n}.json"
            if cmd_path.exists():
                try:
                    cmd = json.loads(cmd_path.read_text())
                except ValueError:
                    time.sleep(0.1)
                    continue
                if cmd["op"] == "stop":
                    done, result = True, {"ok": True}
                else:
                    try:
                        result = handle(cmd)
                    except Exception as exc:  # solo el TIPO: nunca el mensaje (podria citar valores)
                        result = {"error": type(exc).__name__}
                tmp = OUT / f"res-{n}.tmp"
                tmp.write_text(json.dumps(result))
                tmp.replace(OUT / f"res-{n}.json")
                n += 1
            else:
                time.sleep(0.2)
    finally:
        if state["server"] is not None:
            state["server"].stop()
        cleanup_tenant(session_factory, tenant_id)


def test_live_b_serve(session_factory, broker_ca, pairing_key, monkeypatch):
    """Envoltorio que SCRUBBEA toda excepcion: el texto de una excepcion de SQLAlchemy/psycopg incluye los
    parametros enlazados (p. ej. el RUC en claro del INSERT del Client), y pytest lo imprimiria. Solo se propaga el
    TIPO, sin encadenar el original (`from None`)."""
    try:
        _serve_impl(session_factory, broker_ca, pairing_key, monkeypatch)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("live_b_broker_failed:" + type(exc).__name__) from None
