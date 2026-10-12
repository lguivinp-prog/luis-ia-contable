"""M2 -- sanitizador ESTRICTO (puro, solo libreria estandar) del reconocimiento estructural de SOL.

Principio: MINIMIZACION DE DATOS. La salida describe SOLO estructura; ningun texto libre, valor, cookie, query ni
identificador dinamico sale de aqui. Lo que no esta en una lista blanca explicita se DESCARTA (no se enmascara):

* texto: solo etiquetas de navegacion de `NAV_LABELS` (igualdad exacta tras normalizar) -> se guarda la CLAVE canonica;
* titulo de pagina: solo `TITLE_ALLOWLIST` (igualdad exacta) -> clave canonica;
* ids / clases: solo tokens "de desarrollador" (letras, digitos, _ y -; sin rachas de 3+ digitos; sin cadenas largas
  con digitos); cualquier otro -> "[redacted]";
* role / tag / type: listas blancas;
* URL: host (solo dominios de SUNAT o loopback de pruebas) + ruta normalizada; SIN query, fragmento ni credenciales;
  segmentos dinamicos -> "{id}"; los href solo se conservan para enlaces cuya etiqueta esta en la lista blanca.

Cada funcion vuelve a validar su entrada (defensa en profundidad): aunque el extractor del navegador fallara, nada
fuera de la lista blanca puede llegar al archivo."""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

SANITIZER_VERSION = 1

# Etiquetas de navegacion genericas del portal (clave canonica -> formas normalizadas aceptadas). EXPLICITA.
NAV_LABELS: dict[str, tuple[str, ...]] = {
    "MENU_SOL": ("menú sol", "menu sol"),
    "FICHA_RUC": ("ficha ruc",),
    "SIRE": ("sire",),
    "DECLARACIONES_Y_PAGOS": ("declaraciones y pagos",),
    "DECLARA_FACIL": ("declara fácil", "declara facil"),
    "TRAMITES_Y_CONSULTAS": ("trámites y consultas", "tramites y consultas", "mis trámites y consultas", "mis tramites y consultas"),
    # Genericos que ayudan a distinguir el shell autenticado del login/pagina publica y a ubicar la ruta del menu.
    "EMPRESAS": ("empresas",),
    "MI_RUC_Y_OTROS_REGISTROS": ("mi ruc y otros registros",),
    "CERRAR_SESION": ("cerrar sesión", "cerrar sesion", "salir"),
    "INICIAR_SESION": ("iniciar sesión", "iniciar sesion"),
    "INGRESAR": ("ingresar",),
}
_LABEL_BY_TEXT = {form: key for key, forms in NAV_LABELS.items() for form in forms}
NAV_TEXT_FORMS: list[str] = sorted(_LABEL_BY_TEXT)  # se pasa al extractor del navegador (solo para comparar)

TITLE_ALLOWLIST: dict[str, str] = {
    "sunat - menú sol": "SUNAT_MENU_SOL",
    "sunat - menu sol": "SUNAT_MENU_SOL",
}

ALLOWED_TAGS = frozenset(
    {"a", "button", "input", "select", "textarea", "form", "nav", "ul", "ol", "li", "div", "span", "table", "iframe",
     "frame", "h1", "h2", "h3", "h4", "label", "section", "header", "footer", "main", "aside", "img"}
)
ALLOWED_ROLES = frozenset(
    {"navigation", "menu", "menubar", "menuitem", "tree", "treeitem", "group", "button", "link", "tab", "tablist",
     "tabpanel", "main", "banner", "contentinfo", "complementary", "dialog", "alert", "form", "list", "listitem"}
)
ALLOWED_INPUT_TYPES = frozenset(
    {"text", "password", "hidden", "button", "submit", "checkbox", "radio", "search", "number", "tel", "email", "date"}
)
ALLOWED_HOST_SUFFIXES = ("sunat.gob.pe",)
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})

_SAFE_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
_DIGIT_RUN_3 = re.compile(r"\d{3,}")
_DYNAMIC_SEGMENT = re.compile(r"(\d{3,})|([0-9a-fA-F]{16,})|([A-Za-z0-9_-]{25,})")
MAX_NODES = 800
REDACTED = "[redacted]"


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def label_key(text: str | None) -> str | None:
    """Clave canonica si el texto (normalizado) esta EXACTAMENTE en la lista blanca; si no, None (se descarta)."""
    return _LABEL_BY_TEXT.get(normalize_text(text or ""))


def title_key(title: str | None) -> str:
    return TITLE_ALLOWLIST.get(normalize_text(title or ""), "[not-allowlisted]")


def safe_token(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if not _SAFE_TOKEN.match(value) or _DIGIT_RUN_3.search(value) or (len(value) > 24 and any(c.isdigit() for c in value)):
        return REDACTED
    return value


def safe_classes(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for token in value[:4]:
        tok = safe_token(token)
        if tok is not None:
            out.append(tok)
    return out


def safe_url(url: object) -> dict:
    """Solo esquema + host permitido + ruta normalizada. Sin query, fragmento, usuario ni puerto."""
    if not isinstance(url, str) or not url:
        return {"scheme": None, "host": None, "path": None}
    try:
        parts = urlsplit(url)
    except ValueError:
        return {"scheme": REDACTED, "host": REDACTED, "path": REDACTED}
    scheme = parts.scheme if parts.scheme in {"https", "http", "about", "data", "blob"} else REDACTED
    if scheme in {"about", "data", "blob"}:
        return {"scheme": scheme, "host": None, "path": None}
    host = parts.hostname or ""
    if host in LOOPBACK_HOSTS or host.endswith(".sunat.gob.pe") or host == "sunat.gob.pe":
        host_out = host
    else:
        host_out = "[other-host]"
    segments = []
    for seg in (parts.path or "/").split("/"):
        segments.append("{id}" if seg and _DYNAMIC_SEGMENT.search(seg) else seg[:40])
    path = "/".join(segments)[:120]
    return {"scheme": scheme, "host": host_out, "path": path}


def _safe_href(href: object, key: str | None) -> str | None:
    # Solo para enlaces con etiqueta de la lista blanca; se reduce a ruta normalizada (nunca query/fragmento).
    if key is None or not isinstance(href, str) or not href:
        return None
    if href.strip().lower().startswith(("javascript:", "data:", "vbscript:")):
        return "[script-href]"
    sanitized = safe_url(href if "://" in href else "https://placeholder.sunat.gob.pe" + (href if href.startswith("/") else "/" + href))
    return sanitized["path"]


def sanitize_node(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    tag = raw.get("tag")
    if tag not in ALLOWED_TAGS:
        return None
    key = label_key(raw.get("text_probe")) if isinstance(raw.get("text_probe"), str) else None
    aria = label_key(raw.get("aria_probe")) if isinstance(raw.get("aria_probe"), str) else None
    role = raw.get("role") if raw.get("role") in ALLOWED_ROLES else (None if raw.get("role") is None else REDACTED)
    itype = raw.get("type")
    node = {
        "tag": tag,
        "id": safe_token(raw.get("id")),
        "classes": safe_classes(raw.get("classes")),
        "role": role,
        "input_type": (itype if itype in ALLOWED_INPUT_TYPES else REDACTED) if tag == "input" and itype else None,
        "label": key,
        "aria_label": aria,
        "href_path": _safe_href(raw.get("href"), key) if tag == "a" else None,
        "visible": bool(raw.get("visible")),
        "depth": raw["depth"] if isinstance(raw.get("depth"), int) and 0 <= raw["depth"] < 100 else None,
        "sibling_index": raw["sibling_index"] if isinstance(raw.get("sibling_index"), int) and 0 <= raw["sibling_index"] < 1000 else None,
        "parent_tag": raw.get("parent_tag") if raw.get("parent_tag") in ALLOWED_TAGS else None,
        "parent_id": safe_token(raw.get("parent_id")),
        "has_onclick": bool(raw.get("has_onclick")),
    }
    return node


def sanitize_frame_view(view: object) -> dict:
    if not isinstance(view, dict) or "nodes" not in view:
        return {"error": True}
    nodes = []
    for raw in list(view.get("nodes", []))[:MAX_NODES]:
        node = sanitize_node(raw)
        if node is not None:
            nodes.append(node)
    return {
        "title_key": title_key(view.get("title") if isinstance(view.get("title"), str) else None),
        "node_count": len(nodes),
        "password_control_exists": any(n["input_type"] == "password" for n in nodes),
        "visible_password_control": any(n["input_type"] == "password" and n["visible"] for n in nodes),
        "labels_present": sorted({n["label"] for n in nodes if n["label"]}),
        "labels_visible": sorted({n["label"] for n in nodes if n["label"] and n["visible"]}),
        "nodes": nodes,
        "truncated": bool(view.get("truncated")),
    }


_LEAK_PATTERNS = (re.compile(r"\d{5,}"), re.compile(r"@"), re.compile(r"\?"), re.compile(r"(?i)https?://[^\"]*[=&]"))


def assert_clean(serialized: str) -> None:
    """Ultima barrera: el JSON final no debe contener rachas largas de digitos, '@', '?' ni URLs con parametros."""
    for pattern in _LEAK_PATTERNS:
        match = pattern.search(serialized)
        if match:
            raise ValueError(f"salida con contenido potencialmente sensible (patron {pattern.pattern!r}); no se escribe")


def dumps(snapshots: list[dict]) -> str:
    text = json.dumps(snapshots, ensure_ascii=False, indent=1)
    assert_clean(text)
    return text
