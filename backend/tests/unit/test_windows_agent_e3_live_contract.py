"""E3-LIVE-A2: contrato del login congelado con los hechos observados en LIVE-A1.
Logica PURA (sin navegador): identidad de la pagina de login (host/ruta dinamica/titulo),
patron de dos Pages, y la matriz fail-closed de controles/modo RUC. El comportamiento con
Chromium real contra el portal local lo cubre `tests/e3_portal/scenarios.py` (Windows)."""

from __future__ import annotations

import inspect
import re
from dataclasses import replace
from pathlib import Path

import pytest

import windows_agent.browser_executor as browser_executor
from windows_agent.portal_contract import SOL_PORTAL_CONTRACT as C
from windows_agent.portal_identity import (
    ElementInfo,
    LoginCheck,
    LoginSnapshot,
    PageView,
    SurfaceKind,
    check_login_controls,
    classify_surface,
)

PUBLIC = PageView(url="https://www.sunat.gob.pe/sol.html", title="SUNAT SOL Operaciones en Línea")


def login(path="/v1/clientessol/dyn-client-id/oauth2/loginMenuSol", host="api-seguridad.sunat.gob.pe",
          title="SUNAT - Menú SOL", scheme="https", **kw):
    return PageView(url=f"{scheme}://{host}{path}", title=title, password_field_visible=True, **kw)


# --- identidad de la pagina de login ---------------------------------------------------------


def test_two_page_pattern_public_page_stays_and_login_is_a_new_top_level_page():
    surface = classify_surface((PUBLIC, login()), C)
    assert surface.kind is SurfaceKind.LOGIN and surface.page_index == 1
    assert classify_surface((PUBLIC,), C).kind is SurfaceKind.ENTRY  # aun sin login: se sigue esperando
    # el orden de las Pages no importa (nunca se infiere por orden/foco)
    assert classify_surface((login(), PUBLIC), C).page_index == 0


@pytest.mark.parametrize("client_id", ["a", "4f3b88b3-d9d6-402a-b85d-6a0bc857746a", "XYZ_123", "9" * 40])
def test_dynamic_client_id_is_never_hardcoded(client_id):
    assert classify_surface((login(path=f"/v1/clientessol/{client_id}/oauth2/loginMenuSol"),), C).kind is SurfaceKind.LOGIN


@pytest.mark.parametrize(
    "view",
    [
        login(host="evil.example.com"),  # M: host inesperado
        login(host="api-seguridad.sunat.gob.pe.evil.com"),
        login(path="/login"),  # N: ruta fuera del patron
        login(path="/v1/clientessol/a/b/oauth2/loginMenuSol"),
        login(path="/v1/clientessol//oauth2/loginMenuSol"),
        login(title="Otra cosa"),  # titulo inesperado
        login(scheme="http"),
    ],
)
def test_unexpected_login_identity_fails_closed_before_credentials(view):
    surface = classify_surface((PUBLIC, view), C)
    assert surface.kind is SurfaceKind.FAIL


def test_login_inside_an_unknown_frame_fails_closed_as_auth_host_changed():  # O
    framed = replace(PUBLIC, frame_urls=("https://api-seguridad.sunat.gob.pe/v1/clientessol/x/oauth2/loginMenuSol",))
    surface = classify_surface((framed,), C)
    assert surface.kind is SurfaceKind.FAIL and surface.detail == "AUTH_HOST_CHANGED"


def test_post_submit_transitional_auth_urls_are_not_rejected_by_the_strict_identity():
    """Tras enviar, la URL de la pagina de autenticacion puede cambiar (POST/redireccion): la
    identidad estricta de ruta/titulo se exige solo ANTES de pedir la credencial."""
    transitional = login(path="/j_security_check", title="")
    assert classify_surface((PUBLIC, transitional), C, strict_auth_identity=False).kind is SurfaceKind.LOGIN
    assert classify_surface((PUBLIC, transitional), C).kind in {SurfaceKind.ENTRY, SurfaceKind.FAIL}


# --- controles del login ---------------------------------------------------------------------


def el(tag="input", type_="text", ph=None, maxlength=None, visible=True, classes=(), name=""):
    return ElementInfo(tag=tag, type=type_, placeholder=ph, maxlength=maxlength, visible=visible,
                       classes=tuple(classes), name_text=name)


def good(ruc_active=True) -> dict:
    return {
        "txtRuc": (el(ph="RUC", maxlength="11", visible=ruc_active),),
        "txtUsuario": (el(ph="Usuario", maxlength="8"),),
        "txtContrasena": (el(type_="password", ph="Contraseña", maxlength="12"),),
        "txtDni": (el(ph="DNI", maxlength="8", visible=not ruc_active),),
        "btnAceptar": (el(tag="button", type_="button", name="Iniciar sesión"),),
        "btnPorRuc": (el(tag="button", type_="button", classes=("btn", "active") if ruc_active else ("btn",)),),
        "btnPorDni": (el(tag="button", type_="button", classes=("btn",) if ruc_active else ("btn", "active")),),
    }


def check(elements) -> tuple[LoginCheck, str | None]:
    return check_login_controls(LoginSnapshot(elements), C)


def test_live_default_state_is_ready_without_any_mode_click():  # Q
    assert check(good()) == (LoginCheck.READY, None)


def test_inactive_ruc_mode_requests_exactly_the_activation_not_a_ready_state():  # R (parte pura)
    assert check(good(ruc_active=False)) == (LoginCheck.NEEDS_RUC_ACTIVATION, None)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("txtRuc"),  # E ausente
        lambda d: d.update(txtRuc=d["txtRuc"] * 2),  # F duplicado
        lambda d: d.update(txtRuc=(el(type_="number", ph="RUC", maxlength="11"),)),  # G tipo
        lambda d: d.update(txtRuc=(el(ph="RUC", maxlength="10"),)),  # H maxlength
        lambda d: d.update(txtRuc=(el(ph="Otro", maxlength="11"),)),  # placeholder distinto
        lambda d: d.update(txtRuc=(el(tag="textarea", type_=None, ph="RUC", maxlength="11"),)),
        lambda d: d.pop("txtUsuario"),  # I
        lambda d: d.update(txtUsuario=(el(ph="Usuario", maxlength="9"),)),
        lambda d: d.update(txtContrasena=(el(type_="text", ph="Contraseña", maxlength="12"),)),  # J
        lambda d: d.pop("txtContrasena"),
        lambda d: d.pop("btnAceptar"),  # K
        lambda d: d.update(btnAceptar=d["btnAceptar"] * 2),  # L ambiguo
        lambda d: d.update(btnAceptar=(el(tag="button", type_="submit", name="Iniciar sesión"),)),
        lambda d: d.update(btnAceptar=(el(tag="button", type_="button", name="Entrar"),)),
        lambda d: d.update(btnAceptar=(el(tag="div", type_=None, name="Iniciar sesión"),)),
        lambda d: d.pop("btnPorRuc"),
        lambda d: d.pop("btnPorDni"),
        lambda d: d.pop("txtDni"),
        lambda d: d.update(btnPorDni=d["btnPorRuc"]),  # ambos activos: ambiguo
        lambda d: d.update(btnPorRuc=(el(tag="button", type_="button", classes=("btn",)),),
                           btnPorDni=(el(tag="button", type_="button", classes=("btn",)),)),  # ninguno activo
        lambda d: d.update(txtRuc=(el(ph="RUC", maxlength="11", visible=False),)),  # modo RUC activo pero RUC oculto
        lambda d: d.update(txtDni=(el(ph="DNI", maxlength="8", visible=True),)),  # DNI visible en modo RUC
    ],
)
def test_any_structural_difference_fails_closed(mutate):
    elements = good()
    mutate(elements)
    verdict, detail = check(elements)
    assert verdict is LoginCheck.FAIL and detail


def test_no_field_relies_on_label_elements():
    """El portal real no usa <label>: la identidad es por id + atributos observados."""
    snap = good()
    assert all(not hasattr(e, "labels") for infos in snap.values() for e in infos)
    assert check(snap)[0] is LoginCheck.READY


# --- auditoria del codigo del ejecutor ---------------------------------------------------------


def _executor_code() -> str:
    import ast

    src = Path(browser_executor.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Module)) and node.body and isinstance(
            node.body[0], ast.Expr
        ) and isinstance(getattr(node.body[0], "value", None), ast.Constant) and isinstance(node.body[0].value.value, str):
            node.body[0].value.value = ""
    return ast.unparse(tree)


def test_public_entry_never_uses_a_global_ingresar_index():  # PUBLIC_ENTRY_GLOBAL_INGRESAR_INDEX_SELECTOR=NO
    code = _executor_code()
    for forbidden in (r"\.nth\(", r"\.first\b.*Ingresar", r"following::", r"\[1\]\)", r"get_by_text\(c\.entry_link_text"):
        assert re.search(forbidden, code) is None, forbidden
    entry = inspect.getsource(browser_executor.SupervisedAuthExecutor._entry_link)
    assert "following-sibling::*[1]" in entry and 'get_by_role("heading"' in entry and "re.I" in entry


def test_hidden_captcha_input_is_never_inspected_by_the_executor():  # HIDDEN_CAPTCHA_INPUT_MEANS_CHALLENGE=NO
    assert "captcha" not in _executor_code().lower()


def test_login_snapshot_js_is_read_only_and_never_reads_field_values():
    js = browser_executor._LOGIN_SNAPSHOT_JS
    assert ".value" not in js and "innerHTML" not in js and "textContent" not in js


def test_executor_has_a_single_evaluate_call_three_fills_and_three_clicks():
    code = _executor_code()
    # dos lecturas de SOLO LECTURA con scripts CONSTANTES: la instantanea de ids del login/shell y la lectura estructural de avisos
    # intermedios (`interstitial.INTERSTITIAL_JS`, vocabulario cerrado); ninguna recibe datos del llamador ni modifica la pagina.
    assert code.count(".evaluate(") == 2 and "_LOGIN_SNAPSHOT_JS" in code and "interstitial.INTERSTITIAL_JS" in code
    assert code.count(".fill(") == 3
    # entrada, activacion RUC (max 1), enviar + (M2) `guarded_click`: el UNICO clic post-aterrizaje, sujeto al presupuesto
    # de navegacion del destino (0 por defecto, congelado tras validar el destino).
    assert code.count(".click(") == 5  # + la comprobacion SIN efectos (`trial=True`) previa al unico clic post-aterrizaje


def test_ruc_mode_activation_is_capped_at_one_click_by_construction():
    src = inspect.getsource(browser_executor.SupervisedAuthExecutor._prepare_login_form)
    assert "_mode_clicks >= 1" in src and src.count(".click()") == 1


# --- LIVE-B: landing autenticado OBSERVADO (post-auth) ------------------------------------------------------------

OBSERVED_LANDING = PageView(
    url="https://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm", title="SUNAT - Menú SOL"
)


def test_live_b_observed_landing_is_frozen_and_accepted():
    from windows_agent.portal_identity import is_accepted_landing

    assert C.landing_observed_path == "/cl-ti-itmenu/MenuInternet.htm"
    assert C.landing_observed_title == "SUNAT - Menú SOL"
    assert is_accepted_landing(OBSERVED_LANDING, C)
    assert C.landing_observed_title.lower().find(C.landing_title_contains.lower()) >= 0
    assert C.landing_observed_path.startswith(C.landing_path_prefix)
    surface = classify_surface((PUBLIC, OBSERVED_LANDING), C, strict_auth_identity=False)
    assert surface.kind is SurfaceKind.LANDING and surface.page_index == 1


def test_observed_landing_also_accepts_the_oauth_original_url_path_prefix():
    """El prefijo cubre la ruta de Spike A (`AutenticaMenuInternet.htm`) y la observada (`MenuInternet.htm`)."""
    from windows_agent.portal_identity import is_accepted_landing

    assert is_accepted_landing(replace(OBSERVED_LANDING, url="https://e-menu.sunat.gob.pe/cl-ti-itmenu/AutenticaMenuInternet.htm"), C)


@pytest.mark.parametrize(
    "view",
    [
        replace(OBSERVED_LANDING, url="https://e-renta.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm"),  # host conocido pero NO autorizado
        replace(OBSERVED_LANDING, url="https://e-menu.sunat.gob.pe.evil.com/cl-ti-itmenu/MenuInternet.htm"),  # sufijo malicioso
        replace(OBSERVED_LANDING, url="https://evil.com/cl-ti-itmenu/MenuInternet.htm"),
        replace(OBSERVED_LANDING, url="https://e-menu.sunat.gob.pe/otra-app/MenuInternet.htm"),  # ruta incorrecta
        replace(OBSERVED_LANDING, url="https://e-menu.sunat.gob.pe/MenuInternet.htm"),
        replace(OBSERVED_LANDING, title="Otra aplicacion"),  # titulo incorrecto
        replace(OBSERVED_LANDING, title=""),
        replace(OBSERVED_LANDING, url="http://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm"),  # esquema
        replace(OBSERVED_LANDING, password_field_visible=True),  # el formulario de login sigue visible
    ],
)
def test_landing_that_does_not_match_the_full_identity_is_not_success(view):
    from windows_agent.portal_identity import is_accepted_landing

    assert not is_accepted_landing(view, C)
    surface = classify_surface((PUBLIC, view), C, strict_auth_identity=False)
    assert surface.kind is not SurfaceKind.LANDING


def test_login_page_is_never_mistaken_for_the_landing():
    mistaken = login(title="SUNAT - Menú SOL")  # mismo titulo que el landing (LIVE-A1/B): el titulo SOLO no basta
    surface = classify_surface((PUBLIC, mistaken), C, strict_auth_identity=False)
    assert surface.kind is SurfaceKind.LOGIN
    from windows_agent.portal_identity import is_accepted_landing

    assert not is_accepted_landing(mistaken, C)


def test_irrelevant_child_frames_neither_create_nor_block_the_landing():
    """Frames hijos sin hostname normal (about:blank, srcdoc, data:) o de otro host: el exito sigue dependiendo de la
    identidad de la Page de nivel superior; un frame jamas lo crea por si solo."""
    framed = replace(OBSERVED_LANDING, frame_urls=("about:blank", "about:srcdoc", "data:text/html,x", "https://cdn.example.org/a"))
    assert classify_surface((PUBLIC, framed), C, strict_auth_identity=False).kind is SurfaceKind.LANDING
    # un frame con la URL del landing dentro de una Page publica o de login NO crea exito
    for host_page in (PUBLIC, login()):
        decoy = replace(host_page, frame_urls=("https://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm",))
        assert classify_surface((decoy,), C, strict_auth_identity=False).kind is not SurfaceKind.LANDING
    # un frame con el host de AUTENTICACION dentro del landing si falla cerrado
    bad = replace(OBSERVED_LANDING, frame_urls=("https://api-seguridad.sunat.gob.pe/v1/clientessol/x/oauth2/loginMenuSol",))
    assert classify_surface((PUBLIC, bad), C, strict_auth_identity=False).kind is SurfaceKind.FAIL


def test_landing_acceptance_is_page_identity_only_never_network_telemetry():  # LANDING_SUCCESS_NETWORK_ONLY=NO
    from windows_agent import portal_identity

    src = Path(inspect.getsourcefile(portal_identity)).read_text(encoding="utf-8")
    for forbidden in ("response", "request", "status_code", "resource_type", "har"):
        assert f".{forbidden}" not in src and f"({forbidden}" not in src
    params = set(inspect.signature(portal_identity.is_accepted_landing).parameters)
    assert params == {"view", "contract"}  # solo la vista de Page/Frame + el contrato


def test_landing_contract_has_no_positive_structural_marker_only_the_negative_login_control():
    """Estado honesto del contrato: ninguna constante inventada de menu/cuenta. La unica condicion estructural es la
    AUSENCIA del control de contraseña del login."""
    import dataclasses

    names = {f.name for f in dataclasses.fields(C)}
    assert not any(("marker" in n or "shell" in n or "menu_text" in n) for n in names)
