"""E3: identidad de Page/Frame (fuente de verdad) -- clasificacion pura, sin
navegador. Cubre exito, login, fallo cerrado ante hosts/estructuras
desconocidas y ausencia de dependencia de la red."""

from __future__ import annotations

import re

import pytest

from windows_agent.portal_contract import SOL_PORTAL_CONTRACT as C
from windows_agent.portal_contract import AuthOutcome, FieldSpec
from windows_agent.portal_identity import PageView, SurfaceKind, classify_surface, is_accepted_landing

ENTRY = PageView(url="https://www.sunat.gob.pe/sol.html", title="SUNAT")
LOGIN = PageView(url="https://api-seguridad.sunat.gob.pe/v1/clientessol/x/oauth2/loginMenuSol",
                 title="SUNAT - Menú SOL", password_field_visible=True)
LANDING = PageView(url="https://e-menu.sunat.gob.pe/cl-ti-itmenu/AutenticaMenuInternet.htm", title="SUNAT - Menú SOL")


def test_contract_freezes_the_live_a1_observations():
    assert C.auth_hosts == frozenset({"api-seguridad.sunat.gob.pe"})
    assert C.required_scheme == "https"
    assert C.auth_title_contains == "Menú SOL"
    assert C.ruc_field == FieldSpec("txtRuc", "input", "text", "RUC", "11")
    assert C.username_field == FieldSpec("txtUsuario", "input", "text", "Usuario", "8")
    assert C.password_field == FieldSpec("txtContrasena", "input", "password", "Contraseña", "12")
    assert C.submit_button.element_id == "btnAceptar" and C.submit_name == "Iniciar sesión"
    assert (C.mode_ruc_button_id, C.mode_dni_button_id, C.dni_field_id) == ("btnPorRuc", "btnPorDni", "txtDni")
    assert C.entry_heading_text == "Mis tramites y consultas"  # texto DOM; el mayusculas es CSS
    # el client-id es DINAMICO: ningun GUID/identificador concreto en el contrato
    import dataclasses
    assert "clientessol/[^/]+/oauth2/loginMenuSol" in C.auth_path_pattern
    assert not any(re.search(r"[0-9a-f]{8}-[0-9a-f]{4}", str(v)) for v in dataclasses.asdict(C).values())
    # falsas suposiciones eliminadas
    assert not hasattr(C, "login_mode_control") and not hasattr(C, "ruc_label")


def test_login_path_pattern_accepts_any_client_id_and_rejects_other_shapes():
    pat = re.compile(C.auth_path_pattern)
    assert pat.fullmatch("/v1/clientessol/anything-123/oauth2/loginMenuSol")
    assert pat.fullmatch("/v1/clientessol/4f3b88b3/oauth2/loginMenuSol/")
    for bad in ("/v1/clientessol//oauth2/loginMenuSol", "/v1/clientessol/a/b/oauth2/loginMenuSol", "/login",
                "/v1/clientessol/a/oauth2/loginMenuSol/extra", "/v2/clientessol/a/oauth2/loginMenuSol"):
        assert not pat.fullmatch(bad), bad


def test_accepted_landing_requires_every_condition():
    assert is_accepted_landing(LANDING, C)
    from dataclasses import replace

    assert not is_accepted_landing(replace(LANDING, title="Otra cosa"), C)
    assert not is_accepted_landing(replace(LANDING, url="https://e-menu.sunat.gob.pe/otra/ruta"), C)
    assert not is_accepted_landing(replace(LANDING, url="http://e-menu.sunat.gob.pe/cl-ti-itmenu/x"), C)
    assert not is_accepted_landing(replace(LANDING, password_field_visible=True), C)


def test_login_and_landing_and_entry_are_recognised():
    assert classify_surface((ENTRY,), C).kind is SurfaceKind.ENTRY
    assert classify_surface((ENTRY, LOGIN), C).kind is SurfaceKind.LOGIN
    assert classify_surface((LANDING,), C).kind is SurfaceKind.LANDING
    assert classify_surface((), C).kind is SurfaceKind.NO_PAGES
    assert classify_surface((PageView(url="about:blank", title=""),), C).kind is SurfaceKind.NO_PAGES


@pytest.mark.parametrize(
    "views",
    [
        (PageView(url="https://evil.example.com/", title="x"),),
        (PageView(url="https://www.sunat.gob.pe.evil.com/", title="x"),),  # sufijo malicioso
        (PageView(url="https://api-seguridad.sunat.gob.pe.evil.com/", title="x"),),
        (PageView(url="https://e-renta.sunat.gob.pe/x", title="x"),),  # host SUNAT conocido pero NO autorizado
        (PageView(url="http://api-seguridad.sunat.gob.pe/login", title="x"),),  # esquema http
        (LOGIN, LOGIN),  # ambiguo
        (LOGIN, LANDING),  # login y landing a la vez
        (PageView(url="https://e-menu.sunat.gob.pe/otra/ruta", title="SUNAT - Menú SOL"),),  # landing sin identidad
    ],
)
def test_unknown_or_ambiguous_surface_fails_closed(views):
    surface = classify_surface(views, C)
    assert surface.kind is SurfaceKind.FAIL
    assert surface.outcome is AuthOutcome.PORTAL_CHANGED


def test_auth_host_inside_a_frame_fails_closed_as_auth_host_changed():
    framed = PageView(url="https://www.sunat.gob.pe/sol.html", title="x",
                      frame_urls=("https://api-seguridad.sunat.gob.pe/login",))
    surface = classify_surface((framed,), C)
    assert surface.kind is SurfaceKind.FAIL and surface.detail == "AUTH_HOST_CHANGED"


def test_invalid_credentials_flag_only_comes_from_page_view():
    from dataclasses import replace

    assert classify_surface((replace(LOGIN, invalid_credentials_visible=True),), C).invalid_credentials
    assert not classify_surface((LOGIN,), C).invalid_credentials


def test_outcome_to_terminal_reason_uses_only_existing_domain_c_codes():
    from app.models.sol_agent_access import SolAccessTerminalReasonCode
    from windows_agent.portal_contract import TERMINAL_REASON_BY_OUTCOME

    valid = {c.value for c in SolAccessTerminalReasonCode}
    for outcome, code in TERMINAL_REASON_BY_OUTCOME.items():
        assert code is None or code in valid, (outcome, code)
    assert set(TERMINAL_REASON_BY_OUTCOME) == set(AuthOutcome)


def test_popup_window_on_the_landing_host_is_ignored_when_exactly_one_page_is_the_landing():
    from dataclasses import replace

    popup = PageView(url="https://e-menu.sunat.gob.pe/aviso/contacto", title="Aviso")
    surface = classify_surface((LANDING, popup), C)
    assert surface.kind is SurfaceKind.LANDING and surface.page_index == 0
    assert classify_surface((popup, LANDING), C).page_index == 1
    # dos landings completos, un aviso con campo de contraseña o un aviso en OTRO host siguen fallando cerrado
    assert classify_surface((LANDING, LANDING), C).kind is SurfaceKind.FAIL
    assert classify_surface((LANDING, replace(popup, password_field_visible=True)), C).kind is SurfaceKind.FAIL
    assert classify_surface((LANDING, PageView(url="https://evil.example.com/", title="x")), C).kind is SurfaceKind.FAIL
    assert classify_surface((LOGIN, LANDING, popup), C).kind is SurfaceKind.FAIL


def test_landing_host_page_in_transit_is_pending_never_accepted_and_never_an_immediate_failure():
    """Observado en vivo: el login pasa por una pagina del host de aterrizaje con la ruta del menu y un titulo transitorio."""
    from dataclasses import replace

    base = "https://e-menu.sunat.gob.pe/cl-ti-itmenu/SaltoLogin.htm"
    for title in ("", "Cargando", "Otra cosa"):
        surface = classify_surface((ENTRY, PageView(url=base, title=title)), C)
        assert surface.kind is SurfaceKind.PENDING and surface.page_index == 1, title
    # no es evidencia suficiente por si sola: el landing aceptado exige ademas titulo y ausencia de login
    assert classify_surface((PageView(url=base, title="SUNAT - Menú SOL"),), C).kind is SurfaceKind.LANDING
    # ruta fuera del contrato, esquema distinto, otro host o un campo de contraseña visible => fallo inmediato
    assert classify_surface((PageView(url="https://e-menu.sunat.gob.pe/otra/ruta", title=""),), C).kind is SurfaceKind.FAIL
    assert classify_surface((PageView(url="http://e-menu.sunat.gob.pe/cl-ti-itmenu/x", title=""),), C).kind is SurfaceKind.FAIL
    assert classify_surface((replace(PageView(url=base, title=""), password_field_visible=True),), C).kind is SurfaceKind.FAIL
    assert classify_surface((PageView(url="https://e-renta.sunat.gob.pe/cl-ti-itmenu/x", title=""),), C).kind is SurfaceKind.FAIL
