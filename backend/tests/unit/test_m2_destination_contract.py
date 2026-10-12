"""M2: contrato ESTRUCTURAL del shell autenticado, de MENU_SOL y de FICHA_RUC (evidencia LIVE del reconocimiento).

Positivo en el shell, negativo en login / pagina publica / lookalike / duplicados / controles de login; el marcador NO
depende de host, URL ni titulo (las pruebas ni siquiera los pasan)."""

import pytest

from windows_agent import destination_contract as dc
from windows_agent.portal_identity import ElementInfo, LoginSnapshot


def el(tag, *, visible=True, classes=(), role=None, name="", type_=None):
    return ElementInfo(tag=tag, type=type_, placeholder=None, maxlength=None, visible=visible, classes=tuple(classes),
                       name_text=name, role=role)


def shell(state="MENU", **drop_or_override):
    elements = {
        "divCabecera": [el("div", classes=("navbar", "navbar-default", "navbar-fixed-top"), role="navigation")],
        "btnSalir": [el("button", visible=False, classes=("btn", "navbar-btn"), name="Cerrar sesión")],
        "ulMenu01": [el("ul", visible=False)],
        "divContainerAplicacion": [el("div", visible=state == "APPLICATION")],
        "iDivApplication": [el("div", visible=state == "APPLICATION")],
        "liOpcionFavorito": [el("li", visible=False)],
        "divContainerMenu": [el("div", visible=state == "MENU")],
        "divTodasOpciones": [el("div", visible=state == "MENU")],
    }
    if state == "APPLICATION":
        elements["iframeApplication"] = [el("iframe")]
        elements["liBreadNivel4"] = [el("li", name="Ficha RUC")]
    for key, value in drop_or_override.items():
        if value is None:
            elements.pop(key, None)
        else:
            elements[key] = value
    return LoginSnapshot(elements)


def login_page():
    """La pagina de login observada: tiene `#divCabecera` (otras clases) y los controles de login; NINGUN id del shell."""
    return LoginSnapshot({
        "divCabecera": [el("div", classes=("navbar", "navbar-default"), role="navigation")],
        "txtRuc": [el("input")], "txtContrasena": [el("input", type_="password")], "btnAceptar": [el("button")],
    })


def test_authenticated_shell_marker_is_positive_in_both_content_states():
    assert dc.check_shell_marker(shell("MENU")) == dc.ShellCheck(True, None, "MENU")
    assert dc.check_shell_marker(shell("APPLICATION")) == dc.ShellCheck(True, None, "APPLICATION")


def test_login_page_public_page_and_lookalike_are_negative():
    assert not dc.check_shell_marker(login_page()).ok  # login
    assert not dc.check_shell_marker(LoginSnapshot({})).ok  # pagina publica / vacia
    lookalike = LoginSnapshot({
        "divCabecera": [el("div", classes=("navbar-fixed-top",), role="navigation")],
        "btnSalir": [el("button", name="Cerrar sesión")],
    })
    assert not dc.check_shell_marker(lookalike).ok  # imita cabecera + salir, falta el resto del shell


@pytest.mark.parametrize("missing", list(dc.SHELL_REQUIRED_ONCE))
def test_every_required_marker_is_individually_necessary(missing):
    result = dc.check_shell_marker(shell(**{missing: None}))
    assert not result.ok and missing in (result.detail or "")


@pytest.mark.parametrize("duplicated", list(dc.SHELL_REQUIRED_ONCE))
def test_duplicated_marker_is_ambiguous_and_fails_closed(duplicated):
    snap = shell()
    doubled = shell(**{duplicated: snap.elements[duplicated] * 2})
    assert not dc.check_shell_marker(doubled).ok


@pytest.mark.parametrize("login_id", dc.SHELL_FORBIDDEN_IDS)
def test_login_controls_present_means_not_the_shell(login_id):
    assert not dc.check_shell_marker(shell(**{login_id: [el("input")]})).ok


def test_shell_header_must_have_role_class_and_be_visible():
    base = {"role": "navigation", "classes": ("navbar-fixed-top",)}
    assert not dc.check_shell_marker(shell(divCabecera=[el("div", role=None, classes=base["classes"])])).ok
    assert not dc.check_shell_marker(shell(divCabecera=[el("div", role="navigation", classes=("navbar-default",))])).ok
    assert not dc.check_shell_marker(shell(divCabecera=[el("div", visible=False, **base)])).ok


def test_content_region_must_be_exactly_one_of_menu_or_application():
    both = shell(divContainerMenu=[el("div")], iDivApplication=[el("div")])
    neither = shell(divContainerMenu=[el("div", visible=False)], iDivApplication=[el("div", visible=False)])
    assert not dc.check_shell_marker(both).ok and not dc.check_shell_marker(neither).ok


def test_tag_of_each_marker_is_checked():
    assert not dc.check_shell_marker(shell(btnSalir=[el("a", name="Cerrar sesión")])).ok


def test_menu_sol_destination():
    assert dc.check_menu_sol_destination(shell("MENU")).ok
    assert not dc.check_menu_sol_destination(shell("APPLICATION")).ok  # una aplicacion abierta no es el menu
    assert not dc.check_menu_sol_destination(login_page()).ok
    assert not dc.check_menu_sol_destination(shell("MENU", iframeApplication=[el("iframe")])).ok


def test_ficha_ruc_top_level_requires_app_region_iframe_menu_hidden_and_breadcrumb():
    assert dc.check_ficha_ruc_top(shell("APPLICATION")).ok
    assert not dc.check_ficha_ruc_top(shell("MENU")).ok
    assert not dc.check_ficha_ruc_top(shell("APPLICATION", iframeApplication=[el("iframe", visible=False)])).ok
    assert not dc.check_ficha_ruc_top(shell("APPLICATION", liBreadNivel4=[el("li", name="Otra consulta")])).ok
    assert not dc.check_ficha_ruc_top(shell("APPLICATION", liBreadNivel4=[el("li", visible=False, name="Ficha RUC")])).ok
    assert not dc.check_ficha_ruc_top(shell("APPLICATION", divContainerMenu=[el("div")])).ok
    assert dc.check_ficha_ruc_top(shell("APPLICATION", liBreadNivel4=[el("li", name="  FICHA   ruc ")])).ok is False  # solo trim/lower


def ficha_view(**o):
    base = dict(url="https://ww1.sunat.gob.pe/ol-ti-itmoddatruc/abc123xyz", once_id_counts={"popupcabinas": 1, "validaCIIUvsCamRegRTAESP": 1},
                visible_form_tables=11)
    base.update(o)
    return dc.FichaFrameView(**base)


def test_ficha_frame_identity_is_positive_only_with_origin_markers_and_tables():
    assert dc.check_ficha_ruc_frame(ficha_view()).ok
    for bad in (
        ficha_view(url="https://evil.example/ol-ti-itmoddatruc/x"),
        ficha_view(url="http://ww1.sunat.gob.pe/ol-ti-itmoddatruc/x"),
        ficha_view(url="https://ww1.sunat.gob.pe/otra-app/x"),
        ficha_view(once_id_counts={"popupcabinas": 1}),
        ficha_view(once_id_counts={"popupcabinas": 2, "validaCIIUvsCamRegRTAESP": 1}),
        ficha_view(visible_form_tables=dc.FICHA_MIN_VISIBLE_FORM_TABLES - 1),
    ):
        assert not dc.check_ficha_ruc_frame(bad).ok


def test_destination_allowlist_is_closed_and_unvalidated_destinations_do_not_exist():
    assert dc.DESTINATION_VALUES == ("MENU_SOL", "FICHA_RUC")
    for unvalidated in ("SIRE", "DECLARACIONES_Y_PAGOS", "DECLARA_FACIL", "TRAMITES_Y_CONSULTAS", "BUZON", "https://x"):
        with pytest.raises(ValueError):
            dc.Destination(unvalidated)


def test_click_budget_per_destination():
    assert dc.MAX_CLICKS_BY_DESTINATION == {dc.Destination.MENU_SOL: 0, dc.Destination.FICHA_RUC: 4}


def test_contract_module_never_mentions_consequential_actions_or_buzon():
    import inspect

    code = "\n".join(
        line for line in inspect.getsource(dc).splitlines() if not line.strip().startswith(("#", '"""'))
    ).lower()
    for term in ("enviar", "presentar", "pagar", "rectificar", "buzon", "buzón"):
        assert f'"{term}' not in code
