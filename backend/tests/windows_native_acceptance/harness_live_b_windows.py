"""HARNESS E3-LIVE-B (lado Windows, SOLO ACEPTACION -- no es codigo de producto ni prueba de la suite).

LO EJECUTA EL HUMANO en su propia terminal. Hace UNA autenticacion SOL real, supervisada, con el camino de
producto: Agente Windows nativo (DPAPI + mTLS) -> broker E2 -> entrega E1-B (un solo uso) -> ejecutor E3 ->
SUNAT real -> proyeccion E3.1 a la sesion. Termina en el landing: cero acciones posteriores.

SECRETOS: el RUC, el usuario SOL secundario y la clave SOL se piden con `getpass` (sin eco) EN ESTA TERMINAL y viajan
UNA vez por un pipe (stdin) hacia el proceso del broker en Docker; nunca por argv, entorno, archivos ni conversacion.
El script NUNCA los imprime. Solo se escribe un resultado SANITIZADO (estados, hosts, rutas sin identificadores).

Uso (desde backend/, con el venv dedicado, UNA sola vez):
  $env:PLAYWRIGHT_BROWSERS_PATH = "<repo>\\.pw-browsers-win-e2"
  ..\\.venv-win-e2\\Scripts\\python.exe tests\\windows_native_acceptance\\harness_live_b_windows.py

Garantias: un unico intento (marca anti-reintento en %LOCALAPPDATA%), confirmacion explicita, sin reintento de
login, sin Buzon ni acciones tras el landing, navegador efimero y visible."""

from __future__ import annotations

import getpass
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from urllib.parse import urlsplit

BACKEND = pathlib.Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
sys.path.insert(0, str(BACKEND))

ATTEMPT_FLAG = pathlib.Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "LuisIAContable" / "live_b_attempted.flag"
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
WORK = pathlib.Path(tempfile.gettempdir()) / f"live_b_{STAMP}"
HARNESS = WORK / "harness"
OUT = HARNESS / "out"
CONTAINER = "live-b-broker"
_n = {"i": 0}
_lock = threading.Lock()
_ID_SEGMENT = re.compile(r"^(?:[0-9a-fA-F-]{16,}|[A-Za-z0-9_-]{24,})$")


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
            except ValueError:
                time.sleep(0.1)
                continue
            if "error" in res:
                raise RuntimeError(f"broker harness op '{op}' failed: {res['error']}")
            return res
        time.sleep(0.2)
    raise TimeoutError(op)


def sanitize_path(path: str) -> str:
    return "/" + "/".join("<id>" if _ID_SEGMENT.match(seg) else seg for seg in path.split("/") if seg)


# --- ESQUEMA CERRADO del resultado: solo claves/tipos permitidos; ningun texto libre externo ---
_CODE = re.compile(r"^[A-Z][A-Z_]{0,40}$")
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
_NAME = re.compile(r"^[A-Za-z0-9 _.<>'(),-]{1,120}$")  # nombres locales / codigos de producto, nunca texto de la pagina
_HOST = re.compile(r"^(?:[a-z0-9.-]{1,100}|<unexpected-host>)$")
_PATHRE = re.compile(r"^/(?:[A-Za-z0-9._~<>-]{1,60}(?:/[A-Za-z0-9._~<>-]{1,60})*)?$")
_ALLOWED_TITLES = {"SUNAT - Menú SOL", "SUNAT SOL Operaciones en Línea"}


def _leaf(kind: str, v) -> bool:
    return {
        "int": isinstance(v, int) and not isinstance(v, bool),
        "bool": isinstance(v, bool),
        "optbool": v is None or isinstance(v, bool),
        "optint": v is None or (isinstance(v, int) and not isinstance(v, bool)),
        "code": isinstance(v, str) and bool(_CODE.match(v)),
        "optcode": v is None or (isinstance(v, str) and bool(_CODE.match(v))),
        "ts": isinstance(v, str) and bool(_TS.match(v)),
        "name": isinstance(v, str) and bool(_NAME.match(v)),
        "optname": v is None or (isinstance(v, str) and bool(_NAME.match(v))),
        "host": isinstance(v, str) and bool(_HOST.match(v)),
        "path": isinstance(v, str) and bool(_PATHRE.match(v)),
        "scheme": v in {"http", "https", "<other>"},
        "title": v in _ALLOWED_TITLES,  # solo literales esperados del portal
    }[kind]


_PAGE = {"scheme": "scheme", "host": "host", "path": "path", "title_match": "bool", "title": "title",
         "frame_hosts": ["host"], "password_field_visible": "bool", "matches_landing_contract": "bool"}
_SURFACE = {"pages": [_PAGE], "top_level_hosts": ["host"], "capture_error": "name"}
_SCHEMA = {
    "started": "ts", "finished": "ts", "mode": "code", "real_auth_attempts": "int", "outcome": "code",
    "terminal_reason_code": "optcode", "credential_submissions": "int", "post_landing_clicks": "int",
    "ruc_mode_activation_clicks": "optint", "preauth_contract_match": "optbool",
    "credential_requests_at_broker": "int", "credential_delivered": "bool", "final_session_state": "code",
    "final_job_state": "code", "session_terminal_reason": "optcode", "operation_result": "optcode",
    "harness_error": "name",
    "kdf": {"mode": "code", "time_cost": "int", "memory_cost_kib": "int", "parallelism": "int"},
    "state_sequence": [{"t": "ts", "session": "code", "job": "code", "credential_requests": "int", "note": "name"}],
    "evidence": {"mode_activation_clicks": "optint", "preauth_contract_match": "bool", "preauth_surface": _SURFACE,
                 "post_submit_surface": _SURFACE,
                 "preauth_failure": {"outcome": "code", "detail": "optname", "terminal_reason_code": "optcode"}},
    "hygiene": {"secret_in_work_files": ["name"], "secret_in_result_or_repr": "bool",
                "orphan_chromium_processes": "int", "playwright_temp_dirs_left": ["name"],
                "browser_artifacts_in_work_dir": ["name"]},
    "local_portal_forbidden_requests": [["name"]],
    "schema_violations": ["name"],
}


def _validate(value, spec, path: str, bad: list[str]) -> None:
    if isinstance(spec, dict):
        if not isinstance(value, dict):
            bad.append(path)
            return
        for k, v in value.items():
            if k not in spec:
                bad.append(f"{path}.{k}")  # clave no permitida (se reporta el NOMBRE, nunca el valor)
            else:
                _validate(v, spec[k], f"{path}.{k}", bad)
    elif isinstance(spec, list):
        if not isinstance(value, list):
            bad.append(path)
            return
        for i, item in enumerate(value):
            _validate(item, spec[0], f"{path}[{i}]", bad)
    elif not _leaf(spec, value):
        bad.append(path)


def validate_result(summary: dict) -> list[str]:
    bad: list[str] = []
    _validate(summary, _SCHEMA, "$", bad)
    return bad


def chromium_processes(browsers: str) -> int:
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like '"
          + browsers.replace("'", "''") + "*' } | Measure-Object | ForEach-Object { $_.Count }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout.strip()
    return int(out or 0)


def main() -> int:
    selftest = "--selftest" in sys.argv  # prueba de la maquinaria con portal LOCAL sintetico y credenciales FICTICIAS
    if os.name != "nt":
        print("Este harness es para Windows nativo.")
        return 2
    if ATTEMPT_FLAG.exists() and not selftest:
        print(f"Ya existe una marca de intento ({ATTEMPT_FLAG}). Un segundo intento real exige una NUEVA autorizacion: "
              "borra la marca manualmente solo si la tienes.")
        return 3
    browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(REPO / ".pw-browsers-win-e2")
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = browsers

    local_portal = None
    if selftest:
        from tests.e3_portal.fake_portal import FakeSolPortal
        from tests.e3_portal.scenarios import FAKE_PASSWORD, FAKE_RUC, FAKE_USER, test_contract

        print("=== SELFTEST: portal local sintetico, credenciales FICTICIAS, SIN red real ===")
        local_portal = FakeSolPortal().start()
        contract_to_use = test_contract(local_portal)
        ruc, sol_user, sol_password = FAKE_RUC, FAKE_USER, FAKE_PASSWORD
    else:
        print("=== E3-LIVE-B: UNA autenticacion SOL real, supervisada, hasta el landing ===")
        print("Se abrira un Chromium visible en SUNAT real. Se hara UN envio de credenciales y NADA mas despues del landing.")
        print("Si aparece un challenge (CAPTCHA/verificacion), resuelvelo TU en la ventana; el script no lo toca.")
        if input("Escribe exactamente  AUTENTICAR UNA VEZ  para continuar: ").strip() != "AUTENTICAR UNA VEZ":
            print("Cancelado. No se hizo nada.")
            return 1
        ruc = getpass.getpass("RUC (11 digitos, sin eco): ").strip()
        sol_user = getpass.getpass("Usuario SOL secundario (sin eco): ").strip()
        sol_password = getpass.getpass("Clave SOL (sin eco): ")
        if not re.fullmatch(r"\d{11}", ruc) or not sol_user or not sol_password:
            print("Entrada invalida (RUC de 11 digitos, usuario y clave no vacios). No se hizo nada.")
            return 1
        from windows_agent.portal_contract import SOL_PORTAL_CONTRACT as contract_to_use  # noqa: N811

    from tests.windows_native_acceptance import _live_b_support as support  # noqa: PLC0415

    if not selftest:
        ATTEMPT_FLAG.parent.mkdir(parents=True, exist_ok=True)
        ATTEMPT_FLAG.write_text(STAMP)  # a partir de aqui cualquier ejecucion posterior se rechaza
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copy(BACKEND / "tests" / "windows_native_acceptance" / "harness_live_b_broker.py", HARNESS)

    summary: dict = {"started": datetime.now().isoformat(timespec="seconds"),
                     "mode": "SELFTEST_LOCAL_PORTAL" if selftest else "REAL", "real_auth_attempts": 0 if selftest else 1}
    states: list[dict] = []
    stop_poll = threading.Event()
    container_out = open(WORK / "broker.out", "wb")
    broker = None
    runtime = None
    result = None
    try:
        broker = subprocess.Popen(
            ["docker", "compose", "run", "-T", "--rm", "--no-deps", "--name", CONTAINER,
             "-p", "127.0.0.1:18443:18443", "-p", "127.0.0.1:18444:18444", "-v", f"{HARNESS}:/harness",
             "-e", "PYTHONDONTWRITEBYTECODE=1", "backend", "python", "-m", "pytest",
             "/harness/harness_live_b_broker.py", "-s", "-q", "--tb=no", "--no-summary", "-rN",
             "-p", "no:cacheprovider",
             "--rootdir=/app", "-c", "/app/pyproject.toml", "-p", "tests.conftest",
             "-p", "tests.integration.broker.conftest"],
            cwd=str(REPO), stdin=subprocess.PIPE, stdout=container_out, stderr=subprocess.STDOUT,
            env={**os.environ, "MSYS_NO_PATHCONV": "1"},
        )
        broker.stdin.write((json.dumps({"ruc": ruc, "user": sol_user, "password": sol_password,
                                        "selftest": selftest}) + "\n").encode())
        broker.stdin.flush()
        broker.stdin.close()
        t0 = time.time()
        while not (OUT / "ready").exists():
            if broker.poll() is not None or time.time() - t0 > 240:
                raise RuntimeError("el broker desechable no arranco")
            time.sleep(0.5)
        kdf = json.loads((OUT / "kdf.json").read_text())  # atestacion NO secreta del KDF realmente usado
        summary["kdf"] = kdf
        if not selftest and kdf.get("mode") != "PRODUCTION":
            raise RuntimeError("live_kdf_is_not_production")

        from windows_agent.config import AgentConfig
        from windows_agent.identity import WindowsDpapiIdentityStore
        from windows_agent.pairing import pair_agent

        from windows_agent.runtime import AgentRuntime

        ca_pem = (OUT / "ca.pem").read_bytes()
        config = AgentConfig(pairing_port=18443, operational_port=18444)
        store = WindowsDpapiIdentityStore(WORK / "agent-data")
        cmd("new_pairing")
        info = json.loads((OUT / "pairing.json").read_text())
        pair_agent(identity_store=store, pairing_base_url=config.pairing_base_url, ca_certificate_pem=ca_pem,
                   pairing_id=uuid.UUID(info["pairing_id"]), pairing_code=info["code"], protocol_version=1, timeout=10.0)

        evidence: dict = {}

        def _poll():
            last = None
            while not stop_poll.is_set():
                try:
                    s = cmd("session", timeout=15)
                    cur = (s["session_status"], s["job_status"])
                    if cur != last:
                        states.append({"t": datetime.now().isoformat(timespec="seconds"), "session": cur[0],
                                       "job": cur[1], "credential_requests": s["credential_requests"]})
                        last = cur
                except Exception:  # noqa: BLE001
                    pass
                stop_poll.wait(1.0)

        runtime = AgentRuntime(
            identity=store.load(), config=config,
            ssl_context_factory=lambda: store.build_client_ssl_context(ca_certificate_pem=ca_pem),
            auth_executor_factory=lambda **hooks: support.LiveExecutor(
                contract_to_use, evidence=evidence, authorize_hook=lambda: cmd("authorize"), headless=False,
                challenge_timeout_seconds=600.0, **hooks),
        )
        runtime.connect()
        job_id = uuid.UUID(cmd("dispatch")["job_id"])
        states.append({"t": datetime.now().isoformat(timespec="seconds"), "session": "WAITING_FOR_AGENT",
                       "job": "PENDING", "note": "despachado"})
        poller = threading.Thread(target=_poll, daemon=True)
        poller.start()
        assert runtime.poll_and_claim_once() == job_id, "el job no se reclamo"
        print("Job reclamado. Abriendo el navegador y validando el portal (aun SIN credenciales)...")
        result = runtime.authenticate_claimed_job(job_id, heartbeat_every_seconds=3.0)  # UNICO intento real
        time.sleep(1.5)
        stop_poll.set()
        poller.join(5)
        final = cmd("session", timeout=30)
        summary.update(
            outcome=result.outcome.value, terminal_reason_code=result.terminal_reason_code,
            credential_submissions=result.credential_submissions, post_landing_clicks=result.post_landing_clicks,
            ruc_mode_activation_clicks=evidence.get("mode_activation_clicks"),
            preauth_contract_match=evidence.get("preauth_contract_match"),
            credential_requests_at_broker=final["credential_requests"], credential_delivered=final["credential_delivered"],
            final_session_state=final["session_status"], final_job_state=final["job_status"],
            session_terminal_reason=final["terminal_reason_code"], operation_result=final["operation_result"],
            state_sequence=states, evidence=evidence,
        )
    except BaseException as exc:  # noqa: BLE001 -- solo el TIPO (nunca el mensaje)
        summary["harness_error"] = type(exc).__name__
        stop_poll.set()
    finally:
        try:
            if runtime is not None:
                runtime.stop()
        finally:
            try:
                if (OUT / "ready").exists():
                    cmd("stop", timeout=30)
            except Exception:  # noqa: BLE001
                pass
            if broker is not None:
                try:
                    broker.wait(timeout=60)
                except Exception:  # noqa: BLE001
                    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
            container_out.close()

    # --- higiene (las referencias a los secretos viven solo en este proceso) ---
    secrets_bytes = [s.encode() for s in (ruc, sol_user, sol_password) if s]
    leaked_files = []
    for p in WORK.rglob("*"):
        if p.is_file() and p.name not in {"ready"}:
            try:
                data = p.read_bytes()
            except OSError:
                continue
            if any(sb in data for sb in secrets_bytes):
                leaked_files.append(p.name)
    text_blob = json.dumps(summary, default=str) + (repr(result) if result is not None else "")
    time.sleep(2)
    summary["hygiene"] = {
        "secret_in_work_files": leaked_files,  # nombres de archivos (nunca contenido)
        "secret_in_result_or_repr": any(sb.decode() in text_blob for sb in secrets_bytes),
        "orphan_chromium_processes": chromium_processes(browsers),
        "playwright_temp_dirs_left": sorted(
            p.name for p in pathlib.Path(tempfile.gettempdir()).iterdir()
            if p.is_dir() and "playwright" in p.name.lower() and p.stat().st_mtime >= time.time() - 7200
        ),
        "browser_artifacts_in_work_dir": sorted(
            p.name for p in WORK.rglob("*") if p.is_file() and re.search(r"\.(webm|har|png|zip)$|storage.?state|cookies", p.name)
        ),
    }
    del ruc, sol_user, sol_password, secrets_bytes
    summary["finished"] = datetime.now().isoformat(timespec="seconds")
    if local_portal is not None:
        summary["local_portal_forbidden_requests"] = local_portal.forbidden_requests()
        local_portal.stop()
    violations = validate_result(summary)  # esquema cerrado: ningun campo fuera de lo permitido llega al archivo
    if violations:
        keep = ("started", "finished", "mode", "real_auth_attempts", "outcome", "terminal_reason_code",
                "credential_submissions", "post_landing_clicks", "final_session_state", "final_job_state", "kdf")
        summary = {k: summary[k] for k in keep if k in summary and not validate_result({k: summary[k]})}
        summary["schema_violations"] = [re.sub(r"[^A-Za-z0-9_.<>-]", "_", v)[:100] for v in violations][:20]
    out = WORK / "live_b_result.json"
    out.write_text(json.dumps(summary, indent=1, default=str))
    print("\n=== RESULTADO SANITIZADO (sin secretos) ===")
    print(json.dumps({k: v for k, v in summary.items() if k != "evidence"}, indent=1, default=str))
    print(f"\nEvidencia sanitizada de paginas/landing y resultado completo: {out}")
    print("Comparte ESE archivo conmigo. NO se hara un segundo intento sin nueva autorizacion.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
