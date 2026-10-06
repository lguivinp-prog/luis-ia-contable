"""HARNESS (no es producto ni prueba de la suite) -- orquestador de la
validacion NATIVA del Agente Windows (Domain E2.1-B). Ejecuta el CLI
PRODUCTIVO `python -m windows_agent run` como subproceso real de Windows contra
el broker real que corre en Docker (ver `harness_serve_broker.py`), y controla
broker/jobs mediante archivos de comando.

Uso (desde backend/, con el venv de Windows dedicado):
  ..\\.venv-win-e2\\Scripts\\python.exe tests\\windows_native_acceptance\\harness_windows_orchestrator.py <dir_arnes> <dir_trabajo>

Solo credenciales/identidades ficticias de prueba. Nada de SOL real ni SUNAT."""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import time
import uuid

BACKEND = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

HARNESS = pathlib.Path(sys.argv[1]).resolve()
WORK = pathlib.Path(sys.argv[2]).resolve()
OUT = HARNESS / "out"
LOCALAPPDATA = WORK / "localappdata"
DATA_DIR = LOCALAPPDATA / "LuisIAContable" / "SolAgent"
CA_DIR = WORK / "ca"
RESULTS: dict = {}
_n = {"i": 0}

SENTINELS = [b"TEST_SOL_PASSWORD_DO_NOT_USE", b"TEST_SOL_USER"]


def cmd(op: str, timeout: float = 120.0) -> dict:
    i = _n["i"]
    _n["i"] += 1
    (OUT / f"cmd-{i}.json").write_text(json.dumps({"op": op}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = OUT / f"res-{i}.json"
        if r.exists():
            res = json.loads(r.read_text())
            assert "error" not in res, f"{op}: {res}"
            return res
        time.sleep(0.2)
    raise TimeoutError(op)


def wait_for(pred, timeout: float, what: str, interval: float = 0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(interval)
    raise TimeoutError(what)


def env() -> dict:
    e = dict(os.environ)
    e.update(LOCALAPPDATA=str(LOCALAPPDATA), PYTHONPATH=str(BACKEND), PYTHONDONTWRITEBYTECODE="1")
    return e


def spawn_agent(tag: str) -> subprocess.Popen:
    out = open(WORK / f"agent-{tag}.out", "wb")
    return subprocess.Popen(
        [sys.executable, "-m", "windows_agent", "--ca-dir", str(CA_DIR), "run"],
        env=env(), stdout=out, stderr=subprocess.STDOUT, cwd=str(BACKEND),
    )


def agent_log() -> str:
    p = DATA_DIR / "logs" / "agent.log"
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def events(name: str) -> list[dict]:
    found = []
    for line in agent_log().splitlines():
        m = re.search(rf"\b{name} (\{{.*\}})\s*$", line)
        if m:
            found.append(json.loads(m.group(1)))
    return found


def proc_stats(pid: int) -> dict:
    ps = (f"$p=Get-Process -Id {pid}; '{{0}} {{1}}' -f $p.TotalProcessorTime.TotalSeconds, $p.Threads.Count")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout.split()
    return {"cpu_seconds": float(out[0].replace(",", ".")), "threads": int(out[1])}


def scan_work_for_secrets(extra_markers: list[bytes]) -> list[str]:
    """Busca centinelas/claves/frase de paso en TODOS los archivos del directorio de trabajo
    del agente (datos, logs, salidas de proceso)."""
    hits = []
    markers = SENTINELS + extra_markers + [b"-----BEGIN PRIVATE KEY-----", b"-----BEGIN RSA PRIVATE KEY-----"]
    for p in WORK.rglob("*"):
        if p.is_file() and not p.name.startswith("orchestrator-"):
            try:
                b = p.read_bytes()
            except OSError:
                continue
            for m in markers:
                if m in b:
                    hits.append(f"{p.relative_to(WORK)}:{m[:24]!r}")
    return hits


def key_markers() -> list[bytes]:
    """Frase de paso en claro (descifrada via DPAPI en memoria) y cuerpo PEM de la clave en claro
    (derivado en memoria) -- usados SOLO para buscarlos en disco; nunca se escriben."""
    from cryptography.hazmat.primitives import serialization
    from windows_agent.identity import _dpapi_unprotect

    pw = _dpapi_unprotect((DATA_DIR / "agent.keypass.dpapi").read_bytes())
    key = serialization.load_pem_private_key((DATA_DIR / "agent.key.enc.pem").read_bytes(), password=pw)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    body = b"".join(pem.splitlines()[1:-1])
    return [pw, body[:64]]


def main() -> None:
    from windows_agent.config import AgentConfig, save_config
    from windows_agent.identity import WindowsDpapiIdentityStore
    from windows_agent.pairing import pair_agent

    wait_for(lambda: (OUT / "ready").exists(), 120, "broker harness ready")
    CA_DIR.mkdir(parents=True, exist_ok=True)
    (CA_DIR / "ca.cert.pem").write_bytes((OUT / "ca.pem").read_bytes())
    config = AgentConfig(pairing_port=18443, operational_port=18444, read_timeout_seconds=6.0)
    save_config(DATA_DIR, config)

    # --- pairing nativo (DPAPI real) ---
    cmd("new_pairing")
    info = json.loads((OUT / "pairing.json").read_text())
    store = WindowsDpapiIdentityStore(DATA_DIR)
    pair_agent(identity_store=store, pairing_base_url=config.pairing_base_url,
               ca_certificate_pem=(CA_DIR / "ca.cert.pem").read_bytes(), pairing_id=uuid.UUID(info["pairing_id"]),
               pairing_code=info["code"], protocol_version=1, timeout=10.0)
    RESULTS["pairing"] = store.is_paired()

    # --- L: broker OFFLINE al arrancar el agente ---
    cmd("stop_broker")
    agent = spawn_agent("offline")
    s0 = None
    try:
        wait_for(lambda: len(events("broker_unavailable")) >= 3, 120, "3 eventos broker_unavailable")
        s0 = proc_stats(agent.pid)
        time.sleep(10)
        s1 = proc_stats(agent.pid)
        evs = events("broker_unavailable")
        RESULTS["L"] = {
            "process_alive": agent.poll() is None,
            "wait_seconds_sequence": [e["wait_seconds"] for e in evs],
            "cpu_seconds_over_10s_idle": round(s1["cpu_seconds"] - s0["cpu_seconds"], 3),
            "threads_before_after": [s0["threads"], s1["threads"]],
            "identity_files": sorted(p.name for p in DATA_DIR.iterdir() if p.is_file()),
        }
        assert RESULTS["L"]["process_alive"]
        assert RESULTS["L"]["wait_seconds_sequence"][:3] == [2.0, 4.0, 8.0], RESULTS["L"]
        assert s1["threads"] <= s0["threads"] + 1

        # --- M-A: el broker vuelve; el MISMO proceso conecta ---
        pid_before = agent.pid
        cmd("start_broker")
        st = wait_for(lambda: (lambda r: r if r["connected"] else None)(cmd("status")), 120, "agent connected")
        fp1 = st["connection_fp"]
        assert agent.poll() is None and agent.pid == pid_before
        RESULTS["M_A"] = {"same_process": True, "connected": True, "fp1": fp1,
                          "log_agent_connected": len(events("agent_connected")) >= 1}

        # --- M-B: reinicio del broker con conexion establecida ---
        cmd("stop_broker")
        time.sleep(3)
        cmd("start_broker")
        st2 = wait_for(lambda: (lambda r: r if r["connected"] and r["connection_fp"] != fp1 else None)(cmd("status")),
                       150, "agent reconnected with a new connection")
        RESULTS["M_B"] = {"same_process": agent.poll() is None and agent.pid == pid_before,
                          "old_fp": fp1, "new_fp": st2["connection_fp"], "new_differs": st2["connection_fp"] != fp1,
                          "connection_lost_events": len(events("connection_lost")) + len(events("broker_unavailable"))}
        assert RESULTS["M_B"]["same_process"] and RESULTS["M_B"]["new_differs"]

        # --- Q: cancelacion de un job reclamado ---
        job = cmd("new_job")["job_id"]
        wait_for(lambda: events("job_claimed"), 60, "job claimed by agent")
        cmd("cancel")
        obs = wait_for(lambda: [e for e in events("job_cancellation_observed") if e.get("job_id") == job], 60,
                       "cancellation observed by agent")
        stq = cmd("status")
        RESULTS["Q"] = {"observed": bool(obs), "job_released_at_broker": stq["active_job"] is None,
                        "credential_requests_at_broker": stq["credential_requests"]}
        assert stq["credential_requests"] == 0

        # --- instancia unica: segundo proceso rechazado mientras el primero vive ---
        second = spawn_agent("second")
        second.wait(timeout=30)
        RESULTS["mutex_second_instance_exit_code"] = second.returncode  # 3 = SingleInstanceError

        # --- AO: terminacion ABRUPTA desde fuera (TerminateProcess) con conexion viva ---
        pre_fp = cmd("status")["connection_fp"]
        agent.kill()
        agent.wait(timeout=15)
        RESULTS["AO_killed_exit_code"] = agent.returncode
    finally:
        if agent.poll() is None:
            agent.kill()

    markers = key_markers()
    RESULTS["AO_scan_after_kill"] = scan_work_for_secrets(markers)
    agent2 = spawn_agent("restart")
    try:
        st3 = wait_for(lambda: (lambda r: r if r["connected"] and r["connection_fp"] != pre_fp else None)(cmd("status")),
                       120, "restart after kill connects with mTLS")
        RESULTS["AO_restart"] = {"alive": agent2.poll() is None, "mtls_connected": True,
                                 "mutex_recovered": agent2.poll() is None,
                                 "new_fp_differs": st3["connection_fp"] != pre_fp}
        assert agent2.poll() is None
    finally:
        agent2.kill()
        agent2.wait(timeout=15)

    # --- AP: secreto SOL de prueba vivo en memoria + kill abrupto + reinicio ---
    # (new_job ya fue cancelado arriba; se crea otro job fresco)
    cmd("new_job")
    holder_out = open(WORK / "agent-holder.out", "wb")
    holder = subprocess.Popen(
        [sys.executable, str(pathlib.Path(__file__).with_name("harness_secret_holder.py")), str(DATA_DIR), str(OUT)],
        env=env(), stdout=holder_out, stderr=subprocess.STDOUT, cwd=str(BACKEND))
    try:
        wait_for(lambda: (OUT / "holder-claimed").exists(), 60, "holder claimed job")
        cmd("authorize")
        (OUT / "holder-authorized").write_text("1")
        wait_for(lambda: (OUT / "holder-holding").exists(), 60, "holder holds the credential in memory")
        stats = cmd("status")
        RESULTS["AP_credential_delivered_to_holder"] = stats["credential_requests"] >= 1
        holder.kill()  # TerminateProcess mientras el secreto esta en memoria
        holder.wait(timeout=15)
    finally:
        if holder.poll() is None:
            holder.kill()
    RESULTS["AP_scan_after_kill_with_secret_in_memory"] = scan_work_for_secrets(markers)
    before = cmd("status")["credential_requests"]
    agent3 = spawn_agent("after-secret")
    try:
        wait_for(lambda: (lambda r: r if r["connected"] else None)(cmd("status")), 120, "agent restarts after secret kill")
        time.sleep(8)
        after = cmd("status")["credential_requests"]
        RESULTS["AP_restart"] = {"agent_requested_credential_on_its_own": after != before,
                                 "scan": scan_work_for_secrets(markers)}
    finally:
        agent3.kill()
        agent3.wait(timeout=15)

    RESULTS["final_files"] = sorted(str(p.relative_to(WORK)) for p in DATA_DIR.rglob("*") if p.is_file())
    RESULTS["final_scan"] = scan_work_for_secrets(markers)
    cmd("stop")
    (WORK / "orchestrator-result.json").write_text(json.dumps(RESULTS, indent=1, default=str))
    print(json.dumps(RESULTS, indent=1, default=str))


if __name__ == "__main__":
    main()
