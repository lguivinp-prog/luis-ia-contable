"""Fase 5, Bloque 5.2, Domain E3 -- clasificacion de la superficie del portal.

PRINCIPIO (Spike A, `observer/auth_surface.py`): la identidad de Page/Frame
es la FUENTE DE VERDAD; la telemetria de red es solo suplementaria y NUNCA
prueba autenticacion. Este modulo es PURO (sin Playwright): recibe vistas
inmutables de las Pages de nivel superior del `BrowserContext` y decide.

Nunca se infiere por orden de creacion, foco ni titulo solo. Ante cualquier
estado fuera del contrato: fail closed (`PORTAL_CHANGED`) -- jamas se
continua la automatizacion sobre una pagina desconocida."""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from windows_agent.portal_contract import (
    DETAIL_AUTH_HOST_CHANGED,
    AuthOutcome,
    PortalContract,
)

_BLANK_URLS = {"", "about:blank"}


class SurfaceKind(str, enum.Enum):
    NO_PAGES = "NO_PAGES"
    ENTRY = "ENTRY"  # solo paginas publicas de entrada
    LOGIN = "LOGIN"
    LANDING = "LANDING"
    # Pagina del host de aterrizaje con la ruta del contrato que AUN no cumple la identidad completa del landing (salto de redireccion
    # hacia el login o hacia el menu: documento cargando o con un titulo transitorio). NO es el landing y NO se acepta como tal; tampoco
    # es todavia un fallo: los llamadores esperan con plazo ACOTADO (sin clics ni credenciales) y, si no se resuelve, falla cerrado.
    PENDING = "PENDING"
    FAIL = "FAIL"  # fuera de contrato -> ver `outcome`/`detail`


@dataclass(frozen=True)
class PageView:
    """Vista inmutable de una Page de nivel superior. Los booleanos los
    evalua el llamador (el ejecutor); este modulo nunca toca el DOM."""

    url: str
    title: str
    frame_urls: tuple[str, ...] = ()
    password_field_visible: bool = False
    invalid_credentials_visible: bool = False
    login_controls_present: bool = False
    challenge_visible: bool = False  # widget de verificacion humana POSITIVAMENTE identificado (solo se evalua tras el envio)


@dataclass(frozen=True)
class Surface:
    kind: SurfaceKind
    page_index: int | None = None
    outcome: AuthOutcome | None = None
    detail: str | None = None
    invalid_credentials: bool = False
    human_challenge: bool = False
    auth_login_identity: bool = False  # la pagina de autenticacion conserva la ruta/titulo del formulario de login


def _fail(detail: str, *, outcome: AuthOutcome = AuthOutcome.PORTAL_CHANGED, code: str | None = None) -> Surface:
    return Surface(SurfaceKind.FAIL, outcome=outcome, detail=code or detail)


def _host(url: str) -> tuple[str | None, str | None, str]:
    parts = urlsplit(url)
    return parts.scheme or None, parts.hostname, parts.path


DETAIL_LANDING_IDENTITY = "landing host reached but landing identity does not match the contract"
DETAIL_LANDING_TRANSIT = "landing transit did not resolve to a login form"


def is_transitional_landing(view: PageView, contract: PortalContract) -> bool:
    """Pagina del host de aterrizaje en TRANSITO: host + esquema + prefijo de ruta del contrato y sin campo de contraseña, pero con
    titulo vacio o distinto del esperado. Solo significa "identidad aun no establecida"; nunca se acepta como landing."""
    scheme, host, path = _host(view.url)
    return (
        scheme == contract.required_scheme
        and host in contract.landing_hosts
        and path.startswith(contract.landing_path_prefix)
        and not view.password_field_visible
    )


def is_accepted_landing(view: PageView, contract: PortalContract) -> bool:
    """Landing aceptado = host + esquema exactos + prefijo de ruta + titulo +
    ausencia de formulario de login visible. Ninguna condicion basta sola."""
    scheme, host, path = _host(view.url)
    return (
        scheme == contract.required_scheme
        and host in contract.landing_hosts
        and path.startswith(contract.landing_path_prefix)
        and contract.landing_title_contains.lower() in view.title.lower()
        and not view.password_field_visible
    )


def classify_surface(
    views: tuple[PageView, ...], contract: PortalContract, *, strict_auth_identity: bool = True
) -> Surface:
    known = contract.entry_hosts | contract.auth_hosts | contract.landing_hosts
    pages = [(i, v) for i, v in enumerate(views) if v.url not in _BLANK_URLS]
    if not views:
        return Surface(SurfaceKind.NO_PAGES)

    # Frames de credenciales: nunca se continua (decision de Spike A).
    for i, v in pages:
        for furl in v.frame_urls:
            _s, fhost, _p = _host(furl)
            if fhost in contract.auth_hosts:
                return _fail("auth surface inside a frame", code=DETAIL_AUTH_HOST_CHANGED)

    auth_pages, landing_pages, entry_pages = [], [], []
    for i, v in pages:
        scheme, host, _path = _host(v.url)
        if host not in known or scheme != contract.required_scheme:
            return _fail("page outside the known portal surface")
        if host in contract.auth_hosts:
            auth_pages.append((i, v))
        elif host in contract.landing_hosts:
            landing_pages.append((i, v))
        else:
            entry_pages.append((i, v))

    # Avisos/ventanas emergentes del MISMO portal (host de aterrizaje): si exactamente UNA pagina cumple la identidad completa del
    # landing y ninguna otra muestra un campo de contraseña, las demas se IGNORAN (jamas se usan ni se pulsan). Cualquier otra
    # combinacion sigue siendo ambigua y falla cerrada.
    if len(landing_pages) > 1 and not auth_pages:
        accepted = [(i, v) for i, v in landing_pages if is_accepted_landing(v, contract)]
        if len(accepted) == 1 and not any(v.password_field_visible for _i, v in landing_pages):
            landing_pages = accepted

    if len(auth_pages) > 1 or len(landing_pages) > 1 or (auth_pages and landing_pages):
        return _fail("ambiguous portal surface")

    if landing_pages:
        i, v = landing_pages[0]
        if not is_accepted_landing(v, contract):
            if is_transitional_landing(v, contract):
                return Surface(SurfaceKind.PENDING, page_index=i)
            return _fail(DETAIL_LANDING_IDENTITY)
        return Surface(SurfaceKind.LANDING, page_index=i)

    if auth_pages:
        i, v = auth_pages[0]
        if strict_auth_identity:
            # Antes de pedir la credencial: ruta del login (client-id DINAMICO) y titulo esperados.
            _s, _h, path = _host(v.url)
            if re.fullmatch(contract.auth_path_pattern, path) is None:
                return _fail("login page path does not match the expected pattern")
            if not v.title:
                return Surface(SurfaceKind.ENTRY)  # DOM aun cargando: se sigue esperando (acotado)
            if contract.auth_title_contains.lower() not in v.title.lower():
                return _fail("login page title does not match the expected title")
        _s2, _h2, path2 = _host(v.url)
        login_identity = re.fullmatch(contract.auth_path_pattern, path2) is not None and contract.auth_title_contains.lower() in (v.title or "").lower()
        return Surface(SurfaceKind.LOGIN, page_index=i, invalid_credentials=v.invalid_credentials_visible,
                       human_challenge=v.challenge_visible, auth_login_identity=login_identity)

    return Surface(SurfaceKind.ENTRY if entry_pages else SurfaceKind.NO_PAGES)


# --- Identidad de los controles del login (LIVE-A1): logica PURA y testeable ---


@dataclass(frozen=True)
class ElementInfo:
    """Metadatos NO secretos de un elemento por id (nunca su valor)."""

    tag: str
    type: str | None
    placeholder: str | None
    maxlength: str | None
    visible: bool
    classes: tuple[str, ...] = ()
    name_text: str = ""  # nombre accesible aproximado: aria-label o texto del boton
    role: str | None = None  # M2: atributo role (estructural)


@dataclass(frozen=True)
class LoginSnapshot:
    """Por cada id esperado, TODOS los elementos que lo llevan (un id duplicado es ambiguedad)."""

    elements: dict[str, tuple[ElementInfo, ...]]


class LoginCheck(str, enum.Enum):
    READY = "READY"
    NEEDS_RUC_ACTIVATION = "NEEDS_RUC_ACTIVATION"
    FAIL = "FAIL"


def _one(snapshot: LoginSnapshot, element_id: str) -> ElementInfo | None:
    found = snapshot.elements.get(element_id, ())
    return found[0] if len(found) == 1 else None


def _field_ok(info: ElementInfo | None, spec) -> bool:
    return (
        info is not None
        and info.tag == spec.tag
        and info.type == spec.input_type
        and info.placeholder == spec.placeholder
        and (spec.maxlength is None or info.maxlength == spec.maxlength)
    )


def check_login_controls(snapshot: LoginSnapshot, contract: PortalContract) -> tuple[LoginCheck, str | None]:
    """Valida la identidad de los controles ANTES de pedir la credencial. Fail closed ante:
    control ausente, duplicado (ambiguo) o con atributos distintos de los observados. El modo RUC
    activo no requiere clic; inactivo => `NEEDS_RUC_ACTIVATION` (el ejecutor hace UN clic y revalida)."""
    c = contract
    for spec in (c.ruc_field, c.username_field, c.password_field, c.submit_button):
        info = _one(snapshot, spec.element_id)
        if not _field_ok(info, spec):
            return LoginCheck.FAIL, f"login control '{spec.element_id}' missing, ambiguous or structurally different"
    submit = _one(snapshot, c.submit_button.element_id)
    if submit.name_text.strip() != c.submit_name:
        return LoginCheck.FAIL, "submit control name differs"
    dni = _one(snapshot, c.dni_field_id)
    ruc_btn, dni_btn = _one(snapshot, c.mode_ruc_button_id), _one(snapshot, c.mode_dni_button_id)
    if dni is None or dni.tag != "input" or ruc_btn is None or ruc_btn.tag != "button" \
            or dni_btn is None or dni_btn.tag != "button":
        return LoginCheck.FAIL, "login mode controls missing or ambiguous"
    ruc_active, dni_active = c.mode_active_class in ruc_btn.classes, c.mode_active_class in dni_btn.classes
    ruc_field, user, pwd = _one(snapshot, c.ruc_field.element_id), _one(snapshot, c.username_field.element_id), \
        _one(snapshot, c.password_field.element_id)
    if ruc_active and not dni_active:
        if ruc_field.visible and user.visible and pwd.visible and not dni.visible:
            return LoginCheck.READY, None
        return LoginCheck.FAIL, "RUC mode is active but the field visibility is not the expected one"
    if dni_active and not ruc_active:
        return LoginCheck.NEEDS_RUC_ACTIVATION, None
    return LoginCheck.FAIL, "login mode state is ambiguous"
