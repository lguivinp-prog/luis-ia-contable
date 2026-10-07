"""Fase 5, Bloque 5.2, Domain G -- runtime del navegador INSTALADO (modulo propiedad de E3: es el unico, junto a
`browser_executor`/`portal_contract`/`portal_identity`, que puede referirse a Playwright/Chromium; el barrido
`test_AV_AW_production_package_never_references_sunat_or_playwright_outside_e3_modules` lo exige).

- En la build empaquetada fija SIEMPRE el directorio de navegadores propio de la app (no hereda ni depende de
  `.pw-browsers-win-e2` ni de ningun valor ambiente).
- `check_installed_browser`: diagnostico seguro -- abre y cierra el navegador instalado (visible, contexto efimero) en
  `about:blank`. No navega a ningun sitio ni toca credenciales; no persiste estado."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from windows_agent.install_paths import installed_browsers_directory


def configure_browsers_path() -> Path | None:
    """Solo en la build congelada; fuera de ella no toca el entorno (desarrollo/pruebas)."""
    browsers = installed_browsers_directory()
    if browsers is None:
        return None
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
    return browsers


def check_installed_browser() -> int:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            try:
                context = browser.new_context()
                page = context.new_page()
                page.goto("about:blank")
                print(f"BROWSER_OK -- chromium {browser.version}")
                context.close()
            finally:
                browser.close()
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"BROWSER_FAILED -- {type(exc).__name__}", file=sys.stderr)
        return 1
