"""HARNESS (no es producto ni prueba de la suite) -- ejecucion NATIVA en Windows de los escenarios M2 con Playwright/Chromium
REAL contra el portal local sintetico (`tests/e3_portal/m2_*`). Solo datos ficticios; sin SUNAT.

Uso (desde backend/, con el venv dedicado):
  ..\\.venv-win-e2\\Scripts\\python.exe tests\\windows_native_acceptance\\harness_m2_windows_runner.py

Mide: escenarios M2, procesos Chromium huerfanos tras ejecutarlos y archivos de navegador persistidos."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import time
import traceback

BACKEND = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
_browsers = BACKEND.parent / ".pw-browsers-win-e2"
if _browsers.is_dir():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_browsers))

from tests.e3_portal import m2_scenarios  # noqa: E402

START = time.time()
BROWSERS = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")


def chromium_processes() -> list[str]:
    if not BROWSERS:
        return []
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like '" + BROWSERS.replace("'", "''")
          + "*' } | ForEach-Object { $_.ProcessId }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def main() -> int:
    failures = []
    for scenario in m2_scenarios.ALL_M2_SCENARIOS:
        try:
            scenario()
            print(f"PASS  {scenario.__name__}")
        except Exception:  # noqa: BLE001
            failures.append(scenario.__name__)
            print(f"FAIL  {scenario.__name__}")
            traceback.print_exc()
    time.sleep(1.5)
    orphans = chromium_processes()
    temp = pathlib.Path(tempfile.gettempdir())
    artifacts = [p.name for p in temp.iterdir()
                 if p.stat().st_mtime >= START - 1 and p.suffix.lower() in {".png", ".webm", ".zip", ".har"}]
    print(f"ORPHAN_CHROMIUM_PROCESSES={len(orphans)}")
    print(f"BROWSER_ARTIFACT_FILES={len(artifacts)}")
    print(f"M2_SCENARIOS={len(m2_scenarios.ALL_M2_SCENARIOS) - len(failures)}/{len(m2_scenarios.ALL_M2_SCENARIOS)} PASS")
    return 0 if not failures and not orphans and not artifacts else 1


if __name__ == "__main__":
    sys.exit(main())
