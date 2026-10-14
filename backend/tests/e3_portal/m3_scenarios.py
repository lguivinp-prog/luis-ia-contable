"""Escenarios M3 contra Playwright REAL + portal local con destinos SINTETICOS (SOLO PRUEBAS; sin red hacia SUNAT).

IMPORTANTE: estos escenarios validan el MOTOR de contratos (secuencia de menu, presupuesto derivado, identidad positiva y negativa, nueva Page /
frame, avisos, fail-closed). NO validan ningun destino real de SUNAT: SIRE, Declaraciones y Pagos, Declara Facil y Tramites y Consultas siguen
`NOT_VALIDATED` y el agente se niega a abrirlos. Un fixture jamas cuenta como validacion real."""

from __future__ import annotations

from contextlib import contextmanager

from tests.e3_portal import m3_fixtures
from tests.e3_portal.m2_scenarios import Run, _clean, _contact_untouched
from windows_agent import destination_contract as dc
from windows_agent.portal_contract import AuthOutcome

WAIT = 3.0  # espera acotada de las llegadas negativas (que fallan por plazo)


@contextmanager
def synthetic_contracts():
    added = m3_fixtures.synthetic_contracts()
    dc.CONTRACTS.update(added)
    try:
        yield
    finally:
        for key in added:
            dc.CONTRACTS.pop(key, None)


def _reached(run: Run, clicks: int) -> None:
    r = run.result
    assert r.outcome is AuthOutcome.AUTH_SUCCEEDED and r.destination_reached and r.shell_reached, r
    assert r.destination_clicks == clicks, (r.destination_clicks, clicks)  # presupuesto derivado de la secuencia, ni uno mas
    assert r.post_landing_clicks == 0 and run.events == ["human_control", "human_closes_browser"], (r, run.events)
    assert r.credential_submissions == 1 and run.provider.fetches == 1
    _clean(run)


def scenario_menu_leaf_destination_in_a_frame_is_reached_with_a_derived_click_budget() -> None:
    with synthetic_contracts():
        _reached(Run("m3_frame", "SYN_FRAME", False), clicks=3)  # 2 cabeceras + la hoja


def scenario_menu_leaf_destination_in_a_new_page_is_reached_and_handed_over() -> None:
    with synthetic_contracts():
        _reached(Run("m3_page", "SYN_PAGE", False), clicks=4)  # 3 cabeceras + la hoja


def scenario_home_link_destination_in_a_new_page_is_reached_with_a_single_click() -> None:
    with synthetic_contracts():
        _reached(Run("m3_home", "SYN_HOME", False), clicks=1)


NEGATIVE = (
    ("m3_frame_absent", "SYN_FRAME", "DESTINATION_ENTRY_NOT_FOUND"),  # ausente del menu de esta cuenta (sin permiso o menu cambiado)
    ("m3_frame_dup", "SYN_FRAME", "DESTINATION_MENU_STRUCTURE_MISMATCH"),  # etiqueta ambigua
    ("m3_frame_depth", "SYN_FRAME", "DESTINATION_MENU_STRUCTURE_MISMATCH"),  # misma etiqueta a otra profundidad
    ("m3_frame_wrongpath", "SYN_FRAME", "DESTINATION_FRAME_NOT_VALIDATED"),
    ("m3_frame_nomarker", "SYN_FRAME", "DESTINATION_FRAME_NOT_VALIDATED"),
    ("m3_frame_fewtables", "SYN_FRAME", "DESTINATION_FRAME_NOT_VALIDATED"),
    ("m3_frame_crumb", "SYN_FRAME", "DESTINATION_IDENTITY_MISMATCH"),  # la miga no identifica el destino
    ("m3_page_extra", "SYN_PAGE", "DESTINATION_PAGE_NOT_VALIDATED"),  # popup inesperado junto a la Page esperada
    ("m3_page_wrongpath", "SYN_PAGE", "DESTINATION_PAGE_NOT_VALIDATED"),
    ("m3_page_nomarker", "SYN_PAGE", "DESTINATION_PAGE_NOT_VALIDATED"),
    ("m3_page_never", "SYN_PAGE", "DESTINATION_PAGE_NOT_VALIDATED"),  # la nueva Page nunca llega
    ("m3_home_absent", "SYN_HOME", "DESTINATION_ENTRY_NOT_FOUND"),
)


def scenario_wrong_menu_wrong_identity_and_unexpected_pages_fail_closed_with_a_specific_diagnostic() -> None:
    with synthetic_contracts():
        for scenario, destination, expected in NEGATIVE:
            run = Run(scenario, destination, False, destination_wait=WAIT)
            r = run.result
            assert r.diagnostic_code == expected, (scenario, r.diagnostic_code, r.detail)
            assert not r.destination_reached and "human_control" not in run.events and r.post_landing_clicks == 0, (scenario, r, run.events)
            assert r.shell_reached  # el shell SI se valido: el fallo es del destino, no de la autenticacion
            assert r.destination_clicks <= dc.CONTRACTS[destination].max_clicks, scenario  # nunca mas clics que el presupuesto derivado
            _clean(run)


def scenario_unvalidated_and_unknown_destinations_are_refused_before_any_browser_or_credential() -> None:
    # RVIE, RCE y Declaraciones y Pagos pasaron la aceptacion real (el agente valida la ESTRUCTURA; el backend decide por perfil): ya no figuran aqui.
    for destination in ("SIRE", "SIRE_GENERACION_REGISTROS", "DECLARA_FACIL"):  # TRAMITES_Y_CONSULTAS es alias de MENU_SOL
        run = Run("m2_success", destination, False)
        r = run.result
        assert r.diagnostic_code is None or r.diagnostic_code == "DESTINATION_NOT_VALIDATED"
        assert r.detail == "destination not validated" and not r.destination_reached and r.credential_submissions == 0, (destination, r)
        assert run.provider.fetches == 0 and run.executor._browser is None and run.events == []  # ni navegador ni credencial ni control humano
        assert run.portal.requests == [], run.portal.requests  # el portal local ni siquiera fue consultado
    for unknown in ("BUZON_SUNAT", "https://e-menu.sunat.gob.pe/x", "sire", ""):
        run = Run("m2_success", unknown, False)
        assert run.result.detail == "destination not allowed" and run.provider.fetches == 0 and run.executor._browser is None, unknown
    run = Run("m2_success", "SIRE", True)  # extraer datos solo existe para FICHA_RUC (y SIRE ni siquiera esta validado)
    assert run.result.detail == "destination not validated" and run.provider.fetches == 0


def scenario_false_shell_means_zero_clicks_for_contract_destinations() -> None:
    with synthetic_contracts():
        for scenario in ("m3_frame__m2_no_marker", "m3_frame__m2_lookalike", "m3_page__m2_login_like_shell"):
            run = Run(scenario, "SYN_FRAME" if "frame" in scenario else "SYN_PAGE", False, destination_wait=WAIT)
            r = run.result
            assert r.destination_clicks == 0 and r.post_landing_clicks == 0 and not r.destination_reached and not r.shell_reached, (scenario, r)
            assert "human_control" not in run.events, scenario
            _clean(run)


def scenario_contact_notice_blocks_the_first_click_the_human_resolves_it_and_the_destination_is_reached() -> None:
    with synthetic_contracts():
        run = Run("m3_frame__m2_contact_interstitial", "SYN_FRAME", False, dismiss_notice=True, human_wait=30.0)
        r = run.result
        assert r.outcome is AuthOutcome.AUTH_SUCCEEDED and r.destination_reached and r.destination_clicks == 3, (r, run.events)
        assert "state:WAITING_FOR_USER" in run.events and run.executor.waiting_reason == "CONTACT_VALIDATION_INTERSTITIAL"
        assert run.events.index("human_dismisses_notice") < run.events.index("human_control")
        _contact_untouched(run)  # cero clics sobre Finalizar / enviar codigo / confirmar contacto


def scenario_mandatory_notice_is_a_recoverable_block_and_cancellation_or_closing_end_the_session_cleanly() -> None:
    with synthetic_contracts():
        blocked = Run("m3_frame__m2_contact_mandatory", "SYN_FRAME", False, human_wait=4.0)
        assert blocked.result.diagnostic_code == "BLOCKED_BY_INTERMEDIATE_SCREEN" and blocked.result.destination_clicks == 0, blocked.result
        _contact_untouched(blocked)
        cancelled = Run("m3_frame__m2_contact_mandatory", "SYN_FRAME", False, cancel_while_waiting=True, human_wait=20.0)
        assert cancelled.result.outcome is AuthOutcome.CANCELLED and cancelled.result.destination_clicks == 0
        _contact_untouched(cancelled)

        def close_browser(run: Run, ex) -> None:
            if "state:WAITING_FOR_USER" in run.events and "closed" not in run.events and ex._browser is not None:
                run.events.append("closed")
                ex._browser.close()

        closed = Run("m3_frame__m2_contact_mandatory", "SYN_FRAME", False, on_tick=close_browser, human_wait=20.0)
        assert closed.result.outcome is AuthOutcome.BROWSER_FAILURE and closed.result.destination_clicks == 0
        _contact_untouched(closed)


def scenario_human_control_for_contract_destinations_can_be_cancelled_expires_and_never_gets_new_clicks() -> None:
    with synthetic_contracts():
        cancelled = Run("m3_page", "SYN_PAGE", False, close_after_hc=False, cancel_after_hc=True)
        assert cancelled.result.outcome is AuthOutcome.CANCELLED and cancelled.executor._browser is None
        assert cancelled.result.post_landing_clicks == 0
        _clean(cancelled)
        expired = Run("m3_frame", "SYN_FRAME", False, close_after_hc=False, hc_timeout=1.5)
        assert expired.result.outcome is AuthOutcome.AUTH_SUCCEEDED and expired.executor._browser is None  # sin sesiones inmortales
        assert expired.result.post_landing_clicks == 0
        _clean(expired)


def scenario_navigation_never_requests_consequential_routes_or_decoys() -> None:
    """Abrir un destino NO presenta, paga, declara ni abre el Buzon: ninguna ruta consecuente, ni el SIRE/Declaraciones del inicio, ni senuelos."""
    with synthetic_contracts():
        for scenario, destination in (("m3_frame", "SYN_FRAME"), ("m3_page", "SYN_PAGE"), ("m3_home", "SYN_HOME")):
            run = Run(scenario, destination, False)
            assert run.forbidden == [], (scenario, run.forbidden)
            assert run.portal.decoy_requests() == [], scenario


@contextmanager
def real_sire_contracts():
    """Contratos REALES de SIRE con host local, validados SOLO durante la prueba del motor (se restauran SIEMPRE)."""
    saved = {k: dc.CONTRACTS[k] for k in ("SIRE_RVIE", "SIRE_RCE", "SIRE_GENERACION_REGISTROS", "DECLARACIONES_Y_PAGOS")}
    dc.CONTRACTS.update(m3_fixtures.local_real_contracts())
    try:
        yield
    finally:
        dc.CONTRACTS.update(saved)
        assert all(dc.CONTRACTS[k] is saved[k] for k in saved)  # los contratos reales (hosts https) se restauran SIEMPRE
        assert not dc.CONTRACTS["SIRE_GENERACION_REGISTROS"].enabled  # Generacion jamas queda habilitada fuera de la prueba


def scenario_real_sire_contracts_navigate_the_real_menu_shape_without_href_or_onclick_and_hand_over() -> None:
    """RVIE (con tilde), RCE y Generacion (sin tilde): hoja `nivel4` a 3 niveles de cabecera sin href ni onclick, texto exacto, llegada en frame por ruta + ids
    (Generacion: sin ids, 2 tablas). Presupuesto = 4 clics. Con la cabecera homonima de Generacion presente se elige la HOJA."""
    with real_sire_contracts():
        for scenario, destination in (("m3_sire_rvie", "SIRE_RVIE"), ("m3_sire_rce", "SIRE_RCE"), ("m3_sire_gen", "SIRE_GENERACION_REGISTROS"),
                                      ("m3_sire_gen_dupheader", "SIRE_GENERACION_REGISTROS")):
            run = Run(scenario, destination, False)
            _reached(run, clicks=4)
            assert run.portal.decoy_requests() == [], scenario
            assert dc.CONTRACTS[destination].open_only == (destination == "SIRE_GENERACION_REGISTROS")
        # Declaraciones y Pagos (tarjeta publica -> login -> plataforma): contrato REAL con host local; 0 clics de navegacion, credencial una sola vez
        dyp = Run("m3_public", "DECLARACIONES_Y_PAGOS", False)
        r = dyp.result
        assert r.outcome is AuthOutcome.AUTH_SUCCEEDED and r.destination_reached and r.destination_clicks == 0 and r.post_landing_clicks == 0, r
        assert dyp.events == ["human_control", "human_closes_browser"] and dyp.provider.fetches == 1 and dyp.portal.decoy_requests() == []


def scenario_real_sire_contracts_fail_closed_on_changed_text_missing_markers_wrong_path_or_too_few_tables() -> None:
    with real_sire_contracts():
        cases = (
            ("m3_sire_rce_accent", "SIRE_RCE", "DESTINATION_ENTRY_NOT_FOUND"),  # el texto cambio de forma (con tilde): la hoja exacta ya no existe
            ("m3_sire_rvie_nomarker", "SIRE_RVIE", "DESTINATION_FRAME_NOT_VALIDATED"),
            ("m3_sire_rvie_wrongpath", "SIRE_RVIE", "DESTINATION_FRAME_NOT_VALIDATED"),  # abre la app de COMPRAS: no se acepta como RVIE
            ("m3_sire_gen_fewtables", "SIRE_GENERACION_REGISTROS", "DESTINATION_FRAME_NOT_VALIDATED"),
        )
        for scenario, destination, expected in cases:
            run = Run(scenario, destination, False, destination_wait=WAIT)
            r = run.result
            assert r.diagnostic_code == expected, (scenario, r.diagnostic_code, r.detail)
            assert not r.destination_reached and "human_control" not in run.events and r.post_landing_clicks == 0 and r.shell_reached, (scenario, r, run.events)
            assert r.destination_clicks <= 4
            assert run.portal.decoy_requests() == [] and run.forbidden == []


@contextmanager
def acceptance_contracts():
    keys = ("SIRE_RVIE", "SIRE_RCE", "SIRE_GENERACION_REGISTROS", "DECLARACIONES_Y_PAGOS")
    saved = {k: dc.CONTRACTS[k] for k in keys}
    dc.CONTRACTS.update(m3_fixtures.local_acceptance_contracts())
    try:
        yield
    finally:
        dc.CONTRACTS.update(saved)
        assert all(dc.CONTRACTS[k] is saved[k] for k in keys)  # restaurados


def scenario_acceptance_mode_runs_a_recon_complete_contract_once_without_enabling_it_and_refuses_open_only_or_unflagged() -> None:
    """Modo de ACEPTACION REAL (disponible para futuros destinos/perfiles): el agente ejecuta un contrato RECON_COMPLETE aun NO validado solo cuando el
    backend lo marca (autorizacion del propietario) y solo para esta sesion; sin la marca, o para Generacion (solo apertura), se niega ANTES de abrir el
    navegador o pedir la credencial. Se prueba con copias locales de los contratos reales en su estado PREVIO a la aceptacion (NOT_VALIDATED)."""
    with acceptance_contracts():
        for scenario, destination, clicks in (("m3_sire_rvie", "SIRE_RVIE", 4), ("m3_sire_rce", "SIRE_RCE", 4)):
            run = Run(scenario, destination, False, acceptance=True)
            _reached(run, clicks=clicks)
            assert run.portal.decoy_requests() == [], scenario
            assert not dc.CONTRACTS[destination].enabled  # el catalogo NO cambia: ni siquiera durante la prueba
        run = Run("m3_public", "DECLARACIONES_Y_PAGOS", False, acceptance=True)
        r = run.result
        assert r.outcome is AuthOutcome.AUTH_SUCCEEDED and r.destination_reached and r.destination_clicks == 0 and r.post_landing_clicks == 0, r
        assert run.events == ["human_control", "human_closes_browser"] and r.credential_submissions == 1
        for scenario, destination, acceptance in (("m3_sire_rvie", "SIRE_RVIE", False), ("m3_sire_gen", "SIRE_GENERACION_REGISTROS", True),
                                                  ("m3_public", "DECLARACIONES_Y_PAGOS", False)):
            run = Run(scenario, destination, False, acceptance=acceptance)
            r = run.result
            assert r.diagnostic_code == "DESTINATION_NOT_VALIDATED" and not r.destination_reached, (scenario, r.diagnostic_code)
            assert r.credential_submissions == 0 and run.provider.fetches == 0 and run.events == [] and run.portal.requests == [], (scenario, run.events)


def scenario_public_card_entry_authenticates_into_the_platform_and_hands_over_with_zero_navigation_clicks() -> None:
    """Tarjeta publica «Mis declaraciones y pagos» -> login -> plataforma `/cl-ti-itmenu2/`. El contrato fija la tarjeta y el landing ANTES de abrir el
    navegador; el destino se valida por ids; no hay clics de navegacion; las demas tarjetas (decoy) jamas se solicitan."""
    with synthetic_contracts():
        for scenario in ("m3_public", "m3_public_nested"):  # la segunda NO tiene el enlace en el hermano siguiente del encabezado
            run = Run(scenario, "SYN_PUBLIC", False)
            r = run.result
            assert r.outcome is AuthOutcome.AUTH_SUCCEEDED and r.destination_reached and r.shell_reached, (scenario, r)
            assert r.destination_clicks == 0 and r.post_landing_clicks == 0, (scenario, r)
            assert run.events == ["human_control", "human_closes_browser"], (scenario, run.events)
            assert r.credential_submissions == 1 and run.provider.fetches == 1
            contract = run.executor._contract
            assert contract.entry_heading_text == "mis declaraciones y pagos" and contract.entry_link_strategy == "card_container" and contract.landing_path_prefix == "/cl-ti-itmenu2/"
            assert run.portal.decoy_requests() == [], (scenario, run.portal.decoy_requests())  # se pulso LA tarjeta, no otra
            assert any(r[0].startswith("menu") and r[2].startswith("/cl-ti-itmenu2/MenuInternetPlataforma.htm") for r in run.portal.requests), scenario
            _clean(run)


def scenario_public_card_entry_fails_closed_before_or_after_the_credential_with_a_specific_diagnostic() -> None:
    with synthetic_contracts():
        before = (("m3_public_absent", "ENTRY_LINK_NOT_FOUND"), ("m3_public_dup", "ENTRY_LINK_NOT_FOUND"), ("m3_public_twolinks", "ENTRY_LINK_NOT_FOUND"))
        for scenario, expected in before:  # NUNCA llega a pedir ni entregar la credencial
            run = Run(scenario, "SYN_PUBLIC", False)
            r = run.result
            assert r.diagnostic_code == expected and r.credential_submissions == 0 and run.provider.fetches == 0 and not r.destination_reached, (scenario, r)
            assert run.portal.decoy_requests() == [] and "human_control" not in run.events, scenario
            _clean(run)
        after = (("m3_public_wronglanding", "LANDING_IDENTITY_MISMATCH"),  # SUNAT llevo al Menu SOL, no a la plataforma
                 ("m3_public_nomarker", "DESTINATION_PAGE_NOT_VALIDATED"), ("m3_public_dupmarker", "DESTINATION_PAGE_NOT_VALIDATED"),
                 ("m3_public_extra", "DESTINATION_PAGE_NOT_VALIDATED"))  # una segunda ventana inesperada
        for scenario, expected in after:
            run = Run(scenario, "SYN_PUBLIC", False, destination_wait=WAIT)
            r = run.result
            assert r.diagnostic_code == expected, (scenario, r.diagnostic_code, r.detail)
            assert not r.destination_reached and "human_control" not in run.events and r.destination_clicks == 0 and r.post_landing_clicks == 0, (scenario, r, run.events)
            assert run.portal.decoy_requests() == [], scenario
            _clean(run)


def _post_submit_clean(run: Run) -> None:
    assert run.portal.decoy_requests() == [] and run.forbidden == []
    assert run.provider.fetches == 1 and run.result.credential_submissions == 1  # la credencial se entrega UNA sola vez (jamas se reenvia)


def scenario_public_card_legitimate_welcome_transitions_are_not_rejected_prematurely() -> None:
    """Tarjeta publica -> login -> «Bienvenidos a SUNAT» (sin controles) que SIGUE progresando por si sola (800 ms, cadena por el salto del host de
    aterrizaje, 4 s lenta) -> plataforma validada. Ninguna transicion legitima se rechaza; 0 clics de navegacion; una sola entrega de credencial."""
    with synthetic_contracts():
        for scenario in ("m3_public__m2_auth_welcome_redirect", "m3_public__m2_auth_welcome_chain", "m3_public__m2_auth_welcome_slow"):
            run = Run(scenario, "SYN_PUBLIC", False, auth_nav=12.0)
            r = run.result
            assert r.outcome is AuthOutcome.AUTH_SUCCEEDED and r.destination_reached and r.shell_reached, (scenario, r)
            assert r.destination_clicks == 0 and r.post_landing_clicks == 0 and run.events[-2:] == ["human_control", "human_closes_browser"], (scenario, run.events)
            _post_submit_clean(run)


def scenario_public_card_stalled_welcome_fails_closed_with_a_precise_cause_and_without_blaming_the_public_page() -> None:
    """La ventana de autenticacion queda estable y sin continuacion (la incidencia real). Debe fallar cerrada (nunca se entrega el navegador ni se reenvia
    la credencial) y la causa deducida se limita a lo ocurrido DESPUES del envio: un fetch fallido y un error de script de la pagina PUBLICA previa no
    pueden figurar como causa (en la incidencia real se atribuyo a `RESOURCE_LOAD_FAILED` por dos fetch de la pagina de entrada)."""
    with synthetic_contracts():
        for scenario in ("m3_public__m2_auth_welcome_stuck", "m3_public_entryfail__m2_auth_welcome_stuck"):
            run = Run(scenario, "SYN_PUBLIC", False, auth_nav=2.5)
            r = run.result
            assert r.diagnostic_code == "AUTH_NAVIGATION_STALLED" and not r.destination_reached and "human_control" not in run.events, (scenario, r)
            obs = r.observation or {}
            assert obs.get("stall_reason") == "STABLE_DOCUMENT_NO_CONTINUATION", (scenario, obs.get("stall_reason"), obs.get("auth_trace", {}).get("counters"))
            doc = obs["auth_document"]
            assert doc["title_key"] == "welcome" and doc["ready_state"] == "complete" and doc["query_keys"] == [] and "route" in doc, doc
            assert obs["submit_state"]["ready_state"] in {"complete", "interactive", "loading", "unavailable"} and obs["auth_trace"]["submitted"] is True
            text = repr(obs).lower()
            for forbidden in ("://", "localhost", "fixture", "bienvenidos", "cookie", "authorization", "bearer"):
                assert forbidden not in text, (scenario, forbidden)
            _post_submit_clean(run)
        # una causa REAL posterior al envio sigue distinguiendose (JS inalcanzable)
        run = Run("m3_public_entryfail__m2_auth_welcome_script_unreachable", "SYN_PUBLIC", False, auth_nav=2.5)
        assert (run.result.observation or {}).get("stall_reason") == "RESOURCE_LOAD_FAILED"


def scenario_public_card_unexpected_popup_host_or_closed_browser_never_reach_the_platform() -> None:
    with synthetic_contracts():
        popup = Run("m3_public__m2_auth_welcome_popup", "SYN_PUBLIC", False, auth_nav=3.0, destination_wait=3.0)
        assert not popup.result.destination_reached and "human_control" not in popup.events, popup.result  # una ventana adicional es ambigua: falla cerrado
        _post_submit_clean(popup)
        evil = Run("m3_public__unexpected_landing", "SYN_PUBLIC", False, auth_nav=3.0)
        r = evil.result
        assert not r.destination_reached and "human_control" not in evil.events and r.diagnostic_code is not None, r
        assert not [q for q in evil.portal.requests if q[0].startswith("evil") and q[1] == "POST"]  # al host inesperado jamas llega una credencial
        _post_submit_clean(evil)

        def close_browser(run, ex):
            if ex._browser is not None and ex._submissions:
                ex._browser.close()  # el usuario cierra Chromium mientras «Bienvenidos a SUNAT» no avanza

        closed = Run("m3_public__m2_auth_welcome_stuck", "SYN_PUBLIC", False, on_tick=close_browser, auth_nav=30.0)
        assert closed.result.outcome is AuthOutcome.BROWSER_FAILURE and closed.executor._browser is None and "human_control" not in closed.events, closed.result
        _post_submit_clean(closed)


def scenario_submission_waits_for_the_login_document_to_finish_loading_without_blocking_or_resubmitting() -> None:
    """Compuerta de carga: la credencial no se escribe antes de `document.readyState == 'complete'` (acotada). Un login con recurso lento se espera; uno cuyo
    `load` nunca llega no bloquea la sesion (se envia al vencer la espera, UNA sola vez)."""
    from windows_agent.browser_executor import SupervisedAuthExecutor

    # la compuerta es una funcion pura sobre una pagina falsa: estados y limites
    class _Page:
        def __init__(self, states):
            self.states, self.calls = list(states), 0

        def evaluate(self, _script):
            self.calls += 1
            return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    ticks = {"t": 0.0}
    ex = SupervisedAuthExecutor.__new__(SupervisedAuthExecutor)
    ex._clock = lambda: ticks["t"]
    ex._poll, ex._ready_wait = 0.1, 1.0
    ex._pump = lambda seconds: ticks.__setitem__("t", ticks["t"] + seconds)
    ready = ex._wait_login_document_ready(_Page(["loading", "interactive", "complete"]))
    assert ready["ready_state"] == "complete" and ready["waited_ms"] <= 300
    ticks["t"] = 0.0
    never = ex._wait_login_document_ready(_Page(["loading"]))
    assert never["ready_state"] == "loading" and 900 <= never["waited_ms"] <= 1300  # vence la espera acotada y continua (no bloquea)
    assert ex._wait_login_document_ready(type("P", (), {"evaluate": staticmethod(lambda s: (_ for _ in ()).throw(RuntimeError("nav")))})())["ready_state"] == "unavailable"


def scenario_public_entry_is_configured_only_before_the_browser_starts_and_never_by_the_portal() -> None:
    from tests.e3_portal.fake_portal import FakeSolPortal
    from tests.e3_portal.scenarios import test_contract
    from windows_agent.browser_executor import SupervisedAuthExecutor

    portal = FakeSolPortal().start()
    try:
        ex = SupervisedAuthExecutor(test_contract(portal))
        ex.configure_public_entry(heading_text="Mis declaraciones y pagos", landing_path_prefix="/cl-ti-itmenu2/")
        assert ex._contract.entry_link_strategy == "card_container" and ex._contract.entry_hosts == test_contract(portal).entry_hosts  # los hosts NO cambian
        for bad in (("", "/x/"), ("x", "x/"), ("x", "/x")):
            try:
                ex.configure_public_entry(heading_text=bad[0], landing_path_prefix=bad[1])
                raise AssertionError(bad)
            except ValueError:
                pass
        ex._pw = object()  # navegador ya iniciado
        try:
            ex.configure_public_entry(heading_text="otra", landing_path_prefix="/otra/")
            raise AssertionError("reconfiguracion en curso")
        except RuntimeError:
            pass
        assert test_contract(portal).entry_link_strategy == "sibling"  # Tramites y Consultas conserva su estrategia
    finally:
        portal.stop()


ALL_M3_SCENARIOS = [
    scenario_public_card_legitimate_welcome_transitions_are_not_rejected_prematurely,
    scenario_public_card_stalled_welcome_fails_closed_with_a_precise_cause_and_without_blaming_the_public_page,
    scenario_public_card_unexpected_popup_host_or_closed_browser_never_reach_the_platform,
    scenario_submission_waits_for_the_login_document_to_finish_loading_without_blocking_or_resubmitting,
    scenario_acceptance_mode_runs_a_recon_complete_contract_once_without_enabling_it_and_refuses_open_only_or_unflagged,
    scenario_real_sire_contracts_navigate_the_real_menu_shape_without_href_or_onclick_and_hand_over,
    scenario_real_sire_contracts_fail_closed_on_changed_text_missing_markers_wrong_path_or_too_few_tables,
    scenario_public_card_entry_authenticates_into_the_platform_and_hands_over_with_zero_navigation_clicks,
    scenario_public_card_entry_fails_closed_before_or_after_the_credential_with_a_specific_diagnostic,
    scenario_public_entry_is_configured_only_before_the_browser_starts_and_never_by_the_portal,
    scenario_menu_leaf_destination_in_a_frame_is_reached_with_a_derived_click_budget,
    scenario_menu_leaf_destination_in_a_new_page_is_reached_and_handed_over,
    scenario_home_link_destination_in_a_new_page_is_reached_with_a_single_click,
    scenario_wrong_menu_wrong_identity_and_unexpected_pages_fail_closed_with_a_specific_diagnostic,
    scenario_unvalidated_and_unknown_destinations_are_refused_before_any_browser_or_credential,
    scenario_false_shell_means_zero_clicks_for_contract_destinations,
    scenario_contact_notice_blocks_the_first_click_the_human_resolves_it_and_the_destination_is_reached,
    scenario_mandatory_notice_is_a_recoverable_block_and_cancellation_or_closing_end_the_session_cleanly,
    scenario_human_control_for_contract_destinations_can_be_cancelled_expires_and_never_gets_new_clicks,
    scenario_navigation_never_requests_consequential_routes_or_decoys,
]
