"""M2 -- regresion del sanitizador y auditoria estatica del script de reconocimiento de SOL (solo observa).

Las herramientas viven en `scripts/` (raiz del repositorio); si no es visible (p. ej. contenedor Docker) se omite."""

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
pytestmark = pytest.mark.skipif(not (SCRIPTS / "m2_recon_sanitizer.py").exists(), reason="raiz del repositorio no disponible (Docker)")

if (SCRIPTS / "m2_recon_sanitizer.py").exists():
    sys.path.insert(0, str(SCRIPTS))
    import m2_recon_sanitizer as san  # noqa: E402

SECRET_TEXTS = ["RAZON SOCIAL FICTICIA", "JUAN PEREZ", "20123456789", "titular@falso.pe", "AV. SECRETA 123", "S/ 98,765.43"]


def raw_node(**overrides):
    base = {"tag": "a", "id": "lnk", "classes": ["menu"], "role": None, "type": None, "text_probe": None,
            "aria_probe": None, "href": None, "visible": True, "depth": 4, "sibling_index": 0, "parent_tag": "li",
            "parent_id": "liX", "has_onclick": False}
    base.update(overrides)
    return base


def test_only_exact_allowlisted_text_survives():
    assert san.label_key("  Ficha   RUC ") == "FICHA_RUC"
    assert san.label_key("Declaraciones y Pagos") == "DECLARACIONES_Y_PAGOS"
    assert san.label_key("Declara Fácil") == "DECLARA_FACIL"
    assert san.label_key("Ficha RUC de JUAN PEREZ") is None
    for secret in SECRET_TEXTS:
        assert san.label_key(secret) is None


def test_hostile_node_fields_never_reach_output():
    node = san.sanitize_node(raw_node(id="ruc20123456789", classes=["ok", "x20123456789", "a" * 60, "tel987654321"],
                                      role="JUAN PEREZ", type="password",
                                      text_probe="razon social ficticia", aria_probe="juan perez",
                                      parent_id="token-abcdef1234567890abcdef", tag="input"))
    dumped = json.dumps(node)
    assert node["id"] == san.REDACTED and node["parent_id"] == san.REDACTED
    assert node["classes"] == ["ok", san.REDACTED, san.REDACTED, san.REDACTED]
    assert node["role"] == san.REDACTED
    assert node["label"] is None and node["aria_label"] is None
    for secret in SECRET_TEXTS + ["987654321"]:
        assert secret.lower() not in dumped.lower()


def test_unknown_tags_and_garbage_are_dropped():
    assert san.sanitize_node(raw_node(tag="script")) is None
    assert san.sanitize_node("texto libre") is None
    assert san.sanitize_node(None) is None


def test_input_values_are_never_modeled():
    node = san.sanitize_node(raw_node(tag="input", type="hidden", value="HIDDENSECRET", id="hid"))
    assert node["input_type"] == "hidden" and "HIDDENSECRET" not in json.dumps(node)
    assert san.sanitize_node(raw_node(tag="input", type="weird-type"))["input_type"] == san.REDACTED


def test_url_sanitization_strips_query_fragment_credentials_and_dynamic_segments():
    url = san.safe_url("https://user:pw@api-seguridad.sunat.gob.pe:443/v1/clientessol/2a1b3c4d5e6f7a8b9c0d1e2f3a4b5c6d/oauth2/loginMenuSol?token=T&state=S#frag")
    assert url == {"scheme": "https", "host": "api-seguridad.sunat.gob.pe", "path": "/v1/clientessol/{id}/oauth2/loginMenuSol"}
    assert san.safe_url("https://evil.example.com/a?b=1")["host"] == "[other-host]"
    assert san.safe_url("https://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm?x=1#y")["path"] == "/cl-ti-itmenu/MenuInternet.htm"
    assert san.safe_url("about:blank") == {"scheme": "about", "host": None, "path": None}
    assert san.safe_url("javascript:alert(1)")["scheme"] == san.REDACTED
    assert san.safe_url("https://www.sunat.gob.pe/menu/20123456789/ficha")["path"] == "/menu/{id}/ficha"


def test_href_only_kept_for_allowlisted_links_and_scripts_masked():
    with_label = san.sanitize_node(raw_node(text_probe="ficha ruc", href="/a/b?token=SECRET#f"))
    assert with_label["href_path"] == "/a/b"
    assert san.sanitize_node(raw_node(text_probe=None, href="/a/b?token=SECRET"))["href_path"] is None
    assert san.sanitize_node(raw_node(text_probe="sire", href="javascript:steal()"))["href_path"] == "[script-href]"


def test_title_is_allowlisted_only():
    assert san.title_key("SUNAT - Menú SOL") == "SUNAT_MENU_SOL"
    assert san.title_key("SUNAT - Ficha de JUAN PEREZ") == "[not-allowlisted]"


def test_frame_view_flags_and_final_barrier():
    view = san.sanitize_frame_view({"title": "SUNAT - Menú SOL", "truncated": False, "nodes": [
        raw_node(tag="input", type="password", id="pwd", visible=False), raw_node(text_probe="salir")]})
    assert view["password_control_exists"] is True and view["visible_password_control"] is False
    assert view["labels_present"] == ["CERRAR_SESION"]
    san.dumps([{"label": "X", "view": view}])
    for bad in ('{"a": "20123456789"}', '{"a": "x@y.pe"}', '{"a": "https://h/p?x=1"}', '{"a": "https://h/p?x=1&y=2"}'):
        with pytest.raises(ValueError):
            san.assert_clean(bad)


def test_node_cap():
    view = san.sanitize_frame_view({"title": "", "nodes": [raw_node() for _ in range(2000)]})
    assert view["node_count"] == san.MAX_NODES


# --- auditoria estatica del script de reconocimiento ---

FORBIDDEN_ATTRS = {"click", "dblclick", "fill", "type", "press", "check", "uncheck", "select_option", "dispatch_event",
                   "screenshot", "storage_state", "cookies", "add_cookies", "tracing", "route", "expose_function",
                   "expose_binding", "add_init_script", "set_input_files", "drag_to", "hover", "focus", "tap",
                   "input_value", "inner_text", "text_content", "inner_html", "content", "evaluate_handle",
                   "record_video_dir", "clipboard", "keyboard", "mouse", "touchscreen"}
FORBIDDEN_NAMES = {"record_video_dir", "storage_state", "clipboard", "pyperclip", "getpass"}


def _tree(name):
    return ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))


def test_recon_script_static_audit_has_no_active_or_capture_apis():
    tree = _tree("m2_sol_recon.py")
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    kwargs = {k.arg for n in ast.walk(tree) if isinstance(n, ast.Call) for k in n.keywords}
    assert not (attrs & FORBIDDEN_ATTRS), attrs & FORBIDDEN_ATTRS
    assert not (names & FORBIDDEN_NAMES), names & FORBIDDEN_NAMES
    assert not (kwargs & {"record_video_dir", "storage_state", "viewport_trace"}), kwargs
    imports = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imports |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not (imports & {"requests", "httpx", "urllib3", "socket", "subprocess", "pyperclip", "ctypes"}), imports


def test_recon_script_only_observes_through_fixed_evaluate_and_uses_enum_input():
    source = (SCRIPTS / "m2_sol_recon.py").read_text(encoding="utf-8")
    tree = _tree("m2_sol_recon.py")
    evaluates = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "evaluate"]
    assert len(evaluates) == 1
    assert isinstance(evaluates[0].args[0], ast.Name) and evaluates[0].args[0].id == "_EXTRACT_JS"
    js = next(n.value.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
              and any(getattr(t, "id", "") == "_EXTRACT_JS" for t in n.targets))
    for forbidden in ("innerText", "textContent", "innerHTML", "outerHTML", ".value", "localStorage", "sessionStorage",
                      "document.cookie", "indexedDB", "fetch(", "XMLHttpRequest", ".click(", ".focus("):
        assert forbidden not in js, forbidden
    assert "label not in OWNER_LABELS" in source and "path_taken" not in source


def test_recon_script_requires_output_outside_repository():
    source = (SCRIPTS / "m2_sol_recon.py").read_text(encoding="utf-8")
    assert "no puede estar dentro del repositorio" in source


# --- regresion del bloqueo: observacion dinamica del BrowserContext ---

def _recon():
    import importlib

    return importlib.import_module("m2_sol_recon")


def test_root_cause_regression_no_blocking_waits_or_sync_api():
    """El script original usaba la API SINCRONA con time.sleep/input(): los eventos de Playwright (paginas, URLs) no
    se procesaban y nunca veia el login. Debe usar la API asincrona y no bloquear el hilo de eventos."""
    source = (SCRIPTS / "m2_sol_recon.py").read_text(encoding="utf-8")
    tree = _tree("m2_sol_recon.py")
    assert "playwright.async_api" in source and "playwright.sync_api" not in source
    calls = {(getattr(n.func.value, "id", None), n.func.attr) for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert ("time", "sleep") not in calls
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "input"]
    assert "daemon=True" in source  # la lectura de consola no bloquea ni impide cerrar


def test_status_messages_and_explicit_timeouts_exist():
    recon = _recon()
    assert (recon.ST_WAIT_LOGIN, recon.ST_AUTHENTICATED, recon.ST_WAIT_LABEL, recon.ST_TIMEOUT) == (
        "ESPERANDO_LOGIN_MANUAL", "SESION_AUTENTICADA_DETECTADA", "ESPERANDO_ETIQUETA", "TIMEOUT_SIN_SESION_AUTENTICADA")
    cfg = recon.ReconConfig()
    assert 0 < cfg.login_timeout_s <= 1800 and 0 < cfg.status_every_s <= 60 and 0 < cfg.snapshot_timeout_s <= 30


def test_owner_labels_are_the_fixed_enum():
    assert _recon().OWNER_LABELS == ("AUTHENTICATED_SHELL", "MENU_SOL", "FICHA_RUC", "SIRE", "DECLARACIONES_Y_PAGOS",
                                     "DECLARA_FACIL", "TRAMITES_Y_CONSULTAS")


def test_classify_pages_is_structural_and_independent_of_page_order():
    recon = _recon()
    cfg = recon.ReconConfig()
    entry, login = "https://www.sunat.gob.pe/sol.html", "https://api-seguridad.sunat.gob.pe/v1/clientessol/x/oauth2/loginMenuSol"
    menu, ficha = "https://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm", "https://e-consultaruc.sunat.gob.pe/x"
    assert recon.classify_pages([], cfg)["candidates"] == 0
    assert recon.classify_pages([entry], cfg) == {"pages": 1, "entry": 1, "auth": 0, "candidates": 0, "other_blank": 0}
    assert recon.classify_pages([entry, login], cfg)["auth"] == 1
    # tras el login: la sesion puede estar en CUALQUIER pagina (reemplazo, nueva Page, popup), en cualquier orden
    for urls in ([menu], [entry, menu], [menu, entry], [ficha], [entry, ficha, menu], ["about:blank", ficha]):
        assert recon.classify_pages(urls, cfg)["candidates"] >= 1, urls
    assert recon.classify_pages(["about:blank", "chrome-error://chromewebdata/"], cfg)["candidates"] == 0
