"""M2 -- contratos ESTRUCTURALES post-aterrizaje (shell autenticado y destinos de SOL). Modulo PURO (sin Playwright).

FUENTE: reconocimiento M2 LIVE (JSON sanitizado, cuenta secundaria de control, el humano navego a mano; el script
solo observo). Hechos observados (nada se adivina):

* SHELL AUTENTICADO: UNA Page de nivel superior (`e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm`), cuyo frame
  principal contiene EXACTAMENTE UNA vez cada uno de: `button#btnSalir`, `ul#ulMenu01`, `div#divContainerAplicacion`,
  `div#iDivApplication`, `li#liOpcionFavorito` y `div#divCabecera` (role=navigation, clase `navbar-fixed-top`).
  La pagina de LOGIN (`api-seguridad`) tambien tiene `#divCabecera` (clases `navbar navbar-default`, role=navigation)
  pero NO tiene ninguno de los otros cinco ids, y SI tiene `#txtRuc`/`#txtContrasena`/`#btnAceptar`, ausentes en el shell.
  => el marcador NO depende de host, URL ni titulo: es una CONJUNCION de ids estructurales + AUSENCIA de los controles
  de login + una region de contenido visible (`#divContainerMenu` o `#iDivApplication`).
* MENU_SOL: shell + `#divContainerMenu` y `#divTodasOpciones` visibles + `#iDivApplication` NO visible.
* FICHA_RUC: la Page NO cambia; aparece UN frame de aplicacion (`ww1.sunat.gob.pe/ol-ti-itmoddatruc/<id>`) dentro de
  `iframe#iframeApplication` (visible) en `#iDivApplication` (visible), con `#divContainerMenu` oculto. El frame de la
  Ficha contiene `input#popupcabinas` y `input#validaCIIUvsCamRegRTAESP` (ocultos, UNO cada uno) y varias
  `table.form-table` visibles (11 observadas). Ruta de menu: `li.nivel1 > li.nivel2 > li.nivel3 > li.nivel4` (4 clics).

SIN VALIDAR EN VIVO (no se exponen): SIRE, DECLARACIONES_Y_PAGOS, DECLARA_FACIL, TRAMITES_Y_CONSULTAS -- la cuenta de
control no los expone. No existen en este modulo ni en el enum `Destination`: un destino desconocido FALLA CERRADO."""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from windows_agent.portal_identity import ElementInfo, LoginSnapshot


class Destination(str, enum.Enum):
    """Allowlist servidor/agente de destinos LIVE-VALIDADOS. El frontend solo envia este enum."""

    MENU_SOL = "MENU_SOL"
    FICHA_RUC = "FICHA_RUC"


DESTINATION_VALUES = tuple(d.value for d in Destination)

# --- shell autenticado ---
SHELL_REQUIRED_ONCE = ("btnSalir", "ulMenu01", "divContainerAplicacion", "iDivApplication", "liOpcionFavorito", "divCabecera")
SHELL_REQUIRED_TAGS = {
    "btnSalir": "button",
    "ulMenu01": "ul",
    "divContainerAplicacion": "div",
    "iDivApplication": "div",
    "liOpcionFavorito": "li",
    "divCabecera": "div",
}
SHELL_FORBIDDEN_IDS = ("txtRuc", "txtContrasena", "btnAceptar")
SHELL_HEADER_ROLE = "navigation"
SHELL_HEADER_CLASS = "navbar-fixed-top"
SHELL_CONTENT_IDS = ("divContainerMenu", "divTodasOpciones", "iframeApplication")

# Ids a leer del DOM del frame principal (un snapshot por id, sin leer valores).
SHELL_SNAPSHOT_IDS = tuple(dict.fromkeys(SHELL_REQUIRED_ONCE + SHELL_FORBIDDEN_IDS + SHELL_CONTENT_IDS + ("liBreadNivel4",)))

# --- ficha ruc ---
FICHA_FRAME_HOSTS = frozenset({"ww1.sunat.gob.pe"})
FICHA_FRAME_PATH_PREFIX = "/ol-ti-itmoddatruc/"
FICHA_FRAME_ONCE_IDS = ("popupcabinas", "validaCIIUvsCamRegRTAESP")
FICHA_MIN_VISIBLE_FORM_TABLES = 5
FICHA_NAV_LABEL = "ficha ruc"  # texto exacto (normalizado) del enlace de menu
FICHA_MAX_NAV_CLICKS = 4  # nivel1..nivel4; ningun clic mas hasta validar el destino

MAX_CLICKS_BY_DESTINATION = {Destination.MENU_SOL: 0, Destination.FICHA_RUC: FICHA_MAX_NAV_CLICKS}


@dataclass(frozen=True)
class ShellCheck:
    ok: bool
    detail: str | None = None
    content_state: str | None = None  # "MENU" | "APPLICATION"


def _one(snapshot: LoginSnapshot, element_id: str) -> ElementInfo | None:
    found = snapshot.elements.get(element_id, ())
    return found[0] if len(found) == 1 else None


def _count(snapshot: LoginSnapshot, element_id: str) -> int:
    return len(snapshot.elements.get(element_id, ()))


def check_shell_marker(snapshot: LoginSnapshot) -> ShellCheck:
    """MARCADOR POSITIVO del shell autenticado (DOM): conjuncion de ids estructurales, cabecera con rol/clase
    del shell, region de contenido visible y AUSENCIA de los controles de login. Fail closed ante duplicados."""
    for element_id in SHELL_REQUIRED_ONCE:
        info = _one(snapshot, element_id)
        if info is None:
            return ShellCheck(False, f"shell marker '{element_id}' missing or ambiguous")
        if info.tag != SHELL_REQUIRED_TAGS[element_id]:
            return ShellCheck(False, f"shell marker '{element_id}' has an unexpected tag")
    header = _one(snapshot, "divCabecera")
    if header is None or header.role != SHELL_HEADER_ROLE or SHELL_HEADER_CLASS not in header.classes or not header.visible:
        return ShellCheck(False, "shell header does not match the expected structure")
    for element_id in SHELL_FORBIDDEN_IDS:
        if _count(snapshot, element_id) != 0:
            return ShellCheck(False, "login controls are present: this is not the authenticated shell")
    menu, app = _one(snapshot, "divContainerMenu"), _one(snapshot, "iDivApplication")
    menu_visible = menu is not None and menu.visible
    app_visible = app is not None and app.visible
    if menu_visible == app_visible:
        return ShellCheck(False, "shell content region is ambiguous")
    return ShellCheck(True, None, "MENU" if menu_visible else "APPLICATION")


def check_menu_sol_destination(snapshot: LoginSnapshot) -> ShellCheck:
    shell = check_shell_marker(snapshot)
    if not shell.ok:
        return shell
    grid = _one(snapshot, "divTodasOpciones")
    if shell.content_state != "MENU" or grid is None or not grid.visible or _count(snapshot, "iframeApplication") != 0:
        return ShellCheck(False, "menu home is not displayed")
    return ShellCheck(True, None, "MENU")


def check_ficha_ruc_top(snapshot: LoginSnapshot) -> ShellCheck:
    """Nivel superior de la Ficha: shell + region de aplicacion visible con su iframe visible y menu oculto +
    migas de pan con la etiqueta exacta 'Ficha RUC' (la lectura del texto la hace el ejecutor y la compara)."""
    shell = check_shell_marker(snapshot)
    if not shell.ok:
        return shell
    frame = _one(snapshot, "iframeApplication")
    crumb = _one(snapshot, "liBreadNivel4")
    menu = _one(snapshot, "divContainerMenu")
    if shell.content_state != "APPLICATION" or frame is None or frame.tag != "iframe" or not frame.visible:
        return ShellCheck(False, "application frame is not displayed")
    if menu is not None and menu.visible:
        return ShellCheck(False, "menu is still displayed")
    if crumb is None or not crumb.visible or crumb.name_text.strip().lower() != FICHA_NAV_LABEL:
        return ShellCheck(False, "breadcrumb does not identify Ficha RUC")
    return ShellCheck(True, None, "APPLICATION")


@dataclass(frozen=True)
class FichaFrameView:
    """Vista NO secreta del frame de la Ficha: URL + conteos estructurales (ningun texto ni valor)."""

    url: str
    once_id_counts: dict[str, int]
    visible_form_tables: int


def check_ficha_ruc_frame(
    view: FichaFrameView, *, hosts: frozenset[str] = FICHA_FRAME_HOSTS, scheme: str = "https"
) -> ShellCheck:
    """`hosts`/`scheme` solo se sobreescriben en pruebas con el portal local (produccion usa los valores observados)."""
    parts = urlsplit(view.url)
    if parts.scheme != scheme or parts.hostname not in hosts or not parts.path.startswith(FICHA_FRAME_PATH_PREFIX):
        return ShellCheck(False, "ficha frame origin/path does not match the contract")
    for element_id in FICHA_FRAME_ONCE_IDS:
        if view.once_id_counts.get(element_id, 0) != 1:
            return ShellCheck(False, f"ficha frame marker '{element_id}' missing or ambiguous")
    if view.visible_form_tables < FICHA_MIN_VISIBLE_FORM_TABLES:
        return ShellCheck(False, "ficha frame does not show the expected data tables")
    return ShellCheck(True)


def normalize_label(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()
