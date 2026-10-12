"""M2 -- PREFLIGHT estructural del portal SOL (herramienta de desarrollo; SOLO LECTURA; SIN CREDENCIALES).

Ejecuta, con el MISMO codigo y contrato del producto (`SupervisedAuthExecutor.open_and_identify_login`), unicamente la fase anterior a la
credencial: abre la pagina publica de entrada, pulsa el acceso de entrada de siempre y valida la identidad estructural de la pantalla
de ingreso. NUNCA pide, escribe ni envia credenciales (no hay proveedor de credenciales) y cierra el navegador al terminar.

Salida: un JSON SANEADO (`portal-preflight.json`) con etapa, codigo estable y observacion de contadores/booleanos por categoria. Nunca
guarda HTML, valores de formularios, cookies, URLs, rutas, textos del portal ni capturas.

Uso (desde backend/, con el venv nativo del agente):
  $env:PYTHONPATH = "<repo>\\backend"; $env:PLAYWRIGHT_BROWSERS_PATH = "<repo>\\.pw-browsers-win-e2"
  ..\\.venv-win-e2\\Scripts\\python.exe ..\\scripts\\m2_portal_preflight.py [--out <carpeta>]
No forma parte del paquete del agente."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

PREFLIGHT_VERSION = 1


def run_preflight(contract, *, headless: bool = False, **executor_kwargs) -> dict:
    """Fase previa a la credencial. Devuelve el informe saneado (sin ningun dato del portal)."""
    from windows_agent import diagnostics
    from windows_agent.browser_executor import SupervisedAuthExecutor
    from windows_agent.portal_contract import AuthOutcome

    executor = SupervisedAuthExecutor(contract, headless=headless, **executor_kwargs)
    report: dict = {"preflight_version": PREFLIGHT_VERSION, "credentials_requested": False, "credential_submissions": 0}
    try:
        early = executor.open_and_identify_login()
        report["login_ready"] = early is None
        report["last_safe_action"] = executor.last_action
        report["observation"] = executor.observe()
        if early is not None:
            stage, code = diagnostics.classify(early.detail, early.outcome)
            report.update(stage=stage, diagnostic_code=code, outcome=early.outcome.value, detail=diagnostics.safe_detail(early.detail))
        else:
            report.update(stage="LOGIN_FORM", diagnostic_code=None, outcome=AuthOutcome.AUTH_SUCCEEDED.value, detail="")
        report["credential_submissions"] = executor.credential_submissions
    finally:
        executor.close()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default=str(pathlib.Path(os.environ.get("USERPROFILE", ".")) / "m2-live"), help="carpeta de salida (fuera del repositorio)")
    args = parser.parse_args(argv)
    from windows_agent.portal_contract import SOL_PORTAL_CONTRACT

    report = run_preflight(SOL_PORTAL_CONTRACT)
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "portal-preflight.json"
    temp = out_dir / "portal-preflight.json.tmp"
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temp, target)
    print(f"LOGIN_FORM_READY={'YES' if report['login_ready'] else 'NO'}")
    print(f"STAGE={report.get('stage')} DIAGNOSTIC_CODE={report.get('diagnostic_code')}")
    print(f"CREDENTIALS_REQUESTED=NO CREDENTIAL_SUBMISSIONS={report['credential_submissions']}")
    print(f"OUTPUT={target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
