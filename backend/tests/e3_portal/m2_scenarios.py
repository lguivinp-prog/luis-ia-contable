"""Escenarios M2 contra Playwright REAL + portal local con la ESTRUCTURA del shell/menu/Ficha observada en vivo (SOLO
PRUEBAS; credenciales y datos FICTICIOS; sin red hacia SUNAT). Se saltan si Playwright no esta instalado (Docker) y se
ejecutan de forma nativa en Windows (`tests/windows_native_acceptance/harness_m2_windows_runner.py`).

Invariantes verificados (ademas de los de E3, que siguen intactos):
- sin el marcador POSITIVO del shell (DOM) NO se hace ningun clic posterior al aterrizaje;
- destino fuera del enum / extraccion fuera de FICHA_RUC => fail closed antes de abrir el navegador;
- clics de navegacion <= presupuesto del contrato (Ficha: 4), 0 para MENU_SOL; tras validar el destino la automatizacion
  se CONGELA (`post_landing_clicks == 0`) y el humano conserva el navegador hasta cerrarlo/cancelar/vencer;
- jamas se solicitan rutas consecuentes (SIRE, Declaraciones y Pagos, Buzon, imprimir/modificar la Ficha);
- una sola entrega de credencial; la Ficha se reporta ANTES del traspaso al humano."""

from __future__ import annotations

import pathlib

from tests.e3_portal import m2_shell
from tests.e3_portal.fake_portal import FakeSolPortal
from tests.e3_portal.scenarios import FakeCredentialProvider, test_contract
from windows_agent import destination_contract as dc
from windows_agent.browser_executor import SupervisedAuthExecutor
from windows_agent.destination_executor import run_destination_session
from windows_agent.portal_contract import AuthOutcome

FICHA_HOSTS = frozenset({"ficha.localhost"})


class Run:
    """Ejecuta una sesion con destino y registra el orden de los eventos del 'backend' simulado."""

    def __init__(self, scenario: str, destination: str, extract: bool, *, close_after_hc: bool = True,
                 cancel_after_hc: bool = False, hc_ok: bool = True, ficha_ok_if_ruc: str | None = m2_shell.FICHA_RUC,
                 hc_timeout: float = 20.0, dismiss_notice: bool = False, cancel_while_waiting: bool = False,
                 human_wait: float = 30.0, on_tick=None, destination_wait: float = 8.0, hop_delay_ms: int | None = None,
                 hop_grace: float = 2.0, attempts: int = 2, dismiss_times: int = 1, auth_nav: float = 6.0, acceptance: bool = False) -> None:
        self.portal = FakeSolPortal().start()
        if hop_delay_ms is not None:
            self.portal.hop_delay_ms = hop_delay_ms
        self.portal.set_scenario(scenario)
        self.events: list[str] = []
        self.ficha: dict | None = None
        self.provider = FakeCredentialProvider()
        self.close_after_hc = close_after_hc
        self.cancel_after_hc = cancel_after_hc
        self._hc_reported = False
        self._ticks_after_hc = 0
        self._waiting_ticks = 0
        self._dismiss_count = 0
        self.holder: dict = {}

        def tick() -> None:
            ex_ = self.holder.get("ex")
            if dismiss_notice and self._dismiss_count < dismiss_times and "state:WAITING_FOR_USER" in self.events and ex_ is not None:
                self._waiting_ticks += 1
                if self._waiting_ticks >= 3:  # el HUMANO cierra el aviso de SUNAT en la ventana del navegador
                    for pg in ex_._pages():
                        pg.evaluate("window.__dismissNotice && window.__dismissNotice()")
                    self.events.append("human_dismisses_notice")
                    self._dismiss_count += 1
                    self._waiting_ticks = 0
            if ex_ is not None and ex_._browser is not None and "state:WAITING_FOR_USER" in self.events and "browser_open_while_waiting" not in self.events:
                self.events.append("browser_open_while_waiting")
            if on_tick is not None and ex_ is not None:
                on_tick(self, ex_)
            if not self._hc_reported:
                return
            self._ticks_after_hc += 1
            ex = self.holder["ex"]
            if self.close_after_hc and self._ticks_after_hc >= 3 and ex._browser is not None:
                self.events.append("human_closes_browser")
                ex._browser.close()  # el HUMANO cierra el navegador (mismo hilo: API sincrona de Playwright)

        def report_ficha(payload: dict) -> bool:
            self.events.append("ficha")
            self.ficha = payload
            return ficha_ok_if_ruc is None or payload["fields"].get("ruc") == ficha_ok_if_ruc

        def report_hc() -> bool:
            self.events.append("human_control")
            self._hc_reported = True
            return hc_ok

        ex = SupervisedAuthExecutor(
            test_contract(self.portal), headless=False, navigation_timeout_seconds=20.0, settle_grace_seconds=1.5,
            challenge_timeout_seconds=human_wait, poll_interval_seconds=0.1, pre_credential_attempts=attempts, landing_hop_grace_seconds=hop_grace, auth_navigation_timeout_seconds=auth_nav, tick=tick,
            is_cancelled=lambda: (self.cancel_after_hc and self._hc_reported)
            or (cancel_while_waiting and "state:WAITING_FOR_USER" in self.events)
            or "cancel_requested" in self.events,
            on_state=lambda state: self.events.append(f"state:{state.value}"),
        )
        self.holder["ex"] = ex
        self.executor = ex
        try:
            self.result = run_destination_session(
                ex, self.provider, None, destination=destination, extract_ficha=extract, report_ficha=report_ficha,
                report_human_control=report_hc, human_control_timeout_seconds=hc_timeout, navigator_wait_seconds=destination_wait, acceptance=acceptance,
                ficha_frame_hosts=FICHA_HOSTS, ficha_frame_scheme="http",
            )
        finally:
            self.forbidden = self.portal.forbidden_requests()
            self.portal.stop()


def _clean(run: Run) -> None:
    assert run.forbidden == [], run.forbidden  # nunca SIRE / Declaraciones y Pagos / Buzon / imprimir / modificar
    assert run.provider.fetches <= 1  # UNA sola entrega de credencial


def scenario_menu_sol_destination_then_human_control() -> None:
    run = Run("m2_success", "MENU_SOL", False)
    r = run.result
    assert r.outcome is AuthOutcome.AUTH_SUCCEEDED, r
    assert r.destination_reached and r.destination_clicks == 0 and r.post_landing_clicks == 0
    assert run.events == ["human_control", "human_closes_browser"], run.events
    assert r.credential_submissions == 1
    _clean(run)


def scenario_ficha_ruc_extraction_then_human_control() -> None:
    run = Run("m2_success", "FICHA_RUC", True)
    r = run.result
    assert r.outcome is AuthOutcome.AUTH_SUCCEEDED, r
    assert r.destination_reached and r.ficha_reported
    assert r.destination_clicks == 4  # nivel1..nivel4, por RELACION ESTRUCTURAL (nunca indice global)
    assert r.post_landing_clicks == 0  # tras validar el destino: CERO clics automaticos
    assert run.events[:2] == ["ficha", "human_control"], run.events  # la Ficha se reporta ANTES del traspaso
    fields = run.ficha["fields"]
    assert fields["ruc"] == m2_shell.FICHA_RUC and fields["razon_social"].startswith("EMPRESA FICTICIA")
    assert fields["fecha_inscripcion"] == "2015-03-15" and fields["estado_contribuyente"] == "ACTIVO"
    assert "nombre_comercial" not in fields and "afiliado_ple_desde" not in fields  # "-" => ausente, nunca inventado
    assert [a["tipo"] for a in fields["actividades_economicas"]] == ["PRINCIPAL", "SECUNDARIA"]
    assert fields["actividades_economicas"][0]["ciiu"] == "6201"
    assert run.ficha["malformed"] == []
    _clean(run)


def scenario_ficha_ruc_direct_access_without_extraction() -> None:  # FLOW A
    run = Run("m2_success", "FICHA_RUC", False)
    assert run.result.outcome is AuthOutcome.AUTH_SUCCEEDED and run.result.destination_reached
    assert run.ficha is None and "ficha" not in run.events
    assert run.result.post_landing_clicks == 0
    _clean(run)


def _assert_failed_closed(run: Run, *, clicks: int) -> None:
    r = run.result
    assert r.outcome is AuthOutcome.PORTAL_CHANGED, r
    assert r.destination_clicks == clicks and r.post_landing_clicks == 0
    assert "human_control" not in run.events and not r.destination_reached or clicks > 0
    assert "human_control" not in run.events
    _clean(run)


def scenario_missing_shell_marker_means_zero_clicks() -> None:
    run = Run("m2_no_marker", "FICHA_RUC", True)
    _assert_failed_closed(run, clicks=0)
    assert run.ficha is None


def scenario_lookalike_page_is_not_the_authenticated_shell() -> None:
    run = Run("m2_lookalike", "MENU_SOL", False)
    _assert_failed_closed(run, clicks=0)


def scenario_shell_with_login_controls_is_not_the_shell() -> None:
    run = Run("m2_login_like_shell", "MENU_SOL", False)
    _assert_failed_closed(run, clicks=0)


def scenario_ficha_entry_missing_fails_closed_without_clicks() -> None:
    run = Run("m2_no_ficha_entry", "FICHA_RUC", True)
    _assert_failed_closed(run, clicks=0)


def scenario_ficha_frame_identity_failure_never_extracts() -> None:
    run = Run("m2_ficha_bad_frame", "FICHA_RUC", True)
    _assert_failed_closed(run, clicks=4)
    assert run.ficha is None


def scenario_wrong_taxpayer_ficha_is_rejected_and_never_handed_over() -> None:
    run = Run("m2_ficha_wrong_ruc", "FICHA_RUC", True)
    r = run.result
    assert r.outcome is AuthOutcome.BROWSER_FAILURE and "human_control" not in run.events, (r, run.events)
    assert run.ficha["fields"]["ruc"] == m2_shell.OTHER_RUC
    assert r.post_landing_clicks == 0
    _clean(run)


def scenario_partial_ficha_omits_missing_sections_and_flags_malformed_values() -> None:
    run = Run("m2_ficha_partial", "FICHA_RUC", True)
    assert run.result.outcome is AuthOutcome.AUTH_SUCCEEDED, (run.result, run.events)
    fields = run.ficha["fields"]
    assert "sistema_contabilidad" not in fields and "padrones" not in fields  # ausentes: jamas inventados
    assert "fecha_inscripcion" not in fields and "fecha_inscripcion" in run.ficha["malformed"]
    assert fields["fecha_inicio_actividades"] == "2015-04-01"
    _clean(run)


def scenario_unknown_destination_and_misplaced_extraction_fail_closed_before_the_browser() -> None:
    for destination, extract in (("SIRE", False), ("SIRE_GENERACION_REGISTROS", False), ("DECLARA_FACIL", False), ("https://evil.example", False),
                                 ("MENU_SOL", True)):
        run = Run("m2_success", destination, extract)
        assert run.result.outcome is AuthOutcome.PORTAL_CHANGED, destination
        assert run.provider.fetches == 0 and run.executor._browser is None  # ni navegador ni credencial
        assert run.portal.requests == [] or all(r[2] != "/sol.html" for r in run.portal.requests)


def scenario_human_control_is_not_a_terminal_state_and_cancel_ends_it() -> None:
    run = Run("m2_success", "FICHA_RUC", False, close_after_hc=False, cancel_after_hc=True)
    assert run.result.outcome is AuthOutcome.CANCELLED, (run.result, run.events)
    assert run.events == ["human_control"] and run.result.post_landing_clicks == 0
    assert run.executor._browser is None  # el navegador se destruyo
    _clean(run)


def scenario_human_control_expires_and_browser_is_closed() -> None:
    run = Run("m2_success", "MENU_SOL", False, close_after_hc=False, hc_timeout=1.5)
    assert run.result.outcome is AuthOutcome.AUTH_SUCCEEDED and run.result.post_landing_clicks == 0
    assert run.executor._browser is None  # sin sesiones inmortales
    _clean(run)


def scenario_human_control_report_rejected_closes_the_browser() -> None:
    run = Run("m2_success", "MENU_SOL", False, hc_ok=False)
    assert run.result.outcome is AuthOutcome.BROWSER_FAILURE and run.executor._browser is None
    _clean(run)


def scenario_automation_is_frozen_after_the_destination_is_validated() -> None:
    """Defensa en profundidad: aunque algo intentara clicar tras el traspaso, `guarded_click` lo rechaza SIN clicar."""
    run = Run("m2_success", "MENU_SOL", False)
    ex = run.executor
    assert ex.destination_clicks == 0 and ex.post_landing_clicks == 0

    class _Boom:
        def click(self) -> None:
            raise AssertionError("un clic automatico llego al navegador tras el traspaso")

    try:
        ex.guarded_click(_Boom())
    except Exception as exc:  # noqa: BLE001
        assert type(exc).__name__ == "_AutomationStopped", exc
    else:
        raise AssertionError("guarded_click debio rechazar el clic")
    assert dc.MAX_CLICKS_BY_DESTINATION[dc.Destination.MENU_SOL] == 0


def scenario_caption_probe_writes_only_generic_labels_and_never_values() -> None:
    """La sonda de rotulos (solo desarrollo, apagada por defecto) con Chromium REAL: el informe contiene rotulos genericos
    y la forma de las filas; ningun valor de la Ficha sale del proceso y la sesion sigue igual (0 clics extra)."""
    import json
    import os
    import pathlib
    import tempfile

    from windows_agent import ficha_caption_probe as probe

    with tempfile.TemporaryDirectory() as tmp:
        os.environ[probe.PROBE_ENV] = tmp
        try:
            run = Run("m2_success", "FICHA_RUC", True)
        finally:
            os.environ.pop(probe.PROBE_ENV, None)
        assert run.result.outcome is AuthOutcome.AUTH_SUCCEEDED and run.result.post_landing_clicks == 0
        raw = (pathlib.Path(tmp) / "ficha_caption_probe.json").read_text(encoding="utf-8")
        report = json.loads(raw)
        labels = {r["label"] for r in report["rows"] if r.get("label")}
        assert {"domicilio fiscal", "estado del contribuyente", "fecha de inscripcion"} <= labels, labels
        for sentinel in (m2_shell.FICHA_RUC, "EMPRESA FICTICIA", "AV. FICTICIA", "2015", "6201"):
            assert sentinel not in raw, sentinel  # ningun valor de la Ficha
        assert report["parse"]["captions_found"] and report["parse"]["parser_captions_live_validated"] is False
        assert "ruc" in report["parse"]["field_keys"] and "razon_social" in report["parse"]["field_keys"]
    _clean(run)


# --------------------------------------------------------------------------- avisos intermedios de SUNAT / cierres inesperados


def _no_contact_side_effects(run: Run) -> None:
    """Jamas se pulso nada en el aviso: ni confirmar, ni recordar, ni actualizar datos de contacto."""
    assert not [r for r in run.forbidden if r[2].startswith("/contacto/")], run.forbidden
    _clean(run)


def scenario_notice_overlay_waits_for_the_human_without_any_click_then_continues() -> None:
    """Un aviso modal (confirmar telefono/correo) cubre el menu: NO se pulsa nada, se informa WAITING_FOR_USER, el navegador sigue
    abierto y, cuando el HUMANO cierra el aviso, se retoma la navegacion con el presupuesto intacto hasta la Ficha."""
    run = Run("m2_notice_overlay", "FICHA_RUC", True, dismiss_notice=True)
    r = run.result
    assert r.outcome is AuthOutcome.AUTH_SUCCEEDED, r
    waiting = run.events.index("state:WAITING_FOR_USER")
    assert waiting < run.events.index("human_dismisses_notice") < run.events.index("ficha") < run.events.index("human_control"), run.events
    assert r.destination_reached and r.ficha_reported and r.destination_clicks == 4 and r.post_landing_clicks == 0
    _no_contact_side_effects(run)


def scenario_notice_overlay_never_cleared_fails_closed_and_closes_the_browser() -> None:
    run = Run("m2_notice_overlay", "FICHA_RUC", True, human_wait=3.0)
    r = run.result
    assert r.outcome is AuthOutcome.PORTAL_CHANGED and r.terminal_reason_code == "PORTAL_CHANGED", r
    assert r.destination_clicks == 0 and r.post_landing_clicks == 0 and not r.destination_reached
    assert "state:WAITING_FOR_USER" in run.events and "human_control" not in run.events and "ficha" not in run.events
    assert run.executor._browser is None or not run.executor._pages()  # el navegador no queda abierto huerfano
    _no_contact_side_effects(run)


def scenario_cancel_while_the_notice_blocks_the_menu_clicks_nothing() -> None:
    run = Run("m2_notice_overlay", "FICHA_RUC", True, cancel_while_waiting=True)
    r = run.result
    assert r.outcome is AuthOutcome.CANCELLED, r
    assert r.destination_clicks == 0 and r.post_landing_clicks == 0 and "human_control" not in run.events
    _no_contact_side_effects(run)


def scenario_informational_popup_window_does_not_break_the_flow() -> None:
    run = Run("m2_notice_popup", "FICHA_RUC", True)
    r = run.result
    assert r.outcome is AuthOutcome.AUTH_SUCCEEDED, r
    assert r.destination_reached and r.ficha_reported and r.destination_clicks == 4 and r.post_landing_clicks == 0
    _no_contact_side_effects(run)


def scenario_popup_closed_by_the_human_while_another_page_stays_active() -> None:
    closed: list = []

    def human_closes_popup(run: Run, ex) -> None:
        if ex._browser is None:
            return  # el navegador aun no se lanzo
        popups = [pg for pg in ex._pages() if pg.url.endswith("/aviso.html")]
        if popups and not closed:
            closed.append(True)
            popups[0].close()  # el humano cierra la ventana emergente; la pagina principal sigue activa

    run = Run("m2_notice_popup", "FICHA_RUC", True, on_tick=human_closes_popup)
    r = run.result
    assert closed, "el escenario no genero una ventana emergente"
    assert r.outcome is AuthOutcome.AUTH_SUCCEEDED and r.destination_reached and r.ficha_reported, r
    _no_contact_side_effects(run)


def scenario_browser_closed_unexpectedly_mid_navigation_is_a_terminal_failure() -> None:
    def kill_browser(run: Run, ex) -> None:
        if ex.destination_clicks >= 1 and ex._browser is not None:
            run.events.append("browser_crashes")
            ex._browser.close()

    run = Run("m2_success", "FICHA_RUC", True, on_tick=kill_browser)
    r = run.result
    assert "browser_crashes" in run.events
    assert r.outcome is AuthOutcome.BROWSER_FAILURE and r.terminal_reason_code == "BROWSER_CLOSED", r
    assert "human_control" not in run.events and not r.ficha_reported
    _clean(run)


def scenario_playwright_timeout_during_navigation_is_a_controlled_failure_never_an_exception() -> None:
    from unittest.mock import patch

    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    from windows_agent.destination_executor import DestinationNavigator

    def boom(self, page):
        raise PlaywrightTimeoutError("Locator.click: Timeout 30000ms exceeded.")

    with patch.object(DestinationNavigator, "_navigate_ficha", boom):
        run = Run("m2_success", "FICHA_RUC", True)
    r = run.result  # no se propago ninguna excepcion
    assert r.outcome is AuthOutcome.PORTAL_CHANGED and r.terminal_reason_code == "PORTAL_CHANGED", r
    assert "human_control" not in run.events and not r.destination_reached
    assert run.executor._browser is None or not run.executor._pages()
    _clean(run)


def scenario_blocker_description_is_structural_and_sanitized() -> None:
    from windows_agent.browser_executor import describe_blocker

    message = (
        'Locator.click: Timeout 2500ms exceeded.\nCall log:\n  - <div id="divAvisoContacto" class="modal-aviso big">Confirme su '
        "telefono 999888777</div> intercepts pointer events\n  - retrying click action"
    )
    described = describe_blocker(Exception(message))
    assert described == "div#divAvisoContacto.modal-aviso.big", described
    assert "999888777" not in described and "Confirme" not in described
    assert describe_blocker(Exception("otro error")) == ""
    hostile = describe_blocker(Exception('<div id="a b<script>" class="x y">t</div> intercepts pointer events'))
    assert "<" not in hostile and "script" not in hostile


# --------------------------------------------------------------------------- diagnostico estructural saneado por etapa


def _diag(run: Run) -> tuple[str | None, str | None, dict]:
    r = run.result
    return r.stage, r.diagnostic_code, r.observation or {}


def scenario_pre_credential_failures_name_the_stage_and_never_consume_the_credential() -> None:
    """El rechazo de identidad ANTERIOR a la credencial deja de ser un generico: etapa, codigo estable y observacion
    (contadores/booleanos) por categoria; la credencial NUNCA se pide ni se entrega."""
    cases = {
        "no_heading": ("ENTRY", "ENTRY_LINK_NOT_FOUND", lambda o: o["entry"]["heading_count"] == 0),
        "no_entry_link": ("ENTRY", "ENTRY_LINK_NOT_FOUND", lambda o: o["entry"]["heading_count"] == 1 and o["entry"]["link_count"] == 0),
        "ruc_wrong_maxlength": (
            "LOGIN_FORM", "LOGIN_CONTROL_MISMATCH",
            lambda o: o["login"]["controls"]["txtRuc"]["count"] == 1 and o["login"]["controls"]["txtRuc"]["maxlength_ok"] is False,
        ),
        "no_txtRuc": ("LOGIN_FORM", "LOGIN_CONTROL_MISMATCH", lambda o: o["login"]["controls"]["txtRuc"]["count"] == 0),
        "dup_txtRuc": ("LOGIN_FORM", "LOGIN_CONTROL_MISMATCH", lambda o: o["login"]["controls"]["txtRuc"]["count"] == 2),
    }
    for name, (stage, code, observed) in cases.items():
        run = Run(name, "FICHA_RUC", True)
        got_stage, got_code, obs = _diag(run)
        assert (got_stage, got_code) == (stage, code), (name, got_stage, got_code, run.result)
        assert observed(obs), (name, obs)
        assert obs["page_count"] >= 1 and obs["last_safe_action"] in {"ENTRY_LOADED", "ENTRY_LINK_CLICKED"}, (name, obs)
        assert not run.result.shell_reached and not run.result.destination_reached
        assert run.result.credential_submissions == 0 and run.provider.fetches == 0, name
        _clean(run)


def scenario_shell_and_destination_failures_name_the_stage_and_flags() -> None:
    cases = {
        "m2_no_marker": ("SHELL", "SHELL_MARKER_MISSING_OR_AMBIGUOUS", False, 0),
        "m2_lookalike": ("SHELL", "SHELL_MARKER_MISSING_OR_AMBIGUOUS", False, 0),
        "m2_no_ficha_entry": ("NAVIGATION", "FICHA_MENU_ENTRY_MISSING_OR_AMBIGUOUS", True, 0),
        "m2_ficha_bad_frame": ("DESTINATION", "FICHA_FRAME_NOT_VALIDATED", True, 4),
    }
    for name, (stage, code, shell, clicks) in cases.items():
        run = Run(name, "FICHA_RUC", True, destination_wait=3.0)
        got_stage, got_code, obs = _diag(run)
        assert (got_stage, got_code) == (stage, code), (name, got_stage, got_code, run.result)
        assert run.result.shell_reached is shell and run.result.destination_clicks == clicks, (name, run.result)
        assert obs["surface_kind"] == "LANDING" and obs["page_count"] >= 1, (name, obs)
        assert run.result.post_landing_clicks == 0 and "human_control" not in run.events
        _clean(run)


def scenario_blocked_notice_and_timeouts_are_distinguished_from_identity_failures() -> None:
    run = Run("m2_notice_overlay", "FICHA_RUC", True, human_wait=3.0)
    stage, code, obs = _diag(run)
    assert (stage, code) == ("NAVIGATION", "BLOCKED_BY_INTERMEDIATE_SCREEN"), run.result
    assert run.result.shell_reached and obs["last_safe_action"] == "SHELL_VALIDATED"  # el shell SI se alcanzo y valido
    _no_contact_side_effects(run)

    from unittest.mock import patch

    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    from windows_agent.destination_executor import DestinationNavigator

    def boom(self, page):
        raise PlaywrightTimeoutError("Locator.click: Timeout 30000ms exceeded.")

    with patch.object(DestinationNavigator, "_navigate_ficha", boom):
        timed_out = Run("m2_success", "FICHA_RUC", True)
    stage, code, _obs = _diag(timed_out)
    assert (stage, code) == ("NAVIGATION", "NAVIGATION_TIMEOUT") and timed_out.result.shell_reached, timed_out.result
    _clean(timed_out)


def scenario_observation_contains_only_counters_categories_and_booleans() -> None:
    import json

    run = Run("m2_notice_popup", "FICHA_RUC", True, destination_wait=3.0)  # exito: sin diagnostico
    assert run.result.diagnostic_code is None and run.result.shell_reached
    failing = Run("ruc_wrong_maxlength", "FICHA_RUC", True)
    blob = json.dumps(failing.result.observation or {})
    for forbidden in ("://", "localhost", "://", "/v1/", "txtUsuario@", "Contraseña", "password=", m2_shell.FICHA_RUC):
        assert forbidden not in blob, forbidden
    allowed_scalars = (bool, int, str, type(None))

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                assert isinstance(key, str) and len(key) <= 40
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        else:
            assert isinstance(node, allowed_scalars)
            if isinstance(node, str):
                assert len(node) <= 40 and "/" not in node and " " not in node, node  # etiquetas, nunca texto del portal

    walk(failing.result.observation)


def _load_preflight():
    import importlib.util

    script = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "m2_portal_preflight.py"
    spec = importlib.util.spec_from_file_location("m2_portal_preflight", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scenario_portal_preflight_reads_structure_without_credentials() -> None:
    """El preflight (herramienta de reconocimiento minimo) solo ejecuta la fase PREVIA a la credencial: jamas pide ni envia una clave,
    y su informe contiene solo contadores/booleanos (nada del portal)."""
    import json

    preflight = _load_preflight()
    ok_portal = FakeSolPortal().start()
    try:
        ok_portal.set_scenario("m2_success")
        ok = preflight.run_preflight(
            test_contract(ok_portal), headless=False, navigation_timeout_seconds=15.0, settle_grace_seconds=1.0,
            poll_interval_seconds=0.1, controls_wait_seconds=1.0, pre_credential_attempts=1,
        )
        assert ok["login_ready"] is True and ok["credential_submissions"] == 0 and ok["credentials_requested"] is False
        assert ok["last_safe_action"] == "LOGIN_FORM_VALIDATED"
        controls = ok["observation"]["login"]["controls"]
        assert all(c["count"] == 1 and c["tag_ok"] and c["type_ok"] for c in controls.values()), controls
        assert ok_portal.submissions == [] and ok_portal.forbidden_requests() == []  # nada se envio al portal
    finally:
        ok_portal.stop()

    bad_portal = FakeSolPortal().start()
    try:
        bad_portal.set_scenario("ruc_wrong_maxlength")
        bad = preflight.run_preflight(
            test_contract(bad_portal), headless=False, navigation_timeout_seconds=15.0, settle_grace_seconds=1.0,
            poll_interval_seconds=0.1, controls_wait_seconds=1.0, pre_credential_attempts=1,
        )
        assert bad["login_ready"] is False and bad["diagnostic_code"] == "LOGIN_CONTROL_MISMATCH" and bad["stage"] == "LOGIN_FORM"
        assert bad["observation"]["login"]["controls"]["txtRuc"]["maxlength_ok"] is False
        assert bad["credential_submissions"] == 0 and bad_portal.submissions == []
        blob = json.dumps(bad)
        for forbidden in ("://", "localhost", "://"):
            assert forbidden not in blob, forbidden
    finally:
        bad_portal.stop()


# --------------------------------------------------------------------------- salto del host de aterrizaje ANTES del login (observado en vivo)


def scenario_login_behind_a_landing_host_hop_is_waited_for_and_then_validated() -> None:
    """Preflight real: en ~50 % de las cargas el login pasa por una pagina del host de aterrizaje con la ruta del menu y un titulo
    transitorio. Se espera (sin clics ni credenciales) y, solo cuando aparece el login con TODOS sus controles, se acepta."""
    for name in ("m2_login_hop", "m2_login_hop_empty_title"):
        run = Run(name, "MENU_SOL", False)
        r = run.result
        assert r.outcome is AuthOutcome.AUTH_SUCCEEDED, (name, r)
        timeline = r.observation or {}
        assert run.provider.fetches == 1 and r.credential_submissions == 1, name
        _clean(run)


def scenario_hop_that_never_resolves_fails_closed_before_any_credential() -> None:
    for name, expected_code in (("m2_login_hop_never", "LANDING_IDENTITY_MISMATCH"), ("m2_login_hop_wrong_path", "LANDING_IDENTITY_MISMATCH")):
        run = Run(name, "MENU_SOL", False, destination_wait=2.0)
        r = run.result
        assert r.outcome is AuthOutcome.PORTAL_CHANGED and r.diagnostic_code == expected_code, (name, r)
        assert r.stage == "LANDING" and r.credential_submissions == 0, (name, r)
        assert run.provider.fetches == 0, name  # la credencial NUNCA se pidio antes de validar la identidad
        timeline = (r.observation or {}).get("timeline", [])
        assert timeline and timeline[-1]["kind"] == ("PENDING" if name == "m2_login_hop_never" else "FAIL"), (name, timeline)
        _clean(run)


def scenario_preflight_waits_through_the_hop_and_reports_login_ready() -> None:
    preflight = _load_preflight()
    portal = FakeSolPortal().start()
    try:
        portal.set_scenario("m2_login_hop")
        report = preflight.run_preflight(
            test_contract(portal), headless=False, navigation_timeout_seconds=15.0, settle_grace_seconds=1.0,
            poll_interval_seconds=0.1, controls_wait_seconds=1.0, pre_credential_attempts=1, landing_hop_grace_seconds=5.0,
        )
        kinds = [step["kind"] for step in report["observation"]["timeline"]]
        assert report["login_ready"] is True and report["credential_submissions"] == 0 and "PENDING" in kinds and kinds[-1] == "LOGIN", report
        assert portal.submissions == []
    finally:
        portal.stop()


# --------------------------------------------------------------------------- maquina de estados de PREAUTENTICACION (A-F), evidencia del preflight real


def _preauth_timeline(run: Run) -> list[dict]:
    return (run.result.observation or {}).get("timeline", [])


def scenario_transit_with_menu_title_but_no_authenticated_marker_is_pending_then_login() -> None:
    """Evidencia real (preflight, 3/12): el transito del host de aterrizaje ya trae el titulo del Menu SOL pero NINGUN id del shell.
    Host + ruta + titulo NO son un shell autenticado: se espera (estado B) y solo al aparecer el login completo (C) se entrega la
    credencial UNA vez (D) y, tras autenticar, el shell exige su marcador DOM positivo (E)."""
    run = Run("m2_login_hop_menu_title", "MENU_SOL", False)
    r = run.result
    assert r.outcome is AuthOutcome.AUTH_SUCCEEDED and r.shell_reached, r
    assert run.provider.fetches == 1 and r.credential_submissions == 1  # una sola entrega
    _clean(run)
    # la credencial nunca se entrego antes del login: el unico envio al portal ocurrio en la pagina de login
    assert len(run.portal.submissions) == 1


def scenario_transit_with_menu_title_that_never_resolves_fails_closed_without_credentials() -> None:
    run = Run("m2_login_hop_menu_title_never", "MENU_SOL", False)
    r = run.result
    assert r.outcome is AuthOutcome.PORTAL_CHANGED and r.diagnostic_code == "LANDING_TRANSIT_UNRESOLVED" and r.stage == "LOGIN_SURFACE", r
    assert r.credential_submissions == 0 and run.provider.fetches == 0 and run.portal.submissions == []
    timeline = _preauth_timeline(run)
    landing = [step for step in timeline if step["kind"] == "LANDING"]
    assert landing and landing[-1]["shell"]["marker_ok"] is False and landing[-1]["shell"]["ids"] == []  # sin ningun id del shell
    assert run.executor.destination_clicks == 0 and run.executor.post_landing_clicks == 0  # nada se pulso en la pagina ambigua
    _clean(run)


def scenario_authenticated_shell_before_credentials_fails_closed() -> None:
    """Un shell autenticado COMPLETO (marcador positivo) antes de entregar credenciales es una sesion inesperada: fallo cerrado."""
    run = Run("m2_login_hop_shell", "MENU_SOL", False)
    r = run.result
    assert r.outcome is AuthOutcome.PORTAL_CHANGED and r.diagnostic_code == "LANDING_BEFORE_AUTHENTICATION", r
    assert r.credential_submissions == 0 and run.provider.fetches == 0 and run.portal.submissions == []
    landing = [step for step in _preauth_timeline(run) if step["kind"] == "LANDING" and step.get("shell")]
    assert landing and landing[-1]["shell"]["marker_ok"] is True
    _clean(run)


def scenario_two_simultaneous_pages_and_unexpected_popups_fail_closed_before_credentials() -> None:
    for name in ("m2_login_hop_new_page", "m2_login_hop_popup"):
        run = Run(name, "MENU_SOL", False, hop_delay_ms=600, hop_grace=2.0)
        r = run.result
        popup_ok = name == "m2_login_hop_popup"
        # transito + login en dos Pages a la vez, o transito + aviso emergente en el mismo host: superficie ambigua => fallo cerrado
        assert r.outcome is AuthOutcome.PORTAL_CHANGED, (name, r)
        assert r.credential_submissions == 0 and run.provider.fetches == 0 and run.portal.submissions == [], name
        assert r.diagnostic_code in {"AMBIGUOUS_PORTAL_SURFACE", "LANDING_TRANSIT_UNRESOLVED", "LANDING_IDENTITY_MISMATCH"}, (name, r.diagnostic_code, popup_ok)
        _clean(run)


def scenario_chromium_closed_or_cancelled_or_expired_during_the_transit_is_terminal_and_credential_free() -> None:
    def close_browser(run: Run, ex) -> None:
        if ex._browser is not None and any(s["kind"] in {"PENDING", "LANDING"} for s in ex._timeline) and "closed" not in run.events:
            run.events.append("closed")
            ex._browser.close()

    closed = Run("m2_login_hop_menu_title_never", "MENU_SOL", False, on_tick=close_browser, hop_grace=8.0, attempts=1)
    assert "closed" in closed.events
    assert closed.result.outcome is AuthOutcome.BROWSER_FAILURE and closed.result.credential_submissions == 0 and closed.provider.fetches == 0
    _clean(closed)
    # con el reintento acotado previo a la credencial (2 intentos) el segundo tampoco entrega nada: sigue siendo terminal y sin credencial
    retried = Run("m2_login_hop_menu_title_never", "MENU_SOL", False, on_tick=close_browser, hop_grace=2.0, attempts=2)
    assert "closed" in retried.events and retried.result.outcome in {AuthOutcome.BROWSER_FAILURE, AuthOutcome.PORTAL_CHANGED}
    assert retried.result.credential_submissions == 0 and retried.provider.fetches == 0
    _clean(retried)

    def cancel(run: Run, ex) -> None:
        if ex._browser is not None and any(s["kind"] in {"PENDING", "LANDING"} for s in ex._timeline) and "cancel_requested" not in run.events:
            run.events.append("cancel_requested")

    cancelled = Run("m2_login_hop_menu_title_never", "MENU_SOL", False, on_tick=cancel, hop_grace=8.0, attempts=1)
    assert cancelled.result.outcome is AuthOutcome.CANCELLED and cancelled.result.credential_submissions == 0 and cancelled.provider.fetches == 0
    _clean(cancelled)

    expired = Run("m2_login_hop_menu_title_never", "MENU_SOL", False, hop_grace=1.0)  # expiracion del plazo acotado
    assert expired.result.diagnostic_code == "LANDING_TRANSIT_UNRESOLVED" and expired.provider.fetches == 0
    _clean(expired)


def scenario_repeated_transits_under_latency_and_cpu_load_are_stable() -> None:
    """Sin intermitencias: el salto con titulo del Menu SOL, vacio o transitorio, con retardos distintos y la CPU saturada, siempre termina
    en login valido (una sola entrega de credencial) y nunca en un rechazo prematuro."""
    import threading
    import time as _time

    stop = threading.Event()

    def burn() -> None:
        while not stop.is_set():
            sum(i * i for i in range(20000))

    workers = [threading.Thread(target=burn, daemon=True) for _ in range(4)]
    for w in workers:
        w.start()
    try:
        outcomes = []
        for name, delay in (("m2_login_hop", 150), ("m2_login_hop_menu_title", 300), ("m2_login_hop_empty_title", 900),
                            ("m2_login_hop_menu_title", 1500), ("m2_login_hop", 50), ("m2_login_hop_menu_title", 2500)):
            run = Run(name, "MENU_SOL", False, hop_delay_ms=delay, hop_grace=6.0)
            outcomes.append((name, delay, run.result.outcome.value, run.provider.fetches))
            assert run.result.outcome is AuthOutcome.AUTH_SUCCEEDED and run.provider.fetches == 1, outcomes
            _clean(run)
    finally:
        stop.set()
        _time.sleep(0.2)


def scenario_login_with_incomplete_or_duplicated_controls_never_receives_the_credential_after_a_transit() -> None:
    for name in ("no_txtRuc", "dup_txtRuc", "no_txtUsuario"):
        run = Run(name, "MENU_SOL", False)
        assert run.result.diagnostic_code == "LOGIN_CONTROL_MISMATCH" and run.provider.fetches == 0 and run.portal.submissions == [], name
        _clean(run)


# --------------------------------------------------------------------------- aviso de validacion de contacto (evidencia real) y Ficha con estructura real


def _contact_untouched(run: Run) -> None:
    """Cero acciones automaticas sobre los controles de contacto (Finalizar / enviar codigo / confirmar / actualizar)."""
    assert [r for r in run.forbidden if r[2].startswith("/contacto/")] == [], run.forbidden
    _clean(run)


def scenario_contact_validation_interstitial_waits_for_the_human_then_revalidates_and_reaches_the_ficha() -> None:
    run = Run("m2_contact_interstitial", "FICHA_RUC", True, dismiss_notice=True)
    r = run.result
    assert r.outcome is AuthOutcome.AUTH_SUCCEEDED, r
    assert run.executor.waiting_reason == "CONTACT_VALIDATION_INTERSTITIAL", run.executor.waiting_reason
    waiting = run.events.index("state:WAITING_FOR_USER")
    assert waiting < run.events.index("human_dismisses_notice") < run.events.index("ficha") < run.events.index("human_control"), run.events
    assert "browser_open_while_waiting" in run.events  # Chromium permanece abierto durante la espera
    assert r.destination_reached and r.ficha_reported and r.destination_clicks == 4 and r.post_landing_clicks == 0
    _contact_untouched(run)


def scenario_two_consecutive_modals_need_two_human_interventions() -> None:
    run = Run("m2_contact_two_modals", "FICHA_RUC", True, dismiss_notice=True, dismiss_times=2)
    r = run.result
    assert r.outcome is AuthOutcome.AUTH_SUCCEEDED and r.ficha_reported, r
    assert run.events.count("human_dismisses_notice") == 2
    assert r.destination_clicks == 4 and r.post_landing_clicks == 0
    _contact_untouched(run)


def scenario_mandatory_contact_validation_without_a_way_out_is_an_explicit_recoverable_block() -> None:
    """Sin opcion de omision (o el modal sigue bloqueando tras la 'intervencion'): el navegador sigue abierto durante la espera acotada y despues el
    flujo termina con un bloqueo EXPLICITO (nada se fuerza ni se automatiza)."""
    run = Run("m2_contact_mandatory", "FICHA_RUC", True, dismiss_notice=True, human_wait=12.0)
    r = run.result
    assert r.outcome is AuthOutcome.PORTAL_CHANGED and r.diagnostic_code == "BLOCKED_BY_INTERMEDIATE_SCREEN" and r.stage == "NAVIGATION", r
    assert run.executor.waiting_reason == "CONTACT_VALIDATION_INTERSTITIAL"
    assert "browser_open_while_waiting" in run.events and "human_dismisses_notice" in run.events  # el humano lo intento: sigue bloqueando
    assert r.shell_reached and r.destination_clicks == 0 and "ficha" not in run.events and "human_control" not in run.events
    _contact_untouched(run)


def scenario_informative_modal_is_classified_without_touching_it() -> None:
    run = Run("m2_notice_informative", "FICHA_RUC", True, dismiss_notice=True)
    assert run.result.outcome is AuthOutcome.AUTH_SUCCEEDED
    assert run.executor.waiting_reason == "INFORMATIONAL_MODAL", run.executor.waiting_reason
    _contact_untouched(run)


def scenario_losing_the_shell_during_the_human_intervention_fails_closed_instead_of_continuing() -> None:
    """Tras la intervencion humana el shell se REVALIDA: si la sesion/estructura ya no es la validada, no se continua ni se pulsa nada."""

    def human_breaks_the_page(run: Run, ex) -> None:
        if "state:WAITING_FOR_USER" in run.events and "broken" not in run.events and ex._browser is not None:
            run.events.append("broken")
            for pg in ex._pages():
                pg.evaluate("(() => { const h = document.getElementById('divCabecera'); if (h) h.remove(); })()")

    run = Run("m2_contact_mandatory", "FICHA_RUC", True, on_tick=human_breaks_the_page, human_wait=10.0)
    r = run.result
    assert r.outcome is AuthOutcome.PORTAL_CHANGED and r.stage == "SHELL" and r.diagnostic_code == "SHELL_MARKER_MISSING_OR_AMBIGUOUS", r
    assert r.destination_clicks == 0 and "human_control" not in run.events
    _contact_untouched(run)


def scenario_chromium_closed_or_cancelled_during_the_contact_intervention_is_terminal() -> None:
    def close_browser(run: Run, ex) -> None:
        if "state:WAITING_FOR_USER" in run.events and "closed" not in run.events and ex._browser is not None:
            run.events.append("closed")
            ex._browser.close()

    closed = Run("m2_contact_mandatory", "FICHA_RUC", True, on_tick=close_browser, human_wait=20.0)
    assert closed.result.outcome is AuthOutcome.BROWSER_FAILURE and closed.result.destination_clicks == 0
    _contact_untouched(closed)
    cancelled = Run("m2_contact_mandatory", "FICHA_RUC", True, cancel_while_waiting=True, human_wait=20.0)
    assert cancelled.result.outcome is AuthOutcome.CANCELLED and cancelled.result.destination_clicks == 0
    _contact_untouched(cancelled)


def scenario_real_ficha_structure_is_extracted_and_the_ruc_comes_from_the_official_header() -> None:
    run = Run("m2_ficha_live", "FICHA_RUC", True)
    r = run.result
    assert r.outcome is AuthOutcome.AUTH_SUCCEEDED and r.ficha_reported, r
    fields = run.ficha["fields"]
    assert fields["ruc"] == m2_shell.FICHA_RUC and fields["razon_social"] == m2_shell._LIVE_NAME
    assert fields["fecha_inscripcion"] == "2015-03-15" and fields["fecha_inicio_actividades"] == "2015-04-01" and run.ficha["malformed"] == []
    assert [a["tipo"] for a in fields["actividades_economicas"]] == ["PRINCIPAL", "SECUNDARIA"]
    assert "nombre_comercial" not in fields and fields["estado_contribuyente"] == "ACTIVO"
    assert run.events[:2] == ["ficha", "human_control"] and r.post_landing_clicks == 0
    _clean(run)


def scenario_header_variants_nested_spaced_and_prefixed_activities_are_extracted() -> None:
    for name in ("m2_ficha_live_nested", "m2_ficha_live_spaced", "m2_ficha_live_prefixed_activities"):
        run = Run(name, "FICHA_RUC", True)
        assert run.result.outcome is AuthOutcome.AUTH_SUCCEEDED and run.result.ficha_reported, (name, run.result)
        assert run.ficha["fields"]["ruc"] == m2_shell.FICHA_RUC and run.ficha["fields"]["razon_social"] == m2_shell._LIVE_NAME, name
        assert [a["tipo"] for a in run.ficha["fields"]["actividades_economicas"]] == ["PRINCIPAL", "SECUNDARIA"], name
        _clean(run)


def scenario_duplicated_misplaced_or_incidental_identifiers_fail_closed() -> None:
    for name in ("m2_ficha_live_dup_header", "m2_ficha_live_header_not_first", "m2_ficha_live_incidental_number"):
        run = Run(name, "FICHA_RUC", True)
        assert run.result.diagnostic_code == "FICHA_TAXPAYER_NOT_IDENTIFIED" and run.result.stage == "EXTRACTION", (name, run.result)
        assert "ficha" not in run.events and "human_control" not in run.events, name
        _clean(run)


def scenario_ficha_without_a_ruc_cell_or_with_another_ruc_never_reports_a_snapshot() -> None:
    no_cell = Run("m2_ficha_live_no_ruc_cell", "FICHA_RUC", True)
    assert no_cell.result.diagnostic_code == "FICHA_TAXPAYER_NOT_IDENTIFIED" and no_cell.result.stage == "EXTRACTION"
    assert "ficha" not in no_cell.events and "human_control" not in no_cell.events and no_cell.result.destination_reached
    _clean(no_cell)
    other = Run("m2_ficha_live_wrong_ruc", "FICHA_RUC", True)  # la celda trae OTRO RUC: el extractor lo lee, el RUC solicitado no coincide
    assert other.result.diagnostic_code == "FICHA_REPORT_REJECTED" and other.result.stage == "REPORT", other.result  # el backend la rechaza (WRONG_TAXPAYER_CONTEXT)
    assert "human_control" not in other.events
    _clean(other)


def scenario_ficha_still_loading_is_awaited_without_clicks_and_a_partial_document_is_not_success() -> None:
    loading = Run("m2_ficha_live_loading", "FICHA_RUC", True, destination_wait=10.0)
    assert loading.result.outcome is AuthOutcome.AUTH_SUCCEEDED and loading.result.ficha_reported, loading.result
    assert loading.result.destination_clicks == 4 and loading.result.post_landing_clicks == 0  # la espera no hizo ningun clic
    _clean(loading)
    partial = Run("m2_ficha_live_never_loads", "FICHA_RUC", True, destination_wait=3.0)
    assert partial.result.diagnostic_code == "FICHA_CONTENT_NOT_READY" and partial.result.stage == "EXTRACTION", partial.result
    assert partial.result.destination_reached and "ficha" not in partial.events and "human_control" not in partial.events
    _clean(partial)


def _released_when_waiting(run: "Run", ex) -> None:
    if "state:WAITING_FOR_USER" in run.events and "challenge_released" not in run.events:
        run.events.append("challenge_released")
        run.portal.release_challenge()  # el HUMANO completa la verificacion


def scenario_welcome_window_that_redirects_by_itself_is_observed_without_a_human_state() -> None:
    """«Bienvenidos a SUNAT» tras el envio: ruta/titulo distintos del login y sin formulario. Antes se trataba como un posible desafio
    (WAITING_FOR_USER) a los pocos segundos; ahora es navegacion pendiente observada de forma pasiva hasta que redirige."""
    for name in ("m2_auth_welcome_redirect", "m2_auth_welcome_chain", "m2_auth_welcome_slow"):
        run = Run(name, "MENU_SOL", False, auth_nav=15.0)  # `slow` redirige a los 4 s: mas que la antigua gracia de 1.5 s
        r = run.result
        assert r.outcome is AuthOutcome.AUTH_SUCCEEDED and r.shell_reached, (name, r)
        assert "state:WAITING_FOR_USER" not in run.events, (name, run.events)
        assert r.credential_submissions == 1 and run.provider.fetches == 1, name
        _clean(run)


def scenario_welcome_window_that_never_advances_is_a_stalled_navigation_not_a_human_wait() -> None:
    for name in ("m2_auth_welcome_stuck", "m2_auth_welcome_empty"):
        run = Run(name, "MENU_SOL", False, auth_nav=2.5)
        r = run.result
        assert r.outcome is AuthOutcome.PORTAL_CHANGED and r.diagnostic_code == "AUTH_NAVIGATION_STALLED" and r.stage == "SUBMIT", (name, r)
        assert "state:WAITING_FOR_USER" not in run.events, (name, run.events)  # nunca se pide una verificacion inexistente
        assert r.credential_submissions == 1 and run.provider.fetches == 1  # la credencial se consumio y NO se reenvia
        assert r.destination_clicks == 0 and r.post_landing_clicks == 0 and not r.shell_reached
        transit = (r.observation or {}).get("auth_transit")
        assert transit is not None and set(transit) >= {"visible_inputs", "forms", "iframes"}, r.observation
        assert transit["visible_inputs"] == 0 and transit["visible_buttons"] == 0, transit  # estructura: sin ningun control humano
        text = repr(r.observation)
        assert "Bienvenidos" not in text and "fixture" not in text.lower() and "http" not in text  # nada de textos ni URLs
        _clean(run)


def scenario_a_positively_identified_human_challenge_is_the_only_human_wait() -> None:
    run = Run("m2_auth_welcome_challenge", "MENU_SOL", False, on_tick=_released_when_waiting, auth_nav=15.0, human_wait=30.0)
    r = run.result
    assert r.outcome is AuthOutcome.AUTH_SUCCEEDED, r
    assert "state:WAITING_FOR_USER" in run.events and run.executor.waiting_reason == "HUMAN_ACTION_REQUIRED", run.events
    assert run.events.index("challenge_released") > run.events.index("state:WAITING_FOR_USER")
    assert "browser_open_while_waiting" in run.events and r.credential_submissions == 1
    _clean(run)
    never = Run("m2_auth_welcome_challenge", "MENU_SOL", False, human_wait=2.0, auth_nav=30.0)  # nadie lo libera: tope de la espera humana
    assert never.result.outcome is AuthOutcome.EXPIRED and never.result.diagnostic_code == "HUMAN_CHALLENGE_TIMEOUT", never.result
    assert "state:WAITING_FOR_USER" in never.events
    _clean(never)


def scenario_extra_window_next_to_the_auth_page_fails_closed_instead_of_waiting() -> None:
    run = Run("m2_auth_welcome_popup", "MENU_SOL", False, auth_nav=15.0)
    r = run.result
    assert r.outcome is AuthOutcome.PORTAL_CHANGED and r.diagnostic_code == "AMBIGUOUS_PORTAL_SURFACE", r
    assert "state:WAITING_FOR_USER" not in run.events and r.credential_submissions == 1
    _clean(run)


def scenario_browser_closed_cancelled_during_the_welcome_window_is_terminal() -> None:
    def close_browser(run: "Run", ex) -> None:
        if ex.credential_submissions == 1 and "closed" not in run.events and ex._browser is not None:
            run.events.append("closed")
            ex._browser.close()  # el usuario cierra Chromium mientras «Bienvenidos a SUNAT» no avanza

    closed = Run("m2_auth_welcome_stuck", "MENU_SOL", False, on_tick=close_browser, auth_nav=30.0)
    assert closed.result.outcome is AuthOutcome.BROWSER_FAILURE and closed.result.terminal_reason_code == "BROWSER_CLOSED", closed.result
    assert closed.result.diagnostic_code == "BROWSER_CLOSED_OR_FAILED" and closed.result.credential_submissions == 1
    _clean(closed)

    def cancel(run: "Run", ex) -> None:
        if ex.credential_submissions == 1 and "cancel_requested" not in run.events:
            run.events.append("cancel_requested")

    cancelled = Run("m2_auth_welcome_stuck", "MENU_SOL", False, on_tick=cancel, auth_nav=30.0)
    assert cancelled.result.outcome is AuthOutcome.CANCELLED and cancelled.result.credential_submissions == 1, cancelled.result
    _clean(cancelled)


def scenario_failure_before_and_after_the_credential_report_the_right_submission_count() -> None:
    before = Run("m2_login_hop_never", "MENU_SOL", False, hop_grace=1.0)  # el login nunca aparece: la credencial NO se entrega
    assert before.result.credential_submissions == 0 and before.provider.fetches == 0, before.result
    after = Run("m2_auth_welcome_stuck", "MENU_SOL", False, auth_nav=1.5)
    assert after.result.credential_submissions == 1 and after.provider.fetches == 1, after.result


STALL_CASES = (
    ("m2_auth_welcome_stuck", "STABLE_DOCUMENT_NO_CONTINUATION"),
    ("m2_auth_welcome_script_missing", "RESOURCE_HTTP_ERROR"),
    ("m2_auth_welcome_script_unreachable", "RESOURCE_LOAD_FAILED"),
    ("m2_auth_welcome_server_error", "SERVER_ERROR_DOCUMENT"),
    ("m2_auth_welcome_js_error", "SCRIPT_ERROR"),
    ("m2_auth_welcome_hang", "NAVIGATION_PENDING_NO_RESPONSE"),
)


def scenario_auth_trace_explains_why_the_authentication_stalls_without_leaking_anything() -> None:
    """Proveedor sintetico: tras el envio la pagina de autenticacion se detiene por causas DISTINTAS (documento estable, JS 404, JS
    inalcanzable, error 500, error de script, redireccion sin respuesta). La traza saneada debe distinguirlas con evidencia."""
    for name, expected in STALL_CASES:
        run = Run(name, "MENU_SOL", False, auth_nav=3.0)
        r = run.result
        assert r.diagnostic_code == "AUTH_NAVIGATION_STALLED", (name, r.diagnostic_code, r.detail)
        obs = r.observation or {}
        assert obs.get("stall_reason") == expected, (name, obs.get("stall_reason"), obs.get("auth_trace"), obs.get("auth_document"))
        trace = obs["auth_trace"]
        assert any(e["e"] == "navigated" for e in trace["events"]) and trace["counters"], name
        doc = obs["auth_document"]
        assert doc["ready_state"] in {"loading", "interactive", "complete", "pending_navigation"}, (name, doc)
        if doc["ready_state"] != "pending_navigation":  # con una navegacion pendiente el documento NO se lee (se bloquearia)
            assert "webdriver_flag" in doc and "has_opener" in doc, (name, doc)
        text = repr(obs).lower()
        for forbidden in ("://", "localhost", "fixture", "bienvenidos", "cookie", "authorization", "bearer"):
            assert forbidden not in text, (name, forbidden)
        assert r.credential_submissions == 1 and "state:WAITING_FOR_USER" not in run.events
        _clean(run)


SENSITIVE_SENTINELS = ("20123456786", "JUAN-PEREZ", "CODE-SECRET-123", "STATE-SECRET-456", "TOKEN-SECRET-789", "juan", "perez", "access_token")


def scenario_trace_never_records_identifiers_codes_state_fragments_or_urls() -> None:
    """Ruta, query y fragmento con datos sensibles SINTETICOS: la traza solo guarda categorias opacas y banderas booleanas."""
    import json

    run = Run("m2_auth_welcome_sensitive", "MENU_SOL", False, auth_nav=2.5)
    r = run.result
    assert r.diagnostic_code == "AUTH_NAVIGATION_STALLED", r
    blob = json.dumps(r.observation, ensure_ascii=False)
    for sentinel in SENSITIVE_SENTINELS:
        assert sentinel.lower() not in blob.lower(), sentinel
    assert "://" not in blob and "@" not in blob
    navigated = [e for e in r.observation["auth_trace"]["events"] if e["e"] == "navigated" and e["host"] == "auth"]
    assert navigated[-1]["query"] is True and navigated[-1]["fragment"] is True, navigated  # solo "hay query / hay fragmento"
    assert any(str(s).startswith("{num") or str(s).startswith("{mixed") for s in navigated[-1]["route"]), navigated[-1]
    _clean(run)


def scenario_connection_dropped_during_the_submit_is_a_terminal_failure_with_a_trace() -> None:
    run = Run("m2_auth_welcome_drop", "MENU_SOL", False, auth_nav=3.0)
    r = run.result
    assert r.outcome is not AuthOutcome.AUTH_SUCCEEDED and r.credential_submissions == 1, r
    assert "state:WAITING_FOR_USER" not in run.events and r.diagnostic_code is not None, r
    trace = (r.observation or {}).get("auth_trace")
    assert trace is not None and trace["events"], r.observation
    assert "://" not in repr(r.observation).lower()
    _clean(run)


def scenario_official_report_sections_are_extracted_from_the_real_dom_and_minimized() -> None:
    run = Run("m2_ficha_live_sections", "FICHA_RUC", True)
    r = run.result
    assert r.outcome is AuthOutcome.AUTH_SUCCEEDED and r.ficha_reported, r
    f = run.ficha["fields"]
    assert f["domicilio"]["distrito"] == "DISTRITO FICTICIO" and f["domicilio"]["numero"] == "123" and "km" not in f["domicilio"]
    assert [t["tributo"] for t in f["tributos_afectos"]] == ["IGV - OPER. INT. - CTA. PROPIA", "RENTA - REGIMEN MYPE TRIBUTARIO", "SENCICO"]
    assert f["tributos_afectos"][1]["afecto_desde"] == "2019-05-01"
    assert f["representantes_legales"] == [{"nombre": "PERSONA FICTICIA UNO", "numero_documento": "00000001", "tipo_documento": "DOC. NACIONAL DE IDENTIDAD",
                                            "cargo": "TITULAR-GERENTE", "fecha_desde": "2019-02-25"}]
    assert f["establecimientos_anexos"][0]["codigo"] == "0001"
    blob = repr(run.ficha)
    for discarded in ("1980", "CALLE SECRETA"):
        assert discarded not in blob
    assert "ruc" in f and run.ficha["malformed"] == []
    _clean(run)


def scenario_unrecognised_section_headers_never_guess_columns() -> None:
    run = Run("m2_ficha_live_sections_headers_unknown", "FICHA_RUC", True)
    assert run.result.outcome is AuthOutcome.AUTH_SUCCEEDED and run.result.ficha_reported, run.result
    f = run.ficha["fields"]
    assert "representantes_legales" not in f and "establecimientos_anexos" not in f
    assert set(f.get("secciones_no_reconocidas", [])) >= {"representantes_legales", "establecimientos_anexos"}
    _clean(run)


def scenario_collapsed_sections_present_in_the_dom_are_read_without_any_click() -> None:
    """Secciones PLEGADAS (display:none) pero presentes en el DOM: `innerText` las ve vacias; el agente lee su `textContent` (solo lectura) y NUNCA
    despliega nada (0 clics tras el destino)."""
    run = Run("m2_ficha_live_sections_collapsed", "FICHA_RUC", True)
    r = run.result
    assert r.outcome is AuthOutcome.AUTH_SUCCEEDED and r.ficha_reported and r.post_landing_clicks == 0, r
    f = run.ficha["fields"]
    assert [t["tributo"] for t in f["tributos_afectos"]] == ["IGV - OPER. INT. - CTA. PROPIA", "RENTA - REGIMEN MYPE TRIBUTARIO", "SENCICO"]
    assert f["domicilio"]["distrito"] == "DISTRITO FICTICIO" and f["establecimientos_anexos"][0]["codigo"] == "0001"
    assert f["representantes_legales"][0]["cargo"] == "TITULAR-GERENTE"
    _clean(run)


def scenario_sections_without_content_stay_empty_and_nothing_is_invented() -> None:
    run = Run("m2_ficha_live_sections_absent", "FICHA_RUC", True)
    assert run.result.outcome is AuthOutcome.AUTH_SUCCEEDED and run.result.ficha_reported, run.result
    f = run.ficha["fields"]
    assert "tributos_afectos" not in f and "representantes_legales" not in f and "establecimientos_anexos" not in f and "domicilio" not in f
    assert "dependencia_sunat" not in f
    _clean(run)


def scenario_tribute_rows_without_a_header_row_are_still_read() -> None:
    run = Run("m2_ficha_live_sections_no_header", "FICHA_RUC", True)
    assert run.result.ficha_reported, run.result
    assert [t["tributo"] for t in run.ficha["fields"]["tributos_afectos"]] == ["IGV - OPER. INT. - CTA. PROPIA", "RENTA - REGIMEN MYPE TRIBUTARIO", "SENCICO"]
    _clean(run)


ALL_M2_SCENARIOS = [
    scenario_collapsed_sections_present_in_the_dom_are_read_without_any_click,
    scenario_sections_without_content_stay_empty_and_nothing_is_invented,
    scenario_tribute_rows_without_a_header_row_are_still_read,
    scenario_official_report_sections_are_extracted_from_the_real_dom_and_minimized,
    scenario_unrecognised_section_headers_never_guess_columns,
    scenario_trace_never_records_identifiers_codes_state_fragments_or_urls,
    scenario_auth_trace_explains_why_the_authentication_stalls_without_leaking_anything,
    scenario_connection_dropped_during_the_submit_is_a_terminal_failure_with_a_trace,
    scenario_welcome_window_that_redirects_by_itself_is_observed_without_a_human_state,
    scenario_welcome_window_that_never_advances_is_a_stalled_navigation_not_a_human_wait,
    scenario_a_positively_identified_human_challenge_is_the_only_human_wait,
    scenario_extra_window_next_to_the_auth_page_fails_closed_instead_of_waiting,
    scenario_browser_closed_cancelled_during_the_welcome_window_is_terminal,
    scenario_failure_before_and_after_the_credential_report_the_right_submission_count,
    scenario_contact_validation_interstitial_waits_for_the_human_then_revalidates_and_reaches_the_ficha,
    scenario_two_consecutive_modals_need_two_human_interventions,
    scenario_mandatory_contact_validation_without_a_way_out_is_an_explicit_recoverable_block,
    scenario_informative_modal_is_classified_without_touching_it,
    scenario_losing_the_shell_during_the_human_intervention_fails_closed_instead_of_continuing,
    scenario_chromium_closed_or_cancelled_during_the_contact_intervention_is_terminal,
    scenario_real_ficha_structure_is_extracted_and_the_ruc_comes_from_the_official_header,
    scenario_header_variants_nested_spaced_and_prefixed_activities_are_extracted,
    scenario_duplicated_misplaced_or_incidental_identifiers_fail_closed,
    scenario_ficha_without_a_ruc_cell_or_with_another_ruc_never_reports_a_snapshot,
    scenario_ficha_still_loading_is_awaited_without_clicks_and_a_partial_document_is_not_success,
    scenario_transit_with_menu_title_but_no_authenticated_marker_is_pending_then_login,
    scenario_transit_with_menu_title_that_never_resolves_fails_closed_without_credentials,
    scenario_authenticated_shell_before_credentials_fails_closed,
    scenario_two_simultaneous_pages_and_unexpected_popups_fail_closed_before_credentials,
    scenario_chromium_closed_or_cancelled_or_expired_during_the_transit_is_terminal_and_credential_free,
    scenario_repeated_transits_under_latency_and_cpu_load_are_stable,
    scenario_login_with_incomplete_or_duplicated_controls_never_receives_the_credential_after_a_transit,
    scenario_login_behind_a_landing_host_hop_is_waited_for_and_then_validated,
    scenario_hop_that_never_resolves_fails_closed_before_any_credential,
    scenario_preflight_waits_through_the_hop_and_reports_login_ready,
    scenario_portal_preflight_reads_structure_without_credentials,
    scenario_pre_credential_failures_name_the_stage_and_never_consume_the_credential,
    scenario_shell_and_destination_failures_name_the_stage_and_flags,
    scenario_blocked_notice_and_timeouts_are_distinguished_from_identity_failures,
    scenario_observation_contains_only_counters_categories_and_booleans,
    scenario_notice_overlay_waits_for_the_human_without_any_click_then_continues,
    scenario_notice_overlay_never_cleared_fails_closed_and_closes_the_browser,
    scenario_cancel_while_the_notice_blocks_the_menu_clicks_nothing,
    scenario_informational_popup_window_does_not_break_the_flow,
    scenario_popup_closed_by_the_human_while_another_page_stays_active,
    scenario_browser_closed_unexpectedly_mid_navigation_is_a_terminal_failure,
    scenario_playwright_timeout_during_navigation_is_a_controlled_failure_never_an_exception,
    scenario_blocker_description_is_structural_and_sanitized,
    scenario_caption_probe_writes_only_generic_labels_and_never_values,
    scenario_menu_sol_destination_then_human_control,
    scenario_ficha_ruc_extraction_then_human_control,
    scenario_ficha_ruc_direct_access_without_extraction,
    scenario_missing_shell_marker_means_zero_clicks,
    scenario_lookalike_page_is_not_the_authenticated_shell,
    scenario_shell_with_login_controls_is_not_the_shell,
    scenario_ficha_entry_missing_fails_closed_without_clicks,
    scenario_ficha_frame_identity_failure_never_extracts,
    scenario_wrong_taxpayer_ficha_is_rejected_and_never_handed_over,
    scenario_partial_ficha_omits_missing_sections_and_flags_malformed_values,
    scenario_unknown_destination_and_misplaced_extraction_fail_closed_before_the_browser,
    scenario_human_control_is_not_a_terminal_state_and_cancel_ends_it,
    scenario_human_control_expires_and_browser_is_closed,
    scenario_human_control_report_rejected_closes_the_browser,
    scenario_automation_is_frozen_after_the_destination_is_validated,
]
