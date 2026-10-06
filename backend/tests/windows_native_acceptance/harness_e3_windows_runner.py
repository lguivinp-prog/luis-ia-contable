"""HARNESS (no es producto ni prueba de la suite) -- ejecucion NATIVA en Windows
de los escenarios E3 con Playwright/Chromium REAL contra el portal local
determinista (`tests/e3_portal/`). Solo credenciales ficticias; sin SUNAT.

Uso (desde backend/, con el venv dedicado):
  $env:PLAYWRIGHT_BROWSERS_PATH = "<repo>\\.pw-browsers-win-e2"
  ..\\.venv-win-e2\\Scripts\\python.exe tests\\windows_native_acceptance\\harness_e3_windows_runner.py

Mide: escenarios (A..L), procesos Chromium huerfanos, perfiles/archivos temporales
nuevos, artefactos de navegador (storageState/cookies/traza/video/capturas) y
presencia de los centinelas ficticios en logs/stdout/stderr/excepciones/archivos."""

from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import traceback

BACKEND = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from tests.e3_portal import scenarios  # noqa: E402

START = time.time()
TEMP = pathlib.Path(tempfile.gettempdir())
BROWSERS = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")


def chromium_processes() -> list[str]:
    if not BROWSERS:
        return []
    ps = (
        "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like '"
        + BROWSERS.replace("'", "''")
        + "*' } | ForEach-Object { $_.ProcessId }"
    )
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def new_temp_entries() -> list[pathlib.Path]:
    found = []
    for p in TEMP.iterdir():
        try:
            if p.stat().st_mtime >= START - 1 and ("playwright" in p.name.lower() or "chromium" in p.name.lower()):
                found.append(p)
        except OSError:
            continue
    return found


def scan_new_files_for_secrets() -> list[str]:
    hits = []
    needles = [s.encode() for s in scenarios.SECRETS]
    for root in (TEMP, pathlib.Path.cwd()):
        for p in root.rglob("*"):
            try:
                if not p.is_file() or p.stat().st_mtime < START - 1 or p.stat().st_size > 5_000_000:
                    continue
                if p.suffix in {".pyc"} or "pw-browsers" in str(p) or "claude" in str(p).lower():
                    continue
                data = p.read_bytes()
            except OSError:
                continue
            if any(n in data for n in needles):
                hits.append(str(p))
    return hits


def browser_artifacts() -> list[str]:
    patterns = ("*.webm", "*trace*.zip", "*storage*state*.json", "*cookies*", "*.har")
    out = []
    for root in (TEMP, pathlib.Path.cwd()):
        for pat in patterns:
            for p in root.rglob(pat):
                try:
                    if p.is_file() and p.stat().st_mtime >= START - 1 and "pw-browsers" not in str(p):
                        out.append(str(p))
                except OSError:
                    continue
    return out


def main() -> int:
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    logging.getLogger("windows_agent").addHandler(handler)
    logging.getLogger("windows_agent").setLevel(logging.DEBUG)
    out_buf, err_buf = io.StringIO(), io.StringIO()

    results, failures, exc_text = {}, [], []
    before = chromium_processes()
    for fn in scenarios.ALL_SCENARIOS:
        t0 = time.time()
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            try:
                results[fn.__name__] = {"status": "PASS", **fn(), "seconds": round(time.time() - t0, 1)}
            except BaseException as exc:  # noqa: BLE001
                results[fn.__name__] = {"status": "FAIL", "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
                failures.append(fn.__name__)
                exc_text.append(traceback.format_exc())
        print(f"{fn.__name__}: {results[fn.__name__]['status']}", flush=True)

    deadline = time.time() + 15
    while time.time() < deadline and chromium_processes():
        time.sleep(0.5)
    all_text = "\n".join([log_stream.getvalue(), out_buf.getvalue(), err_buf.getvalue(), *exc_text, repr(results)])
    summary = {
        "python": sys.version.split()[0],
        "scenarios": results,
        "failed": failures,
        "chromium_processes_before": len(before),
        "chromium_processes_left": len(chromium_processes()),
        "new_playwright_temp_entries_left": [str(p) for p in new_temp_entries()],
        "browser_artifacts_found": browser_artifacts(),
        "secret_sentinels_in_logs_stdout_stderr_exceptions": any(s in all_text for s in scenarios.SECRETS),
        "secret_sentinels_in_new_files": scan_new_files_for_secrets(),
        "agent_log_events": sorted({line.split()[0] for line in log_stream.getvalue().splitlines() if line}),
    }
    print(json.dumps(summary, indent=1, default=str))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
