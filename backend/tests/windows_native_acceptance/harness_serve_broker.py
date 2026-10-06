"""HARNESS DE VALIDACION NATIVA EN WINDOWS (Fase 5 / Bloque 5.2 / Domain E2.1).
NO es codigo de producto y NO es una prueba automatica de la suite (el nombre
`harness_*` evita la recoleccion de pytest): sirve el broker REAL (PostgreSQL
real + mTLS real) dentro de un contenedor Docker DESECHABLE, controlado por
archivos de comando, para validar el Agente Windows NATIVO contra el.

EXCEPCION SOLO DE ARNES: dentro del contenedor el listener se enlaza a
0.0.0.0 (se sustituye `_ALLOWED_HOSTS` en memoria de este proceso) UNICAMENTE
para que Docker pueda publicarlo en 127.0.0.1 del host Windows. El codigo del
broker de producto NO se modifica y el requisito loopback-only del producto
NO se relaja.

Uso (ver docs/phase5/block5_2/BLOCK5_2_WINDOWS_AGENT.md):
  docker compose run --rm --no-deps -p 127.0.0.1:18443:18443 -p 127.0.0.1:18444:18444 \
    -v <dir_arnes>:/harness backend python -m pytest /harness/harness_serve_broker.py \
    -p no:cacheprovider --rootdir=/app -c /app/pyproject.toml \
    -p tests.conftest -p tests.integration.broker.conftest
Comandos: archivos /harness/out/cmd-<n>.json {"op": ...}; resultados res-<n>.json."""

import hashlib
import json
import pathlib
import time

from cryptography.hazmat.primitives import serialization

import app.broker.server as server_mod
from app.broker.dispatch import dispatch_job_to_agent
from app.broker.server import BrokerServer
from app.core.sol_vault_registry import vault_unlock_registry
from app.models.sol_agent_access import SolAgent
from app.services import sol_access_session_service as session_service
from app.services import sol_agent_credential_delivery_service as delivery_service
from tests.integration.broker._secret_delivery_helpers import get_user_row
from tests.integration.broker.conftest import cleanup_tenant, commit_tenant_user, create_pairing_over_service
from tests.integration.windows_agent_e2.test_windows_agent_hygiene_and_scope import _full_real_session

OUT = pathlib.Path("/harness/out")
PAIRING_PORT, OPERATIONAL_PORT = 18443, 18444


def _fp(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def test_serve(session_factory, broker_ca, pairing_key, monkeypatch):
    monkeypatch.setattr(server_mod, "_ALLOWED_HOSTS", {"127.0.0.1", "localhost", "0.0.0.0"})
    credential_requests = {"n": 0}
    handler_cls = server_mod._BrokerHttpHandler
    original = handler_cls._handle_job_credential_request

    def _counting(self, job_id):
        credential_requests["n"] += 1
        return original(self, job_id)

    monkeypatch.setattr(handler_cls, "_handle_job_credential_request", _counting)

    state: dict = {"server": None, "job": None}
    setup = commit_tenant_user(session_factory)

    def start_broker():
        server = BrokerServer(
            host="0.0.0.0", pairing_port=PAIRING_PORT, operational_port=OPERATIONAL_PORT, ca=broker_ca,
            session_factory=session_factory, handshake_timeout_seconds=10.0, idle_timeout_seconds=120.0,
            read_timeout_seconds=15.0,
        )
        server.start()
        state["server"] = server

    def agent_id():
        s = session_factory()
        try:
            row = s.query(SolAgent).filter(SolAgent.tenant_id == setup["tenant_id"]).first()
            return row.id if row else None
        finally:
            s.close()

    def handle(cmd):
        op = cmd["op"]
        if op == "start_broker":
            start_broker()
            return {"ok": True}
        if op == "stop_broker":
            state["server"].stop()
            state["server"] = None
            return {"ok": True}
        if op == "new_pairing":
            pid, code = create_pairing_over_service(
                session_factory, tenant_id=setup["tenant_id"], requested_by_user_id=setup["admin_id"])
            (OUT / "pairing.json").write_text(json.dumps({"pairing_id": str(pid), "code": code}))
            return {"ok": True}
        if op == "status":
            aid, server = agent_id(), state["server"]
            record = server.connections.get(agent_id=aid) if (server and aid) else None
            return {"broker_up": server is not None, "agent_paired": aid is not None,
                    "connected": record is not None,
                    "connection_fp": _fp(record.connection_handle) if record else None,
                    "active_job": str(record.active_job_id) if record and record.active_job_id else None,
                    "credential_requests": credential_requests["n"]}
        if op == "new_job":
            if state["job"] is None:
                extra = _full_real_session(session_factory, monkeypatch, setup)
            else:
                # bóveda/credencial ya existen para este tenant: solo una sesion de acceso nueva
                from app.models.client import Client
                from app.models.client_sol_credential import ClientSolCredential
                from tests.integration.broker._secret_delivery_helpers import commit_sol_access_session

                q = session_factory()
                try:
                    client_id = q.query(Client).filter(Client.tenant_id == setup["tenant_id"]).first().id
                    credential_id = q.query(ClientSolCredential).filter(
                        ClientSolCredential.tenant_id == setup["tenant_id"]).first().id
                finally:
                    q.close()
                new_session_id = commit_sol_access_session(
                    session_factory, tenant_id=setup["tenant_id"], client_id=client_id,
                    credential_id=credential_id, requester_id=setup["admin_id"])
                extra = {"app_session_id": state["job"]["app_session_id"], "ruc": state["job"]["ruc"],
                         "session_id": new_session_id}
            ds = session_factory()
            try:
                job_id = dispatch_job_to_agent(ds, tenant_id=setup["tenant_id"], session_id=extra["session_id"],
                    agent_id=agent_id(), pending_dispatch=state["server"].pending_dispatch)
                ds.commit()
            finally:
                ds.close()
            state["job"] = {"job_id": job_id, **extra}
            return {"job_id": str(job_id)}
        if op == "authorize":
            j = state["job"]
            delivery_service.authorize_credential_delivery(
                session_factory, grant_registry=state["server"].secret_delivery_grants,
                vault_registry=vault_unlock_registry, tenant_id=setup["tenant_id"], session_id=j["session_id"],
                job_id=j["job_id"], actor=get_user_row(session_factory, setup["admin_id"]),
                app_session_id=j["app_session_id"])
            return {"ok": True}
        if op == "session":
            from app.models.sol_agent_access import SolAccessSession, SolAgentJob

            j = state["job"]
            q = session_factory()
            try:
                srow = q.query(SolAccessSession).filter(SolAccessSession.id == j["session_id"]).one()
                jrow = q.query(SolAgentJob).filter(SolAgentJob.id == j["job_id"]).one()
                return {"session_status": srow.status, "terminal_reason_code": srow.terminal_reason_code,
                        "operation_result": srow.operation_result, "job_status": jrow.status,
                        "credential_delivered": jrow.credential_delivered_at is not None,
                        "credential_requests": credential_requests["n"]}
            finally:
                q.close()
        if op == "cancel":
            j = state["job"]
            s = session_factory()
            try:
                session_service.cancel_session(s, tenant_id=setup["tenant_id"], session_id=j["session_id"],
                                               actor_user_id=setup["admin_id"])
                s.commit()
            finally:
                s.close()
            return {"ok": True}
        raise ValueError(op)

    start_broker()
    (OUT / "ca.pem").write_bytes(broker_ca.certificate.public_bytes(serialization.Encoding.PEM))
    (OUT / "ready").write_text("1")
    done, deadline, n = False, time.time() + 1800, 0
    try:
        while not done and time.time() < deadline:
            cmd_path = OUT / f"cmd-{n}.json"
            if cmd_path.exists():
                cmd = json.loads(cmd_path.read_text())
                if cmd["op"] == "stop":
                    done = True
                    result = {"ok": True}
                else:
                    try:
                        result = handle(cmd)
                    except Exception as exc:  # el arnes reporta, nunca oculta
                        result = {"error": f"{type(exc).__name__}: {exc}"}
                (OUT / f"res-{n}.json").write_text(json.dumps(result))
                n += 1
            else:
                time.sleep(0.2)
    finally:
        if state["server"] is not None:
            state["server"].stop()
        cleanup_tenant(session_factory, setup["tenant_id"])
