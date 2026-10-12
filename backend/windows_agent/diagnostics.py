"""M2 -- diagnostico ESTRUCTURAL y saneado de los fallos de la sesion supervisada (modulo puro, sin navegador).

Un fallo ya no se reduce a "El portal cambió": cada rechazo se clasifica por ETAPA y CODIGO ESTABLE, y se acompaña de una
observacion con SOLO contadores, categorias y booleanos (nunca HTML, valores de formularios, cookies, URLs, rutas, textos del portal ni
datos tributarios). Una URL/titulo/dominio aparecen unicamente como booleanos ("coincide con el contrato") o como la CLASE del host
(entrada | autenticacion | aterrizaje | desconocido), nunca como texto.

La lista de codigos es CERRADA y se replica en el backend (`app.broker.protocol.DIAGNOSTIC_CODES`); una prueba exige que coincidan."""

from __future__ import annotations

import re
from dataclasses import replace

from windows_agent.portal_contract import AuthOutcome, AuthResult

STAGES = (
    "BROWSER", "ENTRY", "LOGIN_SURFACE", "LOGIN_FORM", "SUBMIT", "LANDING", "SHELL", "NAVIGATION", "DESTINATION", "EXTRACTION",
    "VALIDATION", "REPORT", "HUMAN_CONTROL", "UNKNOWN",
)

# (patron sobre el `detail` fijo del codigo del agente, etapa, codigo). El orden importa: el primero que coincide gana.
_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = tuple(
    (re.compile(p), stage, code)
    for p, stage, code in (
        (r"^entry link not found$", "ENTRY", "ENTRY_LINK_NOT_FOUND"),
        (r"^navigation timeout$", "ENTRY", "NAVIGATION_TIMEOUT"),
        (r"^login surface not reached$", "LOGIN_SURFACE", "LOGIN_SURFACE_NOT_REACHED"),
        (r"^landing before authentication$", "LOGIN_SURFACE", "LANDING_BEFORE_AUTHENTICATION"),
        (r"^landing transit did not resolve to a login form$", "LOGIN_SURFACE", "LANDING_TRANSIT_UNRESOLVED"),
        (r"^page outside the known portal surface$", "LOGIN_SURFACE", "UNKNOWN_PAGE_OR_HOST"),
        (r"^ambiguous portal surface$", "LOGIN_SURFACE", "AMBIGUOUS_PORTAL_SURFACE"),
        (r"^auth surface inside a frame$|^AUTH_HOST_CHANGED$", "LOGIN_SURFACE", "AUTH_SURFACE_IN_FRAME"),
        (r"^login page path does not match", "LOGIN_SURFACE", "LOGIN_PATH_MISMATCH"),
        (r"^login page title does not match", "LOGIN_SURFACE", "LOGIN_TITLE_MISMATCH"),
        (r"^login control '.*' missing, ambiguous or structurally different$", "LOGIN_FORM", "LOGIN_CONTROL_MISMATCH"),
        (r"^submit control name differs$", "LOGIN_FORM", "LOGIN_SUBMIT_NAME_MISMATCH"),
        (r"^login mode controls missing or ambiguous$", "LOGIN_FORM", "LOGIN_MODE_CONTROLS_MISSING"),
        (r"^RUC mode is active but the field visibility", "LOGIN_FORM", "LOGIN_RUC_VISIBILITY_MISMATCH"),
        (r"^login mode state is ambiguous$", "LOGIN_FORM", "LOGIN_MODE_STATE_AMBIGUOUS"),
        (r"^RUC mode activation", "LOGIN_FORM", "LOGIN_RUC_MODE_ACTIVATION_FAILED"),
        (r"^login surface changed before submit$|^login controls changed before submit$", "LOGIN_FORM", "LOGIN_CHANGED_BEFORE_SUBMIT"),
        (r"^second submission refused$", "SUBMIT", "SECOND_SUBMISSION_REFUSED"),
        (r"^returned to entry page$", "SUBMIT", "RETURNED_TO_ENTRY_PAGE"),
        (r"^auth navigation stalled$", "SUBMIT", "AUTH_NAVIGATION_STALLED"),
        (r"^landing host reached but landing identity", "LANDING", "LANDING_IDENTITY_MISMATCH"),
        (r"^landing page not found$", "SHELL", "LANDING_PAGE_NOT_FOUND"),
        (r"^authenticated shell marker not found$|^shell marker '.*' missing or ambiguous$", "SHELL", "SHELL_MARKER_MISSING_OR_AMBIGUOUS"),
        (r"^login controls are present", "SHELL", "SHELL_LOGIN_CONTROLS_PRESENT"),
        (r"^menu home is not displayed$", "SHELL", "SHELL_STRUCTURE_MISMATCH"),
        (r"^application frame is not displayed$|^breadcrumb does not identify|^menu is still displayed$", "DESTINATION", "FICHA_DESTINATION_MISMATCH"),
        (r"^shell content region is ambiguous$|^shell .*", "SHELL", "SHELL_STRUCTURE_MISMATCH"),
        (r"^navigation blocked by an intermediate screen$", "NAVIGATION", "BLOCKED_BY_INTERMEDIATE_SCREEN"),
        (r"^navigation click budget exhausted$", "NAVIGATION", "NAVIGATION_BUDGET_EXHAUSTED"),
        (r"^navigation timed out$", "NAVIGATION", "NAVIGATION_TIMEOUT"),
        (r"^ficha menu entry not found or ambiguous$", "NAVIGATION", "FICHA_MENU_ENTRY_MISSING_OR_AMBIGUOUS"),
        (r"^ficha menu (depth|header)", "NAVIGATION", "FICHA_MENU_STRUCTURE_MISMATCH"),
        (r"^ficha frame identity not validated$", "DESTINATION", "FICHA_FRAME_NOT_VALIDATED"),
        (r"^ficha extraction did not identify the taxpayer$", "EXTRACTION", "FICHA_TAXPAYER_NOT_IDENTIFIED"),
        (r"^ficha content not ready$", "EXTRACTION", "FICHA_CONTENT_NOT_READY"),
        (r"^ficha report rejected$", "REPORT", "FICHA_REPORT_REJECTED"),
        (r"^ficha ", "DESTINATION", "FICHA_DESTINATION_MISMATCH"),
        (r"^human control report rejected$", "REPORT", "HUMAN_CONTROL_REPORT_REJECTED"),
        (r"^destination not allowed$|^extraction only allowed", "VALIDATION", "DESTINATION_NOT_ALLOWED"),
        (r"^unexpected executor error$", "BROWSER", "UNEXPECTED_EXECUTOR_ERROR"),
        (r"^CLEANUP_FAILED$", "BROWSER", "CLEANUP_FAILED"),
        (r"^CREDENTIAL_UNAVAILABLE$", "SUBMIT", "CREDENTIAL_UNAVAILABLE"),
    )
)

_FALLBACK_BY_OUTCOME = {
    AuthOutcome.PORTAL_CHANGED: ("UNKNOWN", "UNCLASSIFIED_PORTAL_CHANGE"),
    AuthOutcome.BROWSER_FAILURE: ("BROWSER", "BROWSER_CLOSED_OR_FAILED"),
    AuthOutcome.TRANSIENT_NAVIGATION_FAILURE: ("ENTRY", "NAVIGATION_TIMEOUT"),
    AuthOutcome.EXPIRED: ("SUBMIT", "HUMAN_CHALLENGE_TIMEOUT"),
    AuthOutcome.INVALID_CREDENTIALS: ("SUBMIT", "CREDENTIALS_REJECTED_BY_PORTAL"),
}

DIAGNOSTIC_CODES = frozenset({code for _p, _s, code in _RULES} | {code for _s, code in _FALLBACK_BY_OUTCOME.values()} | {"UNEXPECTED_ERROR"} | {"CONTACT_VALIDATION_INTERSTITIAL", "INFORMATIONAL_MODAL", "HUMAN_ACTION_REQUIRED"})  # + motivos de espera humana

_SAFE_DETAIL_CHARS = re.compile(r"[^A-Za-z0-9 '_.,:()-]")


def classify(detail: str | None, outcome: AuthOutcome) -> tuple[str, str]:
    """(etapa, codigo estable). Un detalle desconocido nunca se propaga como texto: cae en el codigo generico de su desenlace."""
    text = (detail or "").strip()
    for pattern, stage, code in _RULES:
        if pattern.search(text):
            return stage, code
    return _FALLBACK_BY_OUTCOME.get(outcome, ("UNKNOWN", "UNEXPECTED_ERROR"))


def safe_detail(detail: str | None) -> str:
    """El `detail` del agente son frases fijas del codigo; aun asi se restringen caracteres y longitud antes de registrarlas."""
    return _SAFE_DETAIL_CHARS.sub("", detail or "")[:120]


def annotate(result: AuthResult, *, observation: dict | None, shell_reached: bool = False, last_action: str = "") -> AuthResult:
    """Anade etapa/codigo/observacion a un resultado NO exitoso (el exito no necesita diagnostico)."""
    if result.outcome in {AuthOutcome.AUTH_SUCCEEDED, AuthOutcome.CANCELLED}:
        return replace(result, shell_reached=shell_reached) if shell_reached else result
    stage, code = classify(result.detail, result.outcome)
    obs = dict(observation or {})
    if last_action:
        obs["last_safe_action"] = last_action
    return replace(result, stage=stage, diagnostic_code=code, observation=obs or None, shell_reached=shell_reached)


# --- observacion estructural (pura: recibe vistas ya reducidas a datos no sensibles) ---


def host_class(host: str, contract) -> str:
    if host in contract.auth_hosts:
        return "auth"
    if host in contract.landing_hosts:
        return "landing"
    if host in contract.entry_hosts:
        return "entry"
    return "unknown"


def observe_pages(views, contract, host_of) -> list[dict]:
    """Por pagina: CLASE del host, esquema valido, numero de frames y booleanos de coincidencia con el contrato."""
    out = []
    for view in views:
        scheme, host, path = host_of(view.url)
        klass = host_class(host, contract)
        entry: dict = {
            "class": klass,
            "scheme_ok": scheme == contract.required_scheme,
            "frames": len(view.frame_urls),
            "password_field_visible": bool(view.password_field_visible),
            "human_challenge_visible": bool(getattr(view, "challenge_visible", False)),
        }
        if klass == "auth":
            entry["login_path_matches"] = re.fullmatch(contract.auth_path_pattern, path) is not None
            entry["login_title_matches"] = bool(view.title) and contract.auth_title_contains.lower() in view.title.lower()
        elif klass == "landing":
            entry["landing_path_prefix_ok"] = path.startswith(contract.landing_path_prefix)
            entry["landing_title_matches"] = contract.landing_title_contains.lower() in (view.title or "").lower()
        out.append(entry)
    return out


def surface_signature(views, contract, host_of) -> list[str]:
    """Firma compacta y SANEADA de las paginas en un instante (clase de host + booleanos de coincidencia; nunca URLs ni textos)."""
    out = []
    for view in views:
        _scheme, host, path = host_of(view.url)
        klass = host_class(host, contract)
        flags: list[str] = []
        if klass == "auth":
            flags.append("path_ok" if re.fullmatch(contract.auth_path_pattern, path) else "path_no")
            flags.append("title_ok" if view.title and contract.auth_title_contains.lower() in view.title.lower() else "title_no")
        elif klass == "landing":
            flags.append("path_ok" if path.startswith(contract.landing_path_prefix) else "path_no")
            flags.append("title_ok" if contract.landing_title_contains.lower() in (view.title or "").lower() else ("title_empty" if not view.title else "title_no"))
        if view.password_field_visible:
            flags.append("pw")
        if getattr(view, "challenge_visible", False):
            flags.append("challenge")
        if view.frame_urls:
            flags.append(f"frames={len(view.frame_urls)}")
        out.append(klass + ("(" + ",".join(flags) + ")" if flags else ""))
    return out


def observe_shell(snapshot, check) -> dict:
    """Evidencia ESTRUCTURAL del marcador de shell autenticado en una pagina del host de aterrizaje: que ids del contrato existen (cuantos
    y si son visibles) y el resultado del marcador positivo. Solo ids del contrato y booleanos; nunca texto ni valores."""
    present = {}
    for element_id, infos in snapshot.elements.items():
        if infos:
            present[element_id] = {"count": len(infos), "visible": any(i.visible for i in infos)}
    return {"marker_ok": bool(check.ok), "marker_detail": classify(check.detail, AuthOutcome.PORTAL_CHANGED)[1] if not check.ok else None, "ids_present": present}


def observe_login_controls(snapshot, contract) -> dict:
    """Por control del contrato: cuantos elementos hay con ese id y que atributos estructurales coinciden (sin valores)."""
    controls = {}
    for spec in (contract.ruc_field, contract.username_field, contract.password_field, contract.submit_button):
        found = snapshot.elements.get(spec.element_id, ())
        item: dict = {"count": len(found)}
        if len(found) == 1:
            info = found[0]
            item.update(
                tag_ok=info.tag == spec.tag,
                type_ok=info.type == spec.input_type,
                placeholder_ok=info.placeholder == spec.placeholder,
                maxlength_ok=spec.maxlength is None or info.maxlength == spec.maxlength,
                visible=bool(info.visible),
            )
        controls[spec.element_id] = item
    mode = {}
    for key, element_id in (("dni_field", contract.dni_field_id), ("ruc_button", contract.mode_ruc_button_id), ("dni_button", contract.mode_dni_button_id)):
        found = snapshot.elements.get(element_id, ())
        mode[key] = {"count": len(found)}
        if len(found) == 1:
            mode[key]["visible"] = bool(found[0].visible)
            mode[key]["active"] = contract.mode_active_class in found[0].classes
    return {"controls": controls, "mode": mode}


def observation_of(executor) -> dict | None:
    """Observacion del ejecutor si la ofrece (un ejecutor de pruebas sin navegador no la tiene). Nunca lanza."""
    observe = getattr(executor, "observe", None)
    if not callable(observe):
        return None
    try:
        return observe()
    except Exception:  # noqa: BLE001
        return {"observation_unavailable": True}


def last_action_of(executor) -> str:
    return str(getattr(executor, "last_action", "") or "")


def note_action(executor, name: str) -> None:
    setter = getattr(executor, "set_last_action", None)
    if callable(setter):
        setter(name)
