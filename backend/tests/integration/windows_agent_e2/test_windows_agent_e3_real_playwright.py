"""E3: escenarios con Playwright/Chromium REAL contra el portal local (ver
`tests/e3_portal/scenarios.py`). Se SALTAN si Playwright no esta instalado
(p. ej. en el contenedor Docker del backend); se ejecutan de forma nativa en
Windows con `tests/windows_native_acceptance/harness_e3_windows_runner.py`."""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e3_portal import scenarios  # noqa: E402


@pytest.mark.parametrize("scenario", scenarios.ALL_SCENARIOS, ids=lambda f: f.__name__)
def test_real_playwright_scenario(scenario):
    scenario()
