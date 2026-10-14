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
    # M3 (correccion de alcance): las tres aplicaciones SIRE. Solo sirven para ETIQUETAR la entrada de menu observada; si el texto real difiere no se
    # registra etiqueta (nunca se guarda texto arbitrario) y el analizador lo informa como entrada no observada.
    "SIRE_RVIE": ("registro de ventas e ingresos electrónico", "registro de ventas e ingresos electronico", "registro de ventas e ingresos", "registro de ventas", "rvie"),
    "SIRE_RCE": ("registro de compras electrónico", "registro de compras electronico", "registro de compras", "rce"),
    "SIRE_GENERACION_REGISTROS": ("generación de registros electrónicos", "generacion de registros electronicos", "generación de registros", "generacion de registros"),
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
        "ancestor_cuerpos": raw["ancestor_cuerpos"] if isinstance(raw.get("ancestor_cuerpos"), int) and 0 <= raw["ancestor_cuerpos"] < 10 else None,
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


# ======================================================================================================================
# M3 v2 -- checkpoints PRE (menu expandido, antes del clic) y LLEGADA. Sigue siendo ESTRUCTURA: sin HTML, sin texto libre.
# ======================================================================================================================
SANITIZER_VERSION_V2 = 2

# Etiquetas de RUTA de menu (clave canonica -> formas normalizadas). Igualdad EXACTA tras normalizar; si no coincide NO se guarda el texto: solo una
# HUELLA (`digest`, 12 letras, sin digitos) del texto normalizado de las hojas/cabeceras de MENU, que permite reconocer duplicados e identidad sin
# almacenar texto. Las rutas son las que el propietario observo en el portal.
ROUTE_LABELS: dict[str, tuple[str, ...]] = {
    "SIRE_ROOT": ("sistema integrado de registros electrónicos", "sistema integrado de registros electronicos", "sire"),
    "SIRE_RVIE_REGISTRO": ("registro de ventas e ingresos electrónico", "registro de ventas e ingresos electronico"),
    "SIRE_RVIE_GESTION": ("gestión de ventas e ingresos electrónicos", "gestion de ventas e ingresos electronicos"),
    "SIRE_RCE_REGISTRO": ("registro de compras electrónico", "registro de compras electronico"),
    "SIRE_RCE_GESTION": ("gestión de compras", "gestion de compras"),
    "SIRE_GENERACION_REGISTROS": ("generación de registros electrónicos", "generacion de registros electronicos"),
    "DECLARACIONES_Y_PAGOS": ("declaraciones y pagos",),
    "DECLARA_FACIL": ("declara fácil", "declara facil"),
    "TRAMITES_Y_CONSULTAS": ("trámites y consultas", "tramites y consultas", "mis trámites y consultas", "mis tramites y consultas"),
    "EMPRESAS": ("empresas",),
    # Pagina PUBLICA de SUNAT: tarjetas de entrada (el encabezado es texto publico, no datos del contribuyente)
    "MIS_DECLARACIONES_Y_PAGOS": ("mis declaraciones y pagos",),
}
_ROUTE_BY_TEXT = {form: key for key, forms in ROUTE_LABELS.items() for form in forms}
ROUTE_TEXT_FORMS: list[str] = sorted(_ROUTE_BY_TEXT)
MENU_LEVELS = range(1, 8)
MAX_MENU_NODES = 1500
MAX_DIGEST_SOURCE = 120


def route_key(text: str | None) -> str | None:
    return _ROUTE_BY_TEXT.get(normalize_text(text or ""))


def safe_digest(value: object) -> str | None:
    """Huella de 12 LETRAS (sin digitos: no dispara los detectores de fuga) calculada en el navegador; aqui solo se valida su forma."""
    return value if isinstance(value, str) and re.fullmatch(r"[g-v]{12}", value) else None


def digest_of(text: str) -> str:
    """FNV-1a de 64 bits sobre los codigos de caracter del texto normalizado (mismo algoritmo que el extractor JS) -> 12 letras."""
    h = 0xCBF29CE484222325
    for ch in normalize_text(text)[:MAX_DIGEST_SOURCE]:
        h = ((h ^ ord(ch)) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return "".join(chr(ord("g") + ((h >> (60 - 4 * i)) & 0xF)) for i in range(12))


def _bounded_int(value: object, hi: int) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < hi else None


def sanitize_menu_node(raw: object) -> dict | None:
    """Nodo de MENU (`li.nivelN > span.spanNivelDescripcion`): jerarquia, visibilidad, relacion con el elemento accionable. Sin texto."""
    if not isinstance(raw, dict):
        return None
    level = _bounded_int(raw.get("level"), 10)
    if level is None or level not in MENU_LEVELS:
        return None
    action = raw.get("actionable") if isinstance(raw.get("actionable"), dict) else {}
    kind = action.get("kind") if action.get("kind") in {"script", "path", "onclick", "pointer", "none"} else "none"
    ancestors = []
    for token in (raw.get("ancestors") if isinstance(raw.get("ancestors"), list) else [])[:8]:
        tok = safe_token(token)
        if tok is not None:
            ancestors.append(tok)
    return {
        "li_id": safe_token(raw.get("li_id")),
        "level": level,
        "is_leaf": bool(raw.get("is_leaf")),
        "label": route_key(raw.get("text_probe")) if isinstance(raw.get("text_probe"), str) else None,
        "digest": safe_digest(raw.get("digest")),
        "visible": bool(raw.get("visible")),
        "chain_visible": bool(raw.get("chain_visible")),
        "ancestors": ancestors,
        "cuerpos": _bounded_int(raw.get("cuerpos"), 10),
        "dup_index": _bounded_int(raw.get("dup_index"), 50),
        "dup_count": _bounded_int(raw.get("dup_count"), 50),
        "container_id": safe_token(raw.get("container_id")),
        "actionable": {"tag": action.get("tag") if action.get("tag") in ALLOWED_TAGS else None, "id": safe_token(action.get("id")),
                       "kind": kind, "visible": bool(action.get("visible"))},
    }


def sanitize_view_v2(view: object) -> dict:
    base = sanitize_frame_view(view)
    if base.get("error") or not isinstance(view, dict):
        return {"error": True}
    menu = []
    for raw in list(view.get("menu_nodes") or [])[:MAX_MENU_NODES]:
        node = sanitize_menu_node(raw)
        if node is not None:
            menu.append(node)
    counts_raw = view.get("counts") if isinstance(view.get("counts"), dict) else {}
    counts = {k: (_bounded_int(counts_raw.get(k), 100000) or 0) for k in ("file_inputs", "forms", "tables_visible", "iframes", "password_controls_visible", "buttons_visible")}
    base["focused"] = bool(view.get("focused"))
    base["cards"] = [c for c in (sanitize_card(x) for x in list(view.get("cards") or [])[:12]) if c is not None]
    base["menu_nodes"] = menu
    base["menu_truncated"] = bool(view.get("menu_truncated"))
    base["counts"] = counts
    return base


def sanitize_card(raw: object) -> dict | None:
    """Tarjeta de la pagina PUBLICA: encabezados (clave canonica o huella), el control INGRESAR y la cadena de contenedores. Sin texto libre."""
    if not isinstance(raw, dict):
        return None
    headings = []
    for h in (raw.get("headings") if isinstance(raw.get("headings"), list) else [])[:3]:
        if isinstance(h, dict) and h.get("tag") in {"h1", "h2", "h3", "h4"}:
            headings.append({"tag": h["tag"], "label": route_key(h.get("text_probe")) if isinstance(h.get("text_probe"), str) else None,
                             "digest": safe_digest(h.get("digest")), "visible": bool(h.get("visible"))})
    ing = raw.get("ingresar") if isinstance(raw.get("ingresar"), dict) else {}
    chain = []
    for c in (raw.get("chain") if isinstance(raw.get("chain"), list) else [])[:5]:
        if isinstance(c, dict) and c.get("tag") in ALLOWED_TAGS:
            chain.append({"tag": c["tag"], "id": safe_token(c.get("id")), "classes": safe_classes(c.get("classes"))})
    return {
        "headings": headings, "index": _bounded_int(raw.get("index"), 100), "visible": bool(raw.get("visible")),
        "ingresar": {"tag": ing.get("tag") if ing.get("tag") in ALLOWED_TAGS else None, "id": safe_token(ing.get("id")), "classes": safe_classes(ing.get("classes")),
                     "visible": bool(ing.get("visible")), "parent_tag": ing.get("parent_tag") if ing.get("parent_tag") in ALLOWED_TAGS else None},
        "chain": chain,
    }


def sanitize_network(raw: object) -> dict:
    """Solo METODO + host permitido + ruta normalizada + tipo de recurso; nunca cuerpo, cabeceras ni query. Lo no-GET se detalla (maximo 30)."""
    out = {"total": 0, "by_method": {}, "non_get": []}
    if not isinstance(raw, list):
        return out
    seen = set()
    for item in raw[:5000]:
        if not isinstance(item, dict):
            continue
        method = item.get("method") if item.get("method") in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"} else "OTHER"
        out["total"] += 1
        out["by_method"][method] = out["by_method"].get(method, 0) + 1
        if method in {"POST", "PUT", "PATCH", "DELETE", "OTHER"}:
            url = safe_url(item.get("url"))
            rtype = item.get("resource_type") if item.get("resource_type") in {"document", "xhr", "fetch", "script", "stylesheet", "image", "font", "other"} else "other"
            key = (method, url["host"], url["path"], rtype)
            if key not in seen and len(out["non_get"]) < 30:
                seen.add(key)
                out["non_get"].append({"method": method, "host": url["host"], "path": url["path"], "resource_type": rtype})
    return out
