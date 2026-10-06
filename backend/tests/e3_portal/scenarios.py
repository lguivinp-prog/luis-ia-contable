"""Escenarios E3 contra Playwright REAL + portal local (SOLO PRUEBAS).

Cada `scenario_*` lanza un Chromium real VISIBLE, contra el portal local
determinista, usa credenciales FICTICIAS y lanza `AssertionError` si algo
falla. Se ejecutan desde pytest (`test_e3_real_playwright.py`, que se salta
si Playwright no esta instalado -- p. ej. en Docker) o directamente desde el
runner de Windows (`tests/windows_native_acceptance/harness_e3_windows_runner.py`).
Solo credenciales/identidades ficticias; sin red hacia SUNAT."""

from __future__ import annotations

import time

from tests.e3_portal.fake_portal import FakeSolPortal
from windows_agent.broker_client import BrokerProtocolError, CredentialMaterial
from windows_agent.browser_executor import SupervisedAuthExecutor, run_authentication
from windows_agent.portal_contract import AuthOutcome, FieldSpec, PortalContract

FAKE_RUC = "20999999990"  # 11 digitos: respeta el maxlength observado
FAKE_USER = "FKUSR_A1"  # <= 8
FAKE_PASSWORD = "FkPw-7c1f#9"  # <= 12
SECRETS = (FAKE_RUC, FAKE_USER, FAKE_PASSWORD)


def test_contract(portal: FakeSolPortal) -> PortalContract:
    """Contrato de PRUEBA: misma estructura/ids/atributos que el de produccion (hechos LIVE-A1) pero
    con los hosts `*.localhost` del portal local y esquema http."""
    return PortalContract(
        required_scheme="http",
        entry_url=portal.url("entry", "/sol.html"),
        entry_hosts=frozenset({"entry.localhost"}),
        auth_hosts=frozenset({"auth.localhost"}),
        auth_path_pattern=r"^/v1/clientessol/[^/]+/oauth2/loginMenuSol/?$",
        auth_title_contains="Menú SOL",
        landing_hosts=frozenset({"menu.localhost"}),
        landing_path_prefix="/cl-ti-itmenu/",
        landing_title_contains="Menú SOL",
        landing_observed_path="/cl-ti-itmenu/MenuInternet.htm",
        landing_observed_title="SUNAT - Menú SOL",
        entry_heading_text="Mis tramites y consultas",
        entry_link_text="Ingresar",
        ruc_field=FieldSpec("txtRuc", "input", "text", "RUC", "11"),
        username_field=FieldSpec("txtUsuario", "input", "text", "Usuario", "8"),
        password_field=FieldSpec("txtContrasena", "input", "password", "Contraseña", "12"),
        submit_button=FieldSpec("btnAceptar", "button", "button", None),
        submit_name="Iniciar sesión",
        dni_field_id="txtDni",
        mode_ruc_button_id="btnPorRuc",
        mode_dni_button_id="btnPorDni",
        mode_active_class="active",
        invalid_credentials_texts=("Credenciales inválidas (fixture)",),
    )


class FakeCredentialProvider:
    """Equivale a `fetch_credential_for_job` ya aplicado: cuenta las entregas
    (la entrega real es de UN solo uso)."""

    def __init__(self, on_enter=None, fail: bool = False) -> None:
        self.fetches = 0
        self.last_material: CredentialMaterial | None = None
        self._on_enter = on_enter
        self._fail = fail

    def __call__(self):
        return self

    def __enter__(self) -> CredentialMaterial:
        self.fetches += 1
        if self._fail:
            raise BrokerProtocolError("simulated delivery failure")
        if self._on_enter:
            self._on_enter()
        self.last_material = CredentialMaterial(ruc=FAKE_RUC, sol_username=FAKE_USER, sol_password=FAKE_PASSWORD)
        return self.last_material

    def __exit__(self, *exc) -> None:
        if self.last_material is not None:
            self.last_material._clear()


def _executor(portal: FakeSolPortal, **kw) -> SupervisedAuthExecutor:
    defaults = dict(navigation_timeout_seconds=20.0, settle_grace_seconds=1.5, challenge_timeout_seconds=30.0,
                    poll_interval_seconds=0.1, pre_credential_attempts=2)
    defaults.update(kw)
    return SupervisedAuthExecutor(test_contract(portal), headless=False, **defaults)


def _assert_no_forbidden(portal: FakeSolPortal) -> None:
    assert portal.forbidden_requests() == [], portal.forbidden_requests()


def scenario_success() -> dict:  # A, L
    portal = FakeSolPortal().start()
    try:
        provider = FakeCredentialProvider()
        seen = {"max_pages": 0, "states": []}
        holder: dict = {}

        def _tick() -> None:
            ctx = holder["ex"]._context
            if ctx is not None:
                seen["max_pages"] = max(seen["max_pages"], len(ctx.pages))

        ex = _executor(portal, tick=_tick, on_state=lambda s: seen["states"].append(s))
        holder["ex"] = ex
        result = run_authentication(ex, provider)
        assert result.outcome is AuthOutcome.AUTH_SUCCEEDED, result
        assert provider.fetches == 1
        assert seen["max_pages"] >= 2  # el login se abrio en una Page NUEVA (patron de dos Pages observado)
        assert seen["states"] == []  # el input #captcha hidden NO es un challenge
        assert ex.mode_activation_clicks == 0 and portal.mode_clicks == []  # modo RUC ya activo: sin clic (Q)
        assert portal.decoy_requests() == []  # solo el enlace estructural; ningun "Ingresar" ajeno
        assert len(portal.submissions) == 1 and result.credential_submissions == 1  # L
        sub = portal.submissions[0]
        assert (sub["mode"], sub["ruc"], sub["user"], sub["pass"]) == ("ruc", FAKE_RUC, FAKE_USER, FAKE_PASSWORD)
        assert result.post_landing_clicks == 0
        time.sleep(0.5)  # ventana de observacion: nada mas debe pedirse tras el landing
        _assert_no_forbidden(portal)
        assert provider.last_material.sol_password is None  # referencias soltadas
        return {"outcome": result.outcome.value, "submissions": len(portal.submissions)}
    finally:
        portal.stop()


def scenario_invalid_credentials() -> dict:  # B
    portal = FakeSolPortal().start()
    try:
        portal.set_scenario("invalid")
        provider = FakeCredentialProvider()
        result = run_authentication(_executor(portal), provider)
        assert result.outcome is AuthOutcome.INVALID_CREDENTIALS, result
        assert result.terminal_reason_code == "AUTH_FAILED"
        assert len(portal.submissions) == 1 and provider.fetches == 1  # sin bucle de reintentos
        time.sleep(0.5)
        assert len(portal.submissions) == 1
        return {"outcome": result.outcome.value}
    finally:
        portal.stop()


def scenario_human_challenge_completed() -> dict:  # C
    portal = FakeSolPortal().start()
    try:
        portal.set_scenario("challenge")
        states: list[AuthOutcome] = []

        def _on_state(state: AuthOutcome) -> None:
            states.append(state)
            portal.release_challenge()  # "el humano" completa el challenge

        result = run_authentication(_executor(portal, on_state=_on_state), FakeCredentialProvider())
        assert AuthOutcome.WAITING_FOR_USER in states
        assert result.outcome is AuthOutcome.AUTH_SUCCEEDED, result
        assert len(portal.submissions) == 1  # el challenge nunca provoco un reenvio
        _assert_no_forbidden(portal)
        return {"states": [s.value for s in states], "outcome": result.outcome.value}
    finally:
        portal.stop()


def scenario_challenge_timeout() -> dict:  # D (timeout)
    portal = FakeSolPortal().start()
    try:
        portal.set_scenario("challenge_never")
        result = run_authentication(_executor(portal, challenge_timeout_seconds=2.0), FakeCredentialProvider())
        assert result.outcome is AuthOutcome.EXPIRED, result
        assert result.terminal_reason_code == "HUMAN_CHALLENGE_TIMEOUT"
        assert len(portal.submissions) == 1
        return {"outcome": result.outcome.value}
    finally:
        portal.stop()


def scenario_challenge_cancel() -> dict:  # D (cancel)
    portal = FakeSolPortal().start()
    try:
        portal.set_scenario("challenge_never")
        flag = {"cancel": False}
        result = run_authentication(
            _executor(portal, is_cancelled=lambda: flag["cancel"], on_state=lambda s: flag.update(cancel=True)),
            FakeCredentialProvider(),
        )
        assert result.outcome is AuthOutcome.CANCELLED, result
        assert len(portal.submissions) == 1
        return {"outcome": result.outcome.value}
    finally:
        portal.stop()


def scenario_changed_selector() -> dict:  # E
    portal = FakeSolPortal().start()
    try:
        portal.set_scenario("changed_selector")
        provider = FakeCredentialProvider()
        result = run_authentication(_executor(portal), provider)
        assert result.outcome is AuthOutcome.PORTAL_CHANGED, result
        assert provider.fetches == 0 and portal.submissions == []  # nunca se entrego ni envio nada
        return {"outcome": result.outcome.value}
    finally:
        portal.stop()


def scenario_unexpected_landing() -> dict:  # F
    portal = FakeSolPortal().start()
    try:
        portal.set_scenario("unexpected_landing")
        result = run_authentication(_executor(portal), FakeCredentialProvider())
        assert result.outcome is AuthOutcome.PORTAL_CHANGED, result
        assert result.post_landing_clicks == 0
        assert portal.count("evil.localhost", "/x") == 1  # solo la redireccion; ninguna accion posterior
        bad = portal.scenario
        portal.set_scenario("bad_landing")  # landing en el host correcto pero con identidad distinta
        result2 = run_authentication(_executor(portal), FakeCredentialProvider())
        assert result2.outcome is AuthOutcome.PORTAL_CHANGED, result2
        _assert_no_forbidden(portal)
        return {"outcome": result.outcome.value, "scenarios": [bad, "bad_landing"]}
    finally:
        portal.stop()


def scenario_frame_login() -> dict:  # G
    portal = FakeSolPortal().start()
    try:
        portal.set_scenario("frame_login")
        provider = FakeCredentialProvider()
        result = run_authentication(_executor(portal), provider)
        assert result.outcome is AuthOutcome.PORTAL_CHANGED and result.terminal_reason_code == "AUTH_HOST_CHANGED", result
        assert provider.fetches == 0 and portal.submissions == []
        return {"outcome": result.outcome.value, "code": result.terminal_reason_code}
    finally:
        portal.stop()


def scenario_navigation_timeout() -> dict:  # H
    portal = FakeSolPortal().start()
    try:
        portal.set_scenario("hang_entry")
        portal.hang_seconds = 4.0
        provider = FakeCredentialProvider()
        result = run_authentication(_executor(portal, navigation_timeout_seconds=1.0), provider)
        assert result.outcome is AuthOutcome.TRANSIENT_NAVIGATION_FAILURE, result
        assert provider.fetches == 0 and portal.submissions == []
        assert portal.count("entry.localhost", "/sol.html") == 2  # reintento acotado previo a la credencial
        return {"outcome": result.outcome.value}
    finally:
        portal.stop()


def scenario_browser_closed_during_wait() -> dict:  # I (el usuario cierra el navegador)
    portal = FakeSolPortal().start()
    try:
        portal.set_scenario("challenge_never")
        holder: dict = {}

        def _on_state(state: AuthOutcome) -> None:
            for page in list(holder["ex"]._context.pages):
                page.close()  # cierre por el usuario

        ex = _executor(portal, on_state=_on_state)
        holder["ex"] = ex
        result = run_authentication(ex, FakeCredentialProvider())
        assert result.outcome is AuthOutcome.BROWSER_FAILURE and result.terminal_reason_code == "BROWSER_CLOSED", result
        assert len(portal.submissions) == 1
        return {"outcome": result.outcome.value}
    finally:
        portal.stop()


def scenario_page_crash_during_wait() -> dict:  # I (crash de pagina)
    portal = FakeSolPortal().start()
    try:
        portal.set_scenario("challenge_never")
        holder: dict = {}

        def _on_state(state: AuthOutcome) -> None:
            try:
                holder["ex"]._context.pages[0].goto("chrome://crash", wait_until="commit", timeout=3000)
            except Exception:  # noqa: BLE001 -- el crash es el efecto buscado
                pass

        ex = _executor(portal, on_state=_on_state, challenge_timeout_seconds=20.0)
        holder["ex"] = ex
        result = run_authentication(ex, FakeCredentialProvider())
        assert result.outcome in {AuthOutcome.BROWSER_FAILURE, AuthOutcome.EXPIRED}, result
        assert result.outcome is AuthOutcome.BROWSER_FAILURE, result
        assert len(portal.submissions) == 1
        return {"outcome": result.outcome.value}
    finally:
        portal.stop()


def scenario_cancel_before_secret_delivery() -> dict:  # J
    portal = FakeSolPortal().start()
    try:
        provider = FakeCredentialProvider()
        # se cancela cuando el login ya fue servido, pero ANTES de pedir la credencial
        result = run_authentication(
            _executor(portal, is_cancelled=lambda: portal.count("auth.localhost", "/v1/clientessol/") >= 1), provider
        )
        assert result.outcome is AuthOutcome.CANCELLED, result
        assert provider.fetches == 0 and portal.submissions == []
        return {"outcome": result.outcome.value}
    finally:
        portal.stop()


def scenario_cancel_after_secret_delivery() -> dict:  # K
    portal = FakeSolPortal().start()
    try:
        flag = {"cancel": False}
        provider = FakeCredentialProvider(on_enter=lambda: flag.update(cancel=True))  # cancelacion durante la entrega
        result = run_authentication(_executor(portal, is_cancelled=lambda: flag["cancel"]), provider)
        assert result.outcome is AuthOutcome.CANCELLED, result
        assert provider.fetches == 1  # nunca se vuelve a entregar
        assert portal.submissions == []  # nada se envio
        assert provider.last_material.sol_password is None  # referencias descartadas
        return {"outcome": result.outcome.value, "fetches": provider.fetches}
    finally:
        portal.stop()


def scenario_credential_delivery_failure_no_retry() -> dict:
    portal = FakeSolPortal().start()
    try:
        provider = FakeCredentialProvider(fail=True)
        result = run_authentication(_executor(portal), provider)
        assert result.outcome is AuthOutcome.BROWSER_FAILURE and result.terminal_reason_code == "CREDENTIAL_UNAVAILABLE"
        assert provider.fetches == 1 and portal.submissions == []
        return {"code": result.terminal_reason_code}
    finally:
        portal.stop()


def _negative_contract(scenario_name: str, *, detail_code: str | None = None, mode_clicks: int = 0) -> dict:
    """Variante del portal que contradice el contrato: fail closed como PORTAL_CHANGED ANTES de pedir la
    credencial (CREDENTIAL_FETCH_BEFORE_PORTAL_IDENTITY_VALIDATION=NO): 0 entregas, 0 envios."""
    portal = FakeSolPortal().start()
    try:
        portal.set_scenario(scenario_name)
        provider = FakeCredentialProvider()
        ex = _executor(portal, controls_wait_seconds=1.0)
        result = run_authentication(ex, provider)
        assert result.outcome is AuthOutcome.PORTAL_CHANGED, (scenario_name, result)
        if detail_code:
            assert result.terminal_reason_code == detail_code, result
        assert provider.fetches == 0, scenario_name  # el endpoint de credencial no se toco
        assert portal.submissions == [], scenario_name
        assert ex.mode_activation_clicks == mode_clicks, (scenario_name, ex.mode_activation_clicks)
        assert portal.decoy_requests() == [], scenario_name
        _assert_no_forbidden(portal)
        return {"scenario": scenario_name, "outcome": result.outcome.value}
    finally:
        portal.stop()


def _neg(name: str, **kw):
    def _fn() -> dict:
        return _negative_contract(name, **kw)

    _fn.__name__ = f"scenario_negative_{name}"
    return _fn


NEGATIVE_SCENARIOS = [
    _neg("no_heading"),  # A
    _neg("heading_changed"),  # B
    _neg("no_entry_link"),  # C
    _neg("no_txtRuc"),  # E
    _neg("dup_txtRuc"),  # F
    _neg("ruc_wrong_type"),  # G
    _neg("ruc_wrong_maxlength"),  # H
    _neg("no_txtUsuario"),  # I
    _neg("pwd_not_password"),  # J
    _neg("no_btnAceptar"),  # K
    _neg("dup_btnAceptar"),  # L
    _neg("bad_login_host", detail_code=None),  # M
    _neg("bad_login_path"),  # N
    _neg("frame_login", detail_code="AUTH_HOST_CHANGED"),  # O
    _neg("mode_activation_fails", mode_clicks=1),  # S (un unico clic, nunca mas)
]


def scenario_ruc_mode_inactive_one_activation_click() -> dict:  # R
    portal = FakeSolPortal().start()
    try:
        portal.set_scenario("mode_inactive")
        provider = FakeCredentialProvider()
        ex = _executor(portal)
        result = run_authentication(ex, provider)
        assert result.outcome is AuthOutcome.AUTH_SUCCEEDED, result
        assert ex.mode_activation_clicks == 1 and portal.mode_clicks == ["ruc"]  # exactamente UN clic, en RUC
        assert provider.fetches == 1 and len(portal.submissions) == 1
        return {"outcome": result.outcome.value, "mode_clicks": portal.mode_clicks}
    finally:
        portal.stop()


def scenario_hidden_captcha_only_is_not_a_challenge() -> dict:  # P
    portal = FakeSolPortal().start()  # el fixture siempre incluye `input#captcha` hidden
    try:
        states: list = []
        result = run_authentication(_executor(portal, on_state=states.append), FakeCredentialProvider())
        assert result.outcome is AuthOutcome.AUTH_SUCCEEDED and states == [], (result, states)
        return {"states": states}
    finally:
        portal.stop()


def _post_auth_negative(scenario_name: str) -> dict:
    """Tras UN envio legitimo, un landing que NO cumple la identidad completa (ruta/titulo/formulario de login) NO
    es exito: fail closed como PORTAL_CHANGED, sin clics posteriores y sin segundo envio."""
    portal = FakeSolPortal().start()
    try:
        portal.set_scenario(scenario_name)
        provider = FakeCredentialProvider()
        result = run_authentication(_executor(portal), provider)
        assert result.outcome is AuthOutcome.PORTAL_CHANGED, (scenario_name, result)
        assert provider.fetches == 1 and len(portal.submissions) == 1 and result.credential_submissions == 1
        assert result.post_landing_clicks == 0
        time.sleep(0.4)
        assert len(portal.submissions) == 1
        _assert_no_forbidden(portal)
        return {"scenario": scenario_name, "outcome": result.outcome.value}
    finally:
        portal.stop()


def _post_neg(name: str):
    def _fn() -> dict:
        return _post_auth_negative(name)

    _fn.__name__ = f"scenario_post_auth_negative_{name}"
    return _fn


POST_AUTH_NEGATIVE_SCENARIOS = [_post_neg("bad_landing"), _post_neg("bad_landing_path"), _post_neg("landing_with_login_form")]


def scenario_landing_with_irrelevant_child_frames_is_still_the_landing() -> dict:
    """El landing sintetico incluye frames hijos sin hostname normal (about:blank, srcdoc): ni lo impiden ni lo crean."""
    portal = FakeSolPortal().start()
    try:
        ex = _executor(portal)
        result = run_authentication(ex, FakeCredentialProvider())
        assert result.outcome is AuthOutcome.AUTH_SUCCEEDED, result
        assert portal.count("menu.localhost", "/cl-ti-itmenu/MenuInternet.htm") >= 1  # aterrizo en la ruta observada
        assert portal.count("menu.localhost", "/cl-ti-itmenu/AutenticaMenuInternet.htm") == 1  # salto intermedio
        return {"outcome": result.outcome.value}
    finally:
        portal.stop()


ALL_SCENARIOS = [
    scenario_success,
    scenario_invalid_credentials,
    scenario_human_challenge_completed,
    scenario_challenge_timeout,
    scenario_challenge_cancel,
    scenario_changed_selector,
    scenario_unexpected_landing,
    scenario_frame_login,
    scenario_navigation_timeout,
    scenario_browser_closed_during_wait,
    scenario_page_crash_during_wait,
    scenario_cancel_before_secret_delivery,
    scenario_cancel_after_secret_delivery,
    scenario_credential_delivery_failure_no_retry,
    scenario_ruc_mode_inactive_one_activation_click,
    scenario_hidden_captcha_only_is_not_a_challenge,
    scenario_landing_with_irrelevant_child_frames_is_still_the_landing,
    *NEGATIVE_SCENARIOS,
    *POST_AUTH_NEGATIVE_SCENARIOS,
]
