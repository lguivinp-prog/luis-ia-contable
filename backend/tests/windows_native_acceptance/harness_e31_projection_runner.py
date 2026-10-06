"""HARNESS (no es producto ni prueba de la suite) -- E3.1 de extremo a extremo
NATIVO en Windows: Agente Windows real (DPAPI + mTLS) + Chromium REAL contra el
portal local determinista + broker REAL en Docker (`harness_serve_broker.py`,
con PostgreSQL real). Verifica que el resultado del navegador se PROYECTA en
`sol_access_sessions` por el contrato real. Solo credenciales ficticias; sin SUNAT.

Uso (desde backend/):
  $env:PLAYWRIGHT_BROWSERS_PATH = "<repo>\\.pw-browsers-win-e2"
  ..\\.venv-win-e2\\Scripts\\python.exe tests\\windows_native_acceptance\\harness_e31_projection_runner.py <dir_arnes> <dir_trabajo>"""

from __future__ import annotations

import json
import pathlib
import sys
import threading
import time
import uuid

BACKEND = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

HARNESS = pathlib.Path(sys.argv[1]).resolve()
WORK = pathlib.Path(sys.argv[2]).resolve()
OUT = HARNESS / "out"
_n = {"i": 0}
_lock = threading.Lock()


def cmd(op: str, timeout: float = 120.0) -> dict:
    with _lock:
        i = _n["i"]
        _n["i"] += 1
    (OUT / f"cmd-{i}.json").write_text(json.dumps({"op": op}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = OUT / f"res-{i}.json"
        if r.exists():
            try:
                res = json.loads(r.read_text())
            except ValueError:  # archivo de resultado aun a medio escribir: se reintenta
                time.sleep(0.1)
                continue
            assert "error" not in res, f"{op}: {res}"
            return res
        time.sleep(0.2)
    raise TimeoutError(op)


def wait_for(pred, timeout: float, what: str):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(0.3)
    raise TimeoutError(what)


def main() -> int:
    from tests.e3_portal.fake_portal import FakeSolPortal
    from tests.e3_portal.scenarios import test_contract
    from windows_agent.browser_executor import SupervisedAuthExecutor
    from windows_agent.config import AgentConfig
    from windows_agent.identity import WindowsDpapiIdentityStore
    from windows_agent.pairing import pair_agent
    from windows_agent.portal_contract import AuthOutcome
    from windows_agent.runtime import AgentRuntime

    wait_for(lambda: (OUT / "ready").exists(), 120, "broker harness ready")
    ca_pem = (OUT / "ca.pem").read_bytes()
    config = AgentConfig(pairing_port=18443, operational_port=18444)
    data_dir = WORK / "agent-data"
    store = WindowsDpapiIdentityStore(data_dir)
    cmd("new_pairing")
    info = json.loads((OUT / "pairing.json").read_text())
    pair_agent(identity_store=store, pairing_base_url=config.pairing_base_url, ca_certificate_pem=ca_pem,
               pairing_id=uuid.UUID(info["pairing_id"]), pairing_code=info["code"], protocol_version=1, timeout=10.0)

    results: dict = {}
    failures: list[str] = []

    def run_scenario(name: str, portal_scenario: str, *, expect_outcome: AuthOutcome, expect_session: str,
                     expect_reason: str | None, expect_job: str, helper=None, expect_waiting_seen=False,
                     expect_submissions: int = 1) -> None:
        portal = FakeSolPortal().start()
        portal.set_scenario(portal_scenario)
        runtime = AgentRuntime(
            identity=store.load(), config=config,
            ssl_context_factory=lambda: store.build_client_ssl_context(ca_certificate_pem=ca_pem),
            auth_executor_factory=lambda **hooks: SupervisedAuthExecutor(
                test_contract(portal), headless=False, navigation_timeout_seconds=20.0, settle_grace_seconds=1.5,
                challenge_timeout_seconds=60.0, poll_interval_seconds=0.1, **hooks),
        )
        waiting_seen = {"v": False}
        t = None
        try:
            runtime.connect()
            job_id = uuid.UUID(cmd("new_job")["job_id"])
            assert runtime.poll_and_claim_once() == job_id, "job no reclamado"
            after_claim = cmd("session")["session_status"]  # E3.1.1: el claim mueve la sesion a IN_PROGRESS
            assert after_claim == "IN_PROGRESS", after_claim
            cmd("authorize")
            if helper:
                def _h():
                    wait_for(lambda: cmd("session")["session_status"] == "WAITING_FOR_USER", 60, "WAITING_FOR_USER")
                    waiting_seen["v"] = True
                    helper(portal)
                t = threading.Thread(target=_h, daemon=True)
                t.start()
            result = runtime.authenticate_claimed_job(job_id, heartbeat_every_seconds=1.0)
            if t:
                t.join(30)
            final = cmd("session")
            ok = (result.outcome is expect_outcome and final["session_status"] == expect_session
                  and final["terminal_reason_code"] == expect_reason and final["job_status"] == expect_job
                  and len(portal.submissions) == expect_submissions
                  and (not expect_waiting_seen or waiting_seen["v"]) and portal.forbidden_requests() == [])
            if expect_session == "SUCCEEDED":
                ok = ok and final["operation_result"] == "LANDING_REACHED"
            results[name] = {"ok": ok, "outcome": result.outcome.value, "session": final["session_status"],
                             "reason": final["terminal_reason_code"], "job": final["job_status"],
                             "after_claim": after_claim, "waiting_seen": waiting_seen["v"], "submissions": len(portal.submissions),
                             "credential_requests_total": final["credential_requests"]}
            if not ok:
                failures.append(name)
        except Exception as exc:  # noqa: BLE001
            results[name] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            failures.append(name)
        finally:
            runtime.stop()
            portal.stop()

    run_scenario("normal_login", "success", expect_outcome=AuthOutcome.AUTH_SUCCEEDED, expect_session="SUCCEEDED",
                 expect_reason=None, expect_job="COMPLETED")
    run_scenario("human_challenge", "challenge", expect_outcome=AuthOutcome.AUTH_SUCCEEDED,
                 expect_session="SUCCEEDED", expect_reason=None, expect_job="COMPLETED", expect_waiting_seen=True,
                 helper=lambda portal: portal.release_challenge())
    run_scenario("portal_changed", "changed_selector", expect_outcome=AuthOutcome.PORTAL_CHANGED,
                 expect_session="FAILED", expect_reason="PORTAL_CHANGED", expect_job="FAILED", expect_submissions=0)
    run_scenario("invalid_credentials", "invalid", expect_outcome=AuthOutcome.INVALID_CREDENTIALS,
                 expect_session="FAILED", expect_reason="AUTH_FAILED", expect_job="FAILED")
    run_scenario("cancel_while_waiting", "challenge_never", expect_outcome=AuthOutcome.CANCELLED,
                 expect_session="CANCELLED", expect_reason="CANCELLED_BY_USER", expect_job="CANCELLED",
                 expect_waiting_seen=True, helper=lambda portal: cmd("cancel"))

    cmd("stop")
    summary = {"python": sys.version.split()[0], "results": results, "failed": failures}
    (WORK / "e31-result.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
