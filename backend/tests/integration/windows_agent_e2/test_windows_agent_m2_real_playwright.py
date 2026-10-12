"""M2: escenarios con Playwright/Chromium REAL contra el portal local sintetico (shell/menu/Ficha con la estructura
observada en vivo). Se SALTAN si Playwright no esta instalado (p. ej. Docker); se ejecutan de forma nativa con
`tests/windows_native_acceptance/harness_m2_windows_runner.py`."""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e3_portal import m2_scenarios  # noqa: E402


@pytest.mark.parametrize("scenario", m2_scenarios.ALL_M2_SCENARIOS, ids=lambda f: f.__name__)
def test_real_playwright_m2_scenario(scenario):
    scenario()
