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

M3: el catalogo de destinos es DATOS (`CONTRACTS`), no ramas de codigo. Los destinos CONOCIDOS sin entrada de menu REAL observada estan
`NOT_VALIDATED`: el agente se niega a abrirlos ANTES de lanzar el navegador. Un contrato solo pasa a `LIVE_VALIDATED` con evidencia estructural
real (reconocimiento supervisado) y pruebas de identidad positivas y negativas. Un destino desconocido tambien FALLA CERRADO.

Reconocimiento del propietario (credencial principal, correccion de alcance M3), solo lo OBSERVADO:
* TRAMITES_Y_CONSULTAS es la tarjeta publica que conduce al login y, autenticado, al Menu SOL completo (segun las autorizaciones de la credencial):
  es un ALIAS de MENU_SOL (no un destino distinto, no se duplica ni se vuelve a autenticar).
* DECLARACIONES_Y_PAGOS: hoja `li.nivel3` (2 niveles de cabecera) que abre una NUEVA Page `e-menu.sunat.gob.pe/cl-ti-itmenu2/MenuInternetPlataforma.htm`
  (plataforma con su propio menu, la bandeja de pagos y el frame `e-plataformaunica`). La hoja aparece DUPLICADA en el mismo `li` (nodos de escritorio/
  movil) y no se sabe cual es accionable: la ENTRADA queda sin validar. DECLARA_FACIL es una hoja `nivel3` DENTRO de esa plataforma: no es un destino
  independiente.
* SIRE_RVIE / SIRE_RCE: frames de aplicacion `e-factura.sunat.gob.pe/app/contribuyentems/servicio/{libroventas|librocompras}/mige/...` con los ids
  `btn-aceptar`, `nav-generacion-registro`, `nav-informacion-gestion`. Se observo la IDENTIDAD DE LLEGADA pero no la entrada de menu (su etiqueta no estaba
  en la lista blanca del reconocimiento): siguen `NOT_VALIDATED`.
* SIRE_GENERACION_REGISTROS: sin observacion alguna. Solo APERTURA supervisada (presupuesto = pantalla inicial); cargar archivos, generar, presentar,
  enviar, declarar, pagar o rectificar JAMAS se automatiza (`FORBIDDEN_AUTOMATION`): tras validar la llegada la automatizacion termina."""

from __future__ import annotations

import enum
import re
import dataclasses
from dataclasses import dataclass
from urllib.parse import urlsplit

from windows_agent.portal_identity import ElementInfo, LoginSnapshot


class Destination(str, enum.Enum):
    """Destinos CONOCIDOS (M3). Conocido != habilitado: ver `DestinationContract.validation`. El frontend solo envia este enum."""

    MENU_SOL = "MENU_SOL"
    FICHA_RUC = "FICHA_RUC"
    SIRE = "SIRE"
    SIRE_RVIE = "SIRE_RVIE"
    SIRE_RCE = "SIRE_RCE"
    SIRE_GENERACION_REGISTROS = "SIRE_GENERACION_REGISTROS"
    DECLARACIONES_Y_PAGOS = "DECLARACIONES_Y_PAGOS"
    DECLARA_FACIL = "DECLARA_FACIL"
    TRAMITES_Y_CONSULTAS = "TRAMITES_Y_CONSULTAS"


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

LIVE_VALIDATED = "LIVE_VALIDATED"
NOT_VALIDATED = "NOT_VALIDATED"

# Operaciones que NINGUN contrato automatiza: el navegador se entrega al humano al validar la llegada (espejo de `app.core.sunat_destinations`).
FORBIDDEN_AUTOMATION = ("UPLOAD_FILE", "GENERATE_RECORDS", "SUBMIT", "SEND", "DECLARE", "PAY", "RECTIFY")

# Identidad de llegada OBSERVADA (reconocimiento real) de las aplicaciones SIRE y de la plataforma de Declaraciones y Pagos.
SIRE_APP_HOSTS = frozenset({"e-factura.sunat.gob.pe"})
SIRE_APP_ONCE_IDS = ("btn-aceptar", "nav-generacion-registro", "nav-informacion-gestion")
SIRE_RVIE_PATH_PREFIX = "/app/contribuyentems/servicio/libroventas/mige/gestionlibroventas/1.0.0/"
SIRE_RCE_PATH_PREFIX = "/app/contribuyentems/servicio/librocompras/mige/gestionlibrocompras/1.0.0/"
SIRE_GENERACION_PATH_PREFIX = "/app/contribuyentems/servicio/libro/mige/generacionlibro/1.0.0/"
PLATFORM_HOSTS = frozenset({"e-menu.sunat.gob.pe"})
PLATFORM_PATH_PREFIX = "/cl-ti-itmenu2/MenuInternetPlataforma.htm"
PLATFORM_ONCE_IDS = ("btnSalir", "iframeApplication", "ulSubmenu", "aOpcionPlataforma", "divBreadcrumb")


@dataclass(frozen=True)
class EntrySpec:
    """Como se llega al destino desde el shell autenticado. SOLO se declara con una secuencia OBSERVADA en vivo."""

    kind: str  # "MENU_LEAF": hoja `li.nivelN > span.spanNivelDescripcion` de la jerarquia del menu | "HOME_LINK": enlace del inicio del menu |
    # "PUBLIC_CARD": tarjeta de la pagina PUBLICA de SUNAT (antes del login). El clic de la tarjeta ocurre ANTES del login (ejecutor de autenticacion, contrato
    # fijado por `configure_public_entry`); el motor solo valida la plataforma de llegada. Sigue sin habilitarse hasta la aceptacion en vivo.
    label: str  # texto EXACTO (normalizado) de la hoja o del enlace
    leaf_level: int | None = None  # MENU_LEAF: nivel de la hoja (`nivelN`)
    min_levels: int = 0  # MENU_LEAF: niveles de cabecera a abrir antes de la hoja (minimo / maximo observados)
    max_levels: int = 0


@dataclass(frozen=True)
class ArrivalSpec:
    """Identidad estructural POSITIVA de llegada."""

    kind: str  # "FRAME": iframe de aplicacion dentro del shell | "NEW_PAGE": nueva Page de nivel superior
    hosts: frozenset[str]
    path_prefix: str
    scheme: str = "https"
    required_once_ids: tuple[str, ...] = ()  # ids que deben existir EXACTAMENTE una vez en el frame / la Page de llegada
    min_visible_tables: int = 0  # FRAME: tablas de formulario visibles minimas
    breadcrumb_label: str | None = None  # FRAME: texto exacto de la miga de pan
    breadcrumb_id: str = "liBreadNivel4"
    table_selector: str = "table.form-table"  # FRAME: que tablas visibles cuentan (la Ficha usa `table.form-table`; Generacion de Registros, `table`)


@dataclass(frozen=True)
class DestinationContract:
    id: str
    label: str
    validation: str
    entry: EntrySpec | None = None  # None => sin navegacion (MENU_SOL) o NO habilitado
    arrival: ArrivalSpec | None = None
    extraction: bool = False
    human_control: bool = True  # tras validar el destino la automatizacion TERMINA y el navegador pasa al humano
    evidence: str = "NONE"  # evidencia del reconocimiento (espejo del catalogo del backend): COMPLETE | RECON_COMPLETE | LIVE_ACCEPTED | ARRIVAL_ONLY | ALIAS | CONTAINED | NONE
    evidence_ref: tuple[str, ...] = ()  # checkpoints del reconocimiento (m3_recon_v2.json, fuera del repositorio) que respaldan el contrato
    alias_of: str | None = None  # mismo destino funcional que otro (se resuelve en el backend; el agente nunca recibe un alias)
    contained_in: str | None = None  # se alcanza DESDE otro destino
    open_only: bool = False  # solo apertura supervisada (la pantalla inicial es el limite del presupuesto)

    @property
    def enabled(self) -> bool:
        return self.validation == LIVE_VALIDATED

    @property
    def recon_max_clicks(self) -> int:
        """Presupuesto que tendria el contrato una vez habilitado (niveles de cabecera + la hoja); no es operativo mientras no este LIVE_VALIDATED."""
        if self.entry is None or self.entry.kind != "MENU_LEAF":
            return 0
        return self.entry.max_levels + 1

    @property
    def max_clicks(self) -> int:
        """Presupuesto DERIVADO de la secuencia validada (nunca una estimacion): niveles de cabecera + la hoja / 1 enlace de inicio."""
        if not self.enabled or self.entry is None:
            return 0
        if self.entry.kind == "PUBLIC_CARD":
            return 0  # el clic de la tarjeta ocurre ANTES del login (clic de entrada del ejecutor); tras el landing NO hay clics de navegacion
        return self.entry.max_levels + 1 if self.entry.kind == "MENU_LEAF" else 1


def acceptance_eligible(contract: "DestinationContract") -> bool:
    """Un contrato aun NO validado puede ejecutarse UNA vez en modo de ACEPTACION REAL solo si tiene evidencia completa (RECON_COMPLETE), entrada y llegada,
    no es solo-apertura (Generacion de Registros queda MANUAL_ONLY), no es alias/contenido y no extrae datos. El backend ademas exige una autorizacion del
    propietario por cliente/perfil/destino; el agente jamas activa este modo por su cuenta."""
    return (
        contract.evidence in {"RECON_COMPLETE", "LIVE_ACCEPTED"} and not contract.open_only and not contract.extraction and contract.alias_of is None
        and contract.contained_in is None and contract.entry is not None and contract.arrival is not None
    )


def for_acceptance(contract: "DestinationContract") -> "DestinationContract":
    """Copia del contrato tratada como validada SOLO durante esta sesion (el catalogo `CONTRACTS` no cambia). Falla cerrado si no es elegible."""
    if contract.enabled:
        return contract
    if not acceptance_eligible(contract):
        raise ValueError("destination not eligible for acceptance")
    return dataclasses.replace(contract, validation=LIVE_VALIDATED)


def public_landing_prefix(arrival: "ArrivalSpec") -> str:
    """Directorio del landing de un destino de entrada publica (p. ej. `/cl-ti-itmenu2/`): el login de ESTA tarjeta aterriza en la plataforma, no en el Menu SOL."""
    prefix = arrival.path_prefix
    return prefix[: prefix.rindex("/") + 1] if "/" in prefix else "/"


def _ficha_arrival() -> ArrivalSpec:
    return ArrivalSpec(
        "FRAME", FICHA_FRAME_HOSTS, FICHA_FRAME_PATH_PREFIX, "https", FICHA_FRAME_ONCE_IDS, FICHA_MIN_VISIBLE_FORM_TABLES, FICHA_NAV_LABEL
    )


CONTRACTS: dict[str, DestinationContract] = {
    "MENU_SOL": DestinationContract("MENU_SOL", "Menú SOL", LIVE_VALIDATED),
    "FICHA_RUC": DestinationContract(
        "FICHA_RUC", "Ficha RUC", LIVE_VALIDATED,
        entry=EntrySpec("MENU_LEAF", FICHA_NAV_LABEL, leaf_level=4, min_levels=1, max_levels=FICHA_MAX_NAV_CLICKS - 1),
        arrival=_ficha_arrival(), extraction=True,
    ),
    # Alias de MENU_SOL (evidencia del reconocimiento): hereda su validacion y su navegacion (ninguna).
    "TRAMITES_Y_CONSULTAS": DestinationContract("TRAMITES_Y_CONSULTAS", "Trámites y Consultas", LIVE_VALIDATED, alias_of="MENU_SOL"),
    # M3: conocidos y DESHABILITADOS. La identidad de llegada OBSERVADA queda como dato (`arrival`) pero SIN `entry` no hay navegacion posible
    # (`_navigate_contract` falla cerrado) y el presupuesto de clics es 0. Se completan SOLO con la entrada de menu real.
    "SIRE": DestinationContract("SIRE", "SIRE", NOT_VALIDATED),
    # CONTRATOS COMPLETOS con evidencia REAL (entrada de menu + llegada vinculada) y ACEPTACION EN VIVO del agente por el propietario (14/10/2026): RVIE, RCE y
    # Declaraciones y Pagos son `LIVE_VALIDATED` (`evidence="LIVE_ACCEPTED"`). El agente valida la ESTRUCTURA del portal; que un perfil pueda abrirlos lo decide
    # el backend POR PERFIL (la autorizacion de una credencial no se extrapola). Generacion de Registros sigue NOT_VALIDATED y MANUAL. El texto de cada hoja es el
    # EXACTO que el reconocimiento identifico por huella (RVIE con tilde; RCE y Generacion sin tilde). Hojas `nivel4` a 3 niveles de cabecera.
    "SIRE_RVIE": DestinationContract(
        "SIRE_RVIE", "SIRE · Registro de Ventas (RVIE)", LIVE_VALIDATED,
        entry=EntrySpec("MENU_LEAF", "gestión de ventas e ingresos electrónicos", leaf_level=4, min_levels=3, max_levels=3),
        arrival=ArrivalSpec("FRAME", SIRE_APP_HOSTS, SIRE_RVIE_PATH_PREFIX, "https", SIRE_APP_ONCE_IDS),
        evidence="LIVE_ACCEPTED", evidence_ref=("C004", "C005"),
    ),
    "SIRE_RCE": DestinationContract(
        "SIRE_RCE", "SIRE · Registro de Compras (RCE)", LIVE_VALIDATED,
        entry=EntrySpec("MENU_LEAF", "gestion de compras", leaf_level=4, min_levels=3, max_levels=3),
        arrival=ArrivalSpec("FRAME", SIRE_APP_HOSTS, SIRE_RCE_PATH_PREFIX, "https", SIRE_APP_ONCE_IDS),
        evidence="LIVE_ACCEPTED", evidence_ref=("C006", "C008"),
    ),
    # Solo APERTURA: la pantalla no expone ids; su identidad es host + ruta + 2 tablas visibles. Cargar, validar, generar, aceptar o enviar JAMAS se automatiza.
    "SIRE_GENERACION_REGISTROS": DestinationContract(
        "SIRE_GENERACION_REGISTROS", "SIRE · Generación de Registros Electrónicos", NOT_VALIDATED, open_only=True,
        entry=EntrySpec("MENU_LEAF", "generacion de registros electronicos", leaf_level=4, min_levels=3, max_levels=3),
        arrival=ArrivalSpec("FRAME", SIRE_APP_HOSTS, SIRE_GENERACION_PATH_PREFIX, "https", (), 2, table_selector="table"),
        evidence="RECON_COMPLETE", evidence_ref=("C010", "C011"),
    ),
    # La entrada REAL parte de la pagina PUBLICA (tarjeta «Mis Declaraciones y Pagos» -> INGRESAR), NO del Menu SOL: tras el login SUNAT aterriza directamente
    # en la plataforma `MenuInternetPlataforma.htm` (checkpoints reales C012 entrada publica -> C013 llegada, vinculados). El motor soporta `PUBLIC_CARD`
    # (encabezado exacto + ancestro mas cercano con UN solo «Ingresar» + landing `/cl-ti-itmenu2/` validado por ids); aceptado en vivo el 14/10/2026 (2.o intento).
    "DECLARACIONES_Y_PAGOS": DestinationContract(
        "DECLARACIONES_Y_PAGOS", "Declaraciones y Pagos", LIVE_VALIDATED,
        entry=EntrySpec("PUBLIC_CARD", "mis declaraciones y pagos"),
        arrival=ArrivalSpec("NEW_PAGE", PLATFORM_HOSTS, PLATFORM_PATH_PREFIX, "https", PLATFORM_ONCE_IDS),
        evidence="LIVE_ACCEPTED", evidence_ref=("C012", "C013"),
    ),
    "DECLARA_FACIL": DestinationContract("DECLARA_FACIL", "Declara Fácil", NOT_VALIDATED, contained_in="DECLARACIONES_Y_PAGOS"),
}


def get_contract(destination: str) -> DestinationContract:
    """Contrato del destino o ValueError (destino desconocido => FALLA CERRADO)."""
    try:
        return CONTRACTS[destination]
    except KeyError:
        raise ValueError(destination) from None


# Compatibilidad M2: presupuesto de clics de los destinos HABILITADOS (derivado de sus contratos).
MAX_CLICKS_BY_DESTINATION = {Destination(c.id): c.max_clicks for c in CONTRACTS.values() if c.enabled and c.alias_of is None}


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


def check_application_top(snapshot: LoginSnapshot, arrival: ArrivalSpec) -> ShellCheck:
    """Nivel superior de un destino que se abre en el frame de aplicacion: shell + region de aplicacion visible con su iframe visible y el
    menu oculto + (si el contrato la declara) miga de pan con la etiqueta EXACTA. Generaliza `check_ficha_ruc_top`."""
    shell = check_shell_marker(snapshot)
    if not shell.ok:
        return shell
    frame = _one(snapshot, "iframeApplication")
    menu = _one(snapshot, "divContainerMenu")
    if shell.content_state != "APPLICATION" or frame is None or frame.tag != "iframe" or not frame.visible:
        return ShellCheck(False, "application frame is not displayed")
    if menu is not None and menu.visible:
        return ShellCheck(False, "menu is still displayed")
    if arrival.breadcrumb_label is not None:
        crumb = _one(snapshot, arrival.breadcrumb_id)
        if crumb is None or not crumb.visible or crumb.name_text.strip().lower() != arrival.breadcrumb_label:
            return ShellCheck(False, "breadcrumb does not identify the destination")
    return ShellCheck(True, None, "APPLICATION")


def check_arrival_frame(view: FichaFrameView, arrival: ArrivalSpec) -> ShellCheck:
    """Identidad POSITIVA del frame de llegada: origen (esquema + host) + ruta + ids unicos + tablas visibles. Generaliza la Ficha."""
    parts = urlsplit(view.url)
    if parts.scheme != arrival.scheme or parts.hostname not in arrival.hosts or not parts.path.startswith(arrival.path_prefix):
        return ShellCheck(False, "destination frame origin/path does not match the contract")
    for element_id in arrival.required_once_ids:
        if view.once_id_counts.get(element_id, 0) != 1:
            return ShellCheck(False, f"destination frame marker '{element_id}' missing or ambiguous")
    if view.visible_form_tables < arrival.min_visible_tables:
        return ShellCheck(False, "destination frame does not show the expected structure")
    return ShellCheck(True)


def check_arrival_page(url: str, once_id_counts: dict[str, int], arrival: ArrivalSpec) -> ShellCheck:
    """Identidad POSITIVA de una nueva Page de llegada (esquema + host + ruta + ids unicos)."""
    parts = urlsplit(url)
    if parts.scheme != arrival.scheme or parts.hostname not in arrival.hosts or not parts.path.startswith(arrival.path_prefix):
        return ShellCheck(False, "destination page origin/path does not match the contract")
    for element_id in arrival.required_once_ids:
        if once_id_counts.get(element_id, 0) != 1:
            return ShellCheck(False, f"destination page marker '{element_id}' missing or ambiguous")
    return ShellCheck(True)


def normalize_label(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()
