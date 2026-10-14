"""M3 v2 -- NUCLEO PURO del reconocedor de checkpoints (sin Playwright): modelo de evidencia, almacen incremental, evaluacion de PRENAVEGACION y de
LLEGADA, vinculo entre ambas, matriz de evidencias y candidatos de contrato. Nada aqui abre un navegador, hace clic ni habilita un destino.

CAUSA RAIZ del reconocimiento v1: solo registraba el estado de LLEGADA (tras el clic manual), cuando el menu ya se habia cerrado y sus hojas
estaban ocultas; ademas solo conservaba el texto de una lista blanca corta, de modo que las hojas de SIRE no tenian etiqueta. v2 captura el menu
EXPANDIDO antes del clic (PRE), la llegada despues (ARRIVAL) y las vincula como un mismo recorrido confirmado por el propietario."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import m2_recon_sanitizer as san

TOOL = "m3_sol_recon"
NOISE_FRAME_MARKERS = ("campanhaNotificacion", "gettime.pl")  # frames fijos del shell (campana con boton de Buzon, reloj): nunca son el destino
LOGIN_HOSTS = {"api-seguridad.sunat.gob.pe"}
MIN_DISCRIMINATING_MARKERS = 2


@dataclass(frozen=True)
class DestSpec:
    key: str
    title: str
    route: tuple[str, ...]  # claves canonicas de la ruta de menu, de la raiz a la hoja
    instructions_pre: str
    instructions_arrival: str
    arrival_optional_with_prior: bool = False  # la llegada previa (v1) ya es tecnicamente suficiente: basta el PRE
    effects_review: bool = False  # la apertura debe demostrarse inocua antes de automatizarse (Generacion de Registros)
    needs_platform_window: bool = False  # el PRE ocurre DENTRO de la plataforma (Declara Facil)
    allow_header_target: bool = False  # el destino es un nodo con hijos en el menu (sin hoja homonima): se acepta una cabecera visible
    entry_kind: str = "MENU_LEAF"  # MENU_LEAF (menu autenticado) | PUBLIC_CARD (tarjeta de la pagina PUBLICA de SUNAT, antes del login)
    public_label: str | None = None  # PUBLIC_CARD: clave canonica del encabezado de la tarjeta
    arrival_path_contains: str | None = None  # la ruta de la llegada DEBE contener esto (evita confundir destinos que comparten ids de plataforma)


SPECS: dict[str, DestSpec] = {s.key: s for s in (
    DestSpec("SIRE_RVIE", "SIRE · Registro de Ventas (RVIE)",
             ("SIRE_ROOT", "SIRE_RVIE_REGISTRO", "SIRE_RVIE_GESTION"),
             "Expanda el menú hasta ver «Gestión de Ventas e Ingresos Electrónicos» (Sistema Integrado de Registros Electrónicos → Registro de Ventas e Ingresos Electrónico). NO haga clic en la hoja.",
             "Ahora haga clic usted en «Gestión de Ventas e Ingresos Electrónicos» y espere a que cargue la pantalla. NO elija período ni opere sobre propuestas o registros.",
             arrival_path_contains="/libroventas/"),
    DestSpec("SIRE_RCE", "SIRE · Registro de Compras (RCE)",
             ("SIRE_ROOT", "SIRE_RCE_REGISTRO", "SIRE_RCE_GESTION"),
             "Expanda el menú hasta ver «Gestión de Compras» (Sistema Integrado de Registros Electrónicos → Registro de Compras Electrónico). NO haga clic en la hoja.",
             "Ahora haga clic usted en «Gestión de Compras» y espere a que cargue. NO elija período ni opere sobre propuestas o registros.",
             arrival_path_contains="/librocompras/"),
    DestSpec("SIRE_GENERACION_REGISTROS", "SIRE · Generación de Registros Electrónicos",
             ("SIRE_GENERACION_REGISTROS",),
             "Expanda el menú hasta ver «Generación de Registros Electrónicos». NO haga clic.",
             "Solo si usted sabe que esta opción únicamente MUESTRA la pantalla inicial (sin cargar, validar, generar, aceptar ni enviar), haga clic y espere. Si duda, omita esta llegada: el acceso quedará manual.",
             effects_review=True, arrival_path_contains="/generacionlibro/"),
    # La entrada REAL parte de la pagina PUBLICA de SUNAT (tarjeta «Mis Declaraciones y Pagos» -> INGRESAR), NO del Menu SOL autenticado.
    DestSpec("DECLARACIONES_Y_PAGOS", "Declaraciones y Pagos (tarjeta pública «Mis Declaraciones y Pagos»)",
             ("MIS_DECLARACIONES_Y_PAGOS",),
             "En la PRIMERA pestaña de SUNAT (la página PÚBLICA, antes o después de iniciar sesión) deje visible la tarjeta «Mis Declaraciones y Pagos» con su botón INGRESAR. NO haga clic.",
             "Si quiere vincular la llegada: haga clic usted en INGRESAR de esa tarjeta (autentíquese si SUNAT lo pide) y espere a que abra la plataforma. Si la llegada previa ya basta, puede omitir esto.",
             arrival_optional_with_prior=True, entry_kind="PUBLIC_CARD", public_label="MIS_DECLARACIONES_Y_PAGOS",
             arrival_path_contains="/cl-ti-itmenu2/MenuInternetPlataforma.htm"),
    # Declara Facil (incluido el FV 621) vive DENTRO de la plataforma: es navegacion interna bajo control humano, no un destino aparte.
    DestSpec("DECLARA_FACIL", "Declara Fácil (dentro de la plataforma de Declaraciones y Pagos)",
             ("DECLARA_FACIL",),
             "Ya dentro de la plataforma de Declaraciones y Pagos, deje visible la opción «Declara Fácil». NO haga clic.",
             "Haga clic usted en «Declara Fácil» y espere a que cargue el entorno donde está el FV 621. NO rellene ni presente nada.",
             needs_platform_window=True),
)}
ORDER = ("SIRE_RVIE", "SIRE_RCE", "SIRE_GENERACION_REGISTROS", "DECLARACIONES_Y_PAGOS")
RESOLVED_ELSEWHERE = {
    "TRAMITES_Y_CONSULTAS": "alias de MENU_SOL (ya resuelto con la evidencia de la sesión anterior; no se vuelve a capturar)",
    "DECLARA_FACIL": "contenido en la plataforma de Declaraciones y Pagos (incluye el FV 621): su navegación interna queda bajo control humano; no se captura aparte",
}
PUBLIC_HOSTS = {"www.sunat.gob.pe", "sunat.gob.pe"} | set(san.LOOPBACK_HOSTS)  # loopback: solo los portales sinteticos de prueba


# ---------------------------------------------------------------------------------------------------------------- identificadores
class Registry:
    """Asigna identificadores tecnicos NO sensibles (W1, F1) a ventanas y frames por identidad de objeto, estables durante toda la sesion."""

    def __init__(self) -> None:
        self._w: dict[int, str] = {}
        self._f: dict[int, str] = {}
        self._keep: list = []

    def window(self, page) -> str:
        key = id(page)
        if key not in self._w:
            self._w[key] = f"W{len(self._w) + 1}"
            self._keep.append(page)  # evita la reutilizacion de id() por el recolector
        return self._w[key]

    def frame(self, frame) -> str:
        key = id(frame)
        if key not in self._f:
            self._f[key] = f"F{len(self._f) + 1}"
            self._keep.append(frame)
        return self._f[key]


# ---------------------------------------------------------------------------------------------------------------------- almacen
class Store:
    """Archivo JSON incremental (escritura atomica). Los checkpoints NUNCA se sobrescriben: repetir uno marca el anterior SUPERSEDED."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.checkpoints: list[dict] = []
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.checkpoints = [c for c in data.get("checkpoints", []) if isinstance(c, dict)]

    def next_id(self) -> str:
        return f"C{len(self.checkpoints) + 1:03d}"

    def add(self, cp: dict) -> dict:
        if cp["kind"] in {"PRE", "ARRIVAL"}:
            for old in self.checkpoints:
                if old["destination"] == cp["destination"] and old["kind"] == cp["kind"] and old["status"] == "ACTIVE":
                    old["status"] = "SUPERSEDED"
                    old["superseded_by"] = cp["checkpoint_id"]
                    if cp["kind"] == "PRE":  # una llegada vinculada a un PRE reemplazado deja de ser valida como vinculo
                        for arr in self.checkpoints:
                            if arr["kind"] == "ARRIVAL" and arr.get("linked_pre") == old["checkpoint_id"] and arr["status"] == "ACTIVE":
                                arr["link_stale"] = True
        self.checkpoints.append(cp)
        self.save()
        return cp

    def active(self, destination: str, kind: str) -> dict | None:
        found = [c for c in self.checkpoints if c["destination"] == destination and c["kind"] == kind and c["status"] == "ACTIVE"]
        return found[-1] if found else None

    def pre_for(self, destination: str) -> dict | None:
        """PRE ACTIVO del destino. Si es una REUTILIZACION (`reuse_of`), devuelve la captura de origen con el id y el destino propios: una misma captura del
        menu expandido puede evidenciar varias hojas visibles a la vez, sin duplicar el archivo."""
        cp = self.active(destination, "PRE")
        if cp is None or not cp.get("reuse_of"):
            return cp
        source = next((c for c in self.checkpoints if c["checkpoint_id"] == cp["reuse_of"]), None)
        if source is None:
            return None
        return {**source, "checkpoint_id": cp["checkpoint_id"], "destination": destination, "observed": cp.get("observed"), "reuse_of": cp["reuse_of"]}

    def derive_shared_pre(self) -> list[str]:
        """Para cada destino cuyo PRE no es COMPLETO, busca entre los PRE existentes (propios o de otros destinos, incluso reemplazados) una captura donde su hoja
        SI sea visible y la registra como reutilizacion. Es evidencia REAL ya capturada (nunca se inventa); no promueve nada a contrato."""
        added = []
        for key in ORDER:
            spec = SPECS[key]
            current = self.pre_for(key)
            if current is not None and evaluate_pre(spec, current)["status"] == "COMPLETE":
                continue
            for src in reversed(self.checkpoints):
                if src["kind"] != "PRE" or src.get("reuse_of") or src["destination"] == key and src is self.active(key, "PRE"):
                    continue
                if evaluate_pre(spec, {**src, "destination": key})["status"] == "COMPLETE":
                    self.add({"record": "checkpoint", "checkpoint_id": self.next_id(), "destination": key, "kind": "PRE", "attempt": 1, "status": "ACTIVE",
                              "date": src.get("date"), "windows": [], "reuse_of": src["checkpoint_id"], "linked_pre": None, "same_journey_confirmed": None,
                              "observed": None})
                    added.append(key)
                    break
        return added

    def save(self) -> None:
        payload = {"tool": TOOL, "sanitizer_version": san.SANITIZER_VERSION_V2, "automated_clicks": 0, "checkpoints": self.checkpoints}
        text = san.dumps(payload) if isinstance(payload, list) else json.dumps(payload, ensure_ascii=False, indent=1)
        san.assert_clean(text)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


# ------------------------------------------------------------------------------------------------------------------ utilidades
def _is_noise(frame: dict) -> bool:
    return any(m in str((frame.get("url") or {}).get("path") or "") for m in NOISE_FRAME_MARKERS)


def iter_frames(cp: dict):
    for win in cp.get("windows", []):
        for fr in win.get("frames", []):
            yield win, fr


def _menu_nodes(frame: dict) -> list[dict]:
    return list((frame.get("view") or {}).get("menu_nodes") or [])


def host_is_portal(host: str | None) -> bool:
    return bool(host) and (host.endswith(".sunat.gob.pe") or host == "sunat.gob.pe" or host in san.LOOPBACK_HOSTS)


# --------------------------------------------------------------------------------------------------------------------- PRE
def _evaluate_pre_public(spec: DestSpec, cp: dict) -> dict:
    """PRE de una entrada PUBLICA: la tarjeta con el encabezado esperado y su control INGRESAR visibles en la pagina publica de SUNAT (antes del login)."""
    observed = cp.get("observed") or {}
    cards = []
    for win, fr in iter_frames(cp):
        if _is_noise(fr) or (fr.get("url") or {}).get("host") not in PUBLIC_HOSTS:
            continue
        if observed and (win["window_id"] != observed.get("window") or fr["frame_id"] != observed.get("frame")):
            continue
        for card in (fr.get("view") or {}).get("cards") or []:
            cards.append((win, fr, card))
    if not cards:
        return {"status": "MISSING", "reasons": ["no hay tarjetas de la página PÚBLICA de SUNAT en el checkpoint (¿está abierta la primera pestaña de sol.html?)"], "entry": None}
    choice = cp.get("card_choice")
    if choice is not None:  # el propietario indico la POSICION de la tarjeta (K <n>) porque el texto del encabezado no coincidio con la lista blanca
        matches = [(w, f, c) for (w, f, c) in cards if c["index"] == choice]
    else:
        matches = [(w, f, c) for (w, f, c) in cards if any(h.get("label") == spec.public_label for h in c["headings"])]
    if not matches:
        if choice is not None:
            return {"status": "MISSING", "reasons": [f"no hay una tarjeta en la posición {choice} (hay {len(cards)}: 0..{len(cards) - 1})"], "entry": None}
        return {"status": "MISSING", "reasons": [f"entre {len(cards)} tarjetas (posiciones 0..{len(cards) - 1}, de arriba abajo) ninguna tiene el encabezado «{spec.public_label}»: "
                                                  "si la ve en pantalla, indique su posición con «K <n>» y escriba P otra vez"], "entry": None}
    visible = [(w, f, c) for (w, f, c) in matches if c["visible"] and c["ingresar"]["visible"]]
    if not visible:
        return {"status": "PARTIAL", "reasons": ["la tarjeta existe pero su INGRESAR no está visible (¿hay que desplazar o abrir la sección?)"], "entry": None}
    if len(visible) > 1:
        return {"status": "PARTIAL", "reasons": [f"{len(visible)} tarjetas visibles con ese encabezado: ambiguo; fije el frame con V"], "entry": None}
    win, fr, card = visible[0]
    heading = next((h for h in card["headings"] if h.get("label") == spec.public_label), card["headings"][0] if card["headings"] else {"tag": None, "digest": None})
    entry = {"kind": "PUBLIC_CARD", "label": spec.public_label, "heading_tag": heading["tag"], "heading_digest": heading.get("digest"), "card_index": card["index"],
             "ingresar_tag": card["ingresar"]["tag"], "ingresar_classes": card["ingresar"]["classes"], "card_chain": card["chain"],
             "host": fr["url"].get("host"), "path": fr["url"].get("path"), "window": win["window_id"], "frame": fr["frame_id"],
             "click_target": "ingresar-control", "chosen_by_owner_position": choice is not None,
             "notes": ["el clic ocurre ANTES del login: el motor del agente aún no soporta entradas públicas (contrato deshabilitado hasta implementarlo y validarlo)"]
                      + (["tarjeta elegida por el propietario por su posición; su identidad queda en la huella del encabezado"] if choice is not None else []),
             "header_levels": 0}
    return {"status": "COMPLETE", "reasons": [], "entry": entry}


def evaluate_pre(spec: DestSpec, cp: dict | None) -> dict:
    """PRENAVEGACION: la hoja del destino debe verse en el menu EXPANDIDO, con ruta de ancestros coherente y un unico nodo accionable resuelto
    por jerarquia + visibilidad (no por coincidencia textual). Devuelve {status, reasons, entry}."""
    if cp is None:
        return {"status": "MISSING", "reasons": ["no hay checkpoint PRE"], "entry": None}
    if spec.entry_kind == "PUBLIC_CARD":
        return _evaluate_pre_public(spec, cp)
    leaf_key = spec.route[-1]
    reasons: list[str] = []
    notes: list[str] = []
    instances = []
    observed = cp.get("observed") or {}  # el propietario puede fijar ventana/frame con V (identificadores tecnicos W#/F#)
    for win, fr in iter_frames(cp):
        if _is_noise(fr):
            continue
        if observed and (win["window_id"] != observed.get("window") or fr["frame_id"] != observed.get("frame")):
            continue
        nodes = _menu_nodes(fr)
        by_li = {n["li_id"]: n for n in nodes if n.get("li_id") and n.get("label")}
        for n in nodes:
            if n.get("label") == leaf_key:
                instances.append((win, fr, n, by_li))
    if not instances:
        return {"status": "MISSING", "reasons": [f"la hoja «{leaf_key}» no aparece en ningún frame (¿menú sin expandir o etiqueta distinta?)"], "entry": None}
    # Una etiqueta puede repetirse en una CABECERA y en su HOJA (p. ej. Generacion de Registros): el destino accionable es la hoja.
    leaves = [i for i in instances if i[2].get("is_leaf")]
    pool = leaves or (instances if spec.allow_header_target else [])
    if instances and not pool:
        return {"status": "PARTIAL", "reasons": [f"«{leaf_key}» solo aparece como cabecera con hijos, no como hoja: expanda hasta ver la hoja"], "entry": None}
    if leaves and len(leaves) < len(instances):
        notes.append("la etiqueta también aparece en una cabecera; se usa la hoja")
    instances = pool
    leaf_lis = {n["li_id"] for _w, _f, n, _b in instances}
    visible = [(w, f, n, b) for (w, f, n, b) in instances if n["visible"] and n["chain_visible"]]
    where = sorted({f"{w['window_id']}/{f['frame_id']}" for (w, f, _n, _b) in visible})
    if len(where) > 1:
        return {"status": "PARTIAL", "reasons": [f"la hoja visible aparece en varias ventanas/frames ({', '.join(where)}): elija con V <ventana> <frame>"], "entry": None}
    if not visible:
        return {"status": "PARTIAL", "reasons": ["la hoja existe pero está OCULTA: el menú no estaba expandido al capturar (repita el PRE con la hoja visible)"], "entry": None}
    if len({n["li_id"] for _w, _f, n, _b in visible}) > 1:
        reasons.append("hay hojas visibles en li distintos con la misma etiqueta: jerarquía ambigua")
    chosen = visible[0]
    win, fr, node, by_li = chosen
    if len(visible) > 1 and len({n["li_id"] for _w, _f, n, _b in visible}) == 1:
        reasons.append("dos nodos VISIBLES dentro del mismo li: no se sabe cuál es accionable")
    # ruta de ancestros: cada clave previa de la ruta debe estar entre los encabezados que contienen la hoja
    chain = [by_li[a]["label"] for a in node.get("ancestors", []) if a in by_li]
    cursor = 0
    for key in spec.route[:-1]:
        try:
            cursor = chain.index(key, cursor) + 1
        except ValueError:
            reasons.append(f"la ruta no contiene el encabezado «{key}» sobre la hoja (ancestros reconocidos: {','.join(chain) or 'ninguno'})")
            break
    action = node["actionable"]
    # El menu real enlaza el clic por script (sin <a>/onclick): la ausencia de atributos NO impide el PRE. La hoja visible, unica por jerarquia, es el
    # objetivo; que es pulsable lo confirma el clic del propietario en la LLEGADA vinculada (y, en un contrato, la identidad de llegada).
    click_target = {"script": "anchor", "path": "anchor", "onclick": "onclick-element", "pointer": "pointer-element"}.get(action["kind"], "label-span")
    if click_target == "label-span":
        notes.append("sin <a>, onclick ni cursor:pointer visibles; el objetivo es el rótulo visible y su clic lo confirmará la llegada vinculada")
    entry = {
        "kind": "MENU_LEAF", "label": leaf_key, "leaf_level": node["level"], "header_levels": node.get("cuerpos"),
        "ancestor_labels": chain, "ancestor_li_ids": node.get("ancestors", []), "leaf_li_id": node["li_id"],
        "duplicate_nodes_in_li": node.get("dup_count"), "visible_instances": len(visible), "distinct_leaf_lis": len(leaf_lis),
        "actionable_kind": action["kind"], "click_target": click_target, "notes": notes, "window": win["window_id"], "frame": fr["frame_id"],
        "resolved_by": "hierarchy+visibility" if (node.get("dup_count") or 1) > 1 or len(instances) > 1 else "unique",
    }
    return {"status": "COMPLETE" if not reasons else "PARTIAL", "reasons": reasons, "entry": entry}


# ----------------------------------------------------------------------------------------------------------------------- LLEGADA
def _frame_key(fr: dict) -> tuple:
    u = fr.get("url") or {}
    return (u.get("host"), u.get("path"))


def _unique_ids(fr: dict) -> set[str]:
    nodes = (fr.get("view") or {}).get("nodes") or []
    counts: dict[str, int] = {}
    for n in nodes:
        ident = n.get("id")
        if ident and ident != san.REDACTED:
            counts[ident] = counts.get(ident, 0) + 1
    return {i for i, c in counts.items() if c == 1}


def observed_target(pre: dict, arr: dict) -> tuple[dict | None, dict | None, str, list[str]]:
    """(ventana, frame, tipo, motivos). Seleccion explicita del propietario (`observed`) o automatica si hay UNA candidata nueva."""
    reasons: list[str] = []
    pre_windows = {w["window_id"] for w in pre.get("windows", [])}
    pre_frames = {(fr["frame_id"], _frame_key(fr)) for _w, fr in iter_frames(pre)}
    pre_frame_keys = {k for _id, k in pre_frames}
    chosen = arr.get("observed") or {}
    candidates = []
    for win, fr in iter_frames(arr):
        if _is_noise(fr) or not host_is_portal((fr.get("url") or {}).get("host")):
            continue
        is_new_window = win["window_id"] not in pre_windows
        if is_new_window and fr["is_main"]:
            candidates.append((win, fr, "NEW_PAGE"))
        elif not is_new_window and not fr["is_main"] and _frame_key(fr) not in pre_frame_keys:
            candidates.append((win, fr, "FRAME"))
        elif not is_new_window and fr["is_main"] and _frame_key(fr) not in pre_frame_keys:
            candidates.append((win, fr, "SAME_WINDOW_NAVIGATION"))
    if chosen:
        for win, fr, kind in candidates:
            if win["window_id"] == chosen.get("window") and fr["frame_id"] == chosen.get("frame"):
                return win, fr, kind, reasons
        for win, fr in iter_frames(arr):  # seleccion explicita sobre algo que no es 'nuevo': se respeta pero se avisa
            if win["window_id"] == chosen.get("window") and fr["frame_id"] == chosen.get("frame"):
                return win, fr, "SELECTED_NOT_NEW", ["el frame elegido ya existía en el PRE: la llegada podría no haber cambiado"]
        return None, None, "NONE", ["la ventana/frame elegido no existe en el checkpoint"]
    if len(candidates) == 1:
        return candidates[0][0], candidates[0][1], candidates[0][2], reasons
    if not candidates:
        return None, None, "NONE", ["no apareció ningún frame ni ventana nuevos respecto al PRE (¿la pantalla ya había cargado antes del PRE o aún no cargó?)"]
    return None, None, "AMBIGUOUS", [f"hay {len(candidates)} candidatas ({', '.join(w['window_id'] + '/' + f['frame_id'] for w, f, _k in candidates)}): elija con V"]


def evaluate_arrival(spec: DestSpec, pre: dict | None, arr: dict | None) -> dict:
    if arr is None:
        return {"status": "MISSING", "reasons": ["no hay checkpoint de LLEGADA"], "arrival": None, "effects": None}
    reasons: list[str] = []
    if pre is None:
        return {"status": "PARTIAL", "reasons": ["la llegada no tiene PRE activo al que vincularse"], "arrival": None, "effects": None}
    if arr.get("linked_pre") != pre["checkpoint_id"] or arr.get("link_stale"):
        reasons.append("la llegada no está vinculada al PRE activo (se repitió el PRE: repita la llegada)")
    if arr.get("same_journey_confirmed") is not True:
        reasons.append("el propietario no confirmó que entrada y llegada son el mismo recorrido")
    win, fr, kind, why = observed_target(pre, arr)
    reasons += why
    arrival = None
    if fr is not None:
        view = fr.get("view") or {}
        if view.get("error"):
            reasons.append("el frame observado no se pudo leer")
        counts = view.get("counts") or {}
        if counts.get("password_controls_visible"):
            reasons.append("la llegada muestra un control de contraseña: parece login, no el destino")
        if (fr.get("url") or {}).get("host") in LOGIN_HOSTS:
            reasons.append("la llegada está en el host de autenticación")
        u = fr.get("url") or {}
        # EVIDENCIA POSITIVA de la llegada (todas por separado y registradas):
        #  (1) RUTA NUEVA: el host+ruta del frame no existia en ningun frame del PRE (distingue destinos que comparten la plataforma SIRE);
        #  (2) ids unicos NUEVOS PARA ESA RUTA: se descuentan solo los ids de frames del PRE con la MISMA ruta (un frame de OTRA aplicacion que sigue cargado en el
        #      iframe, p. ej. RVIE al abrir RCE, comparte ids de plataforma pero NO prueba nada sobre esta ruta);
        #  (3) estructura visible (tablas, botones, formularios) y (4) rotulos canonicos visibles del propio frame (titulo de la aplicacion).
        pre_keys = {_frame_key(pfr) for _w, pfr in iter_frames(pre)}
        path_new = _frame_key(fr) not in pre_keys
        same_path_ids: set[str] = set()
        other_ids: set[str] = set()
        for _w, pfr in iter_frames(pre):
            (same_path_ids if _frame_key(pfr) == _frame_key(fr) else other_ids).update(_unique_ids(pfr))
        markers = sorted(_unique_ids(fr) - same_path_ids)
        structure = [name for name, ok in (("tables", counts.get("tables_visible", 0) >= 1), ("buttons", counts.get("buttons_visible", 0) >= 1), ("forms", counts.get("forms", 0) >= 1)) if ok]
        labels = sorted({n["label"] for n in (view.get("nodes") or []) if n.get("label") and n.get("visible")})
        units = len(markers) + len(structure) + len(labels)
        if kind != "SELECTED_NOT_NEW" and not path_new:
            reasons.append("la ruta de la llegada ya existía en el PRE: no se demuestra que la pantalla cambió")
        if units < MIN_DISCRIMINATING_MARKERS:
            reasons.append(f"solo {units} marcador(es) estructural(es) nuevo(s) (ids {len(markers)}, estructura {len(structure)}, rótulos {len(labels)}); se requieren {MIN_DISCRIMINATING_MARKERS}")
        if spec.arrival_path_contains and spec.arrival_path_contains not in str(u.get("path") or ""):
            reasons.append(f"la llegada no corresponde al destino esperado (su ruta no contiene «{spec.arrival_path_contains}»)")
        platform_leaves = sorted({n["label"] for n in (view.get("menu_nodes") or []) if n.get("label") and n.get("visible") and n.get("chain_visible")})
        arrival = {"kind": kind, "host": u.get("host"), "path_pattern": u.get("path"), "discriminating_ids": markers[:8],
                   "positive_markers": {"path_new": path_new, "ids": markers[:8], "structure": structure, "labels": labels, "units": units,
                                        "shared_framework_ids": bool(set(markers) & other_ids)},
                   "visible_tables": counts.get("tables_visible", 0), "window": win["window_id"], "frame": fr["frame_id"],
                   "opener": win.get("opener"), "active_window": win.get("active"), "visible_menu_leaves": platform_leaves}
    net = arr.get("network") or {"total": 0, "by_method": {}, "non_get": []}
    counts = ((fr or {}).get("view") or {}).get("counts") or {}
    effects = {"non_get_requests": len(net.get("non_get", [])), "non_get": net.get("non_get", []), "file_inputs": counts.get("file_inputs", 0),
               "side_effect_risk": "NONE_OBSERVED" if not net.get("non_get") and not counts.get("file_inputs") else "REVIEW"}
    return {"status": "COMPLETE" if not reasons else "PARTIAL", "reasons": reasons, "arrival": arrival, "effects": effects}


def _post_key(item: dict) -> tuple:
    return (item.get("method"), item.get("host"), item.get("path"), item.get("resource_type"))


def unexplained_non_get(non_get: list[dict], pre: dict | None, baseline: set[tuple]) -> list[dict]:
    """Peticiones no-GET que NO se explican por (a) el trafico de fondo de la plataforma SIRE/Menu presente en OTRAS llegadas de solo lectura (linea base)
    ni (b) una aplicacion que YA estaba cargada en un frame del PRE (su ruta coincide con la del POST). Lo que queda requiere revision humana."""
    pre_paths = {str((fr.get("url") or {}).get("path") or "").rstrip("/") for _w, fr in iter_frames(pre or {"windows": []})}
    pre_roots = {_app_root(p) for p in pre_paths if _app_root(p)}
    out = []
    for item in non_get:
        path = str(item.get("path") or "").rstrip("/")
        # (b) misma aplicacion que un frame YA cargado en el PRE (ruta exacta o raiz de la aplicacion: /app/<..>/servicio/<app>/mige)
        if _post_key(item) in baseline or path in pre_paths or (_app_root(path) and _app_root(path) in pre_roots):
            continue
        out.append(item)
    return out


def _app_root(path: str) -> str | None:
    parts = [p for p in path.split("/") if p]
    return "/" + "/".join(parts[:5]) if len(parts) >= 5 and parts[0] == "app" else None


def generation_decision(spec: DestSpec, arrival_eval: dict, arr: dict | None, *, pre: dict | None = None, baseline: set[tuple] | None = None) -> str | None:
    """Solo SIRE_GENERACION_REGISTROS: la apertura se automatiza unicamente si se demostro inocua; en cualquier otro caso queda MANUAL.
    Los POST que SUNAT emite al abrir CUALQUIER pantalla SIRE (token, menu) y los de aplicaciones ya cargadas no cuentan; cualquier otro POST o un `input file`
    deja el acceso manual. Aun asi el resultado es solo un CANDIDATO que exige revision y aceptacion humana: nunca habilita nada."""
    if not spec.effects_review:
        return None
    eff = arrival_eval.get("effects")
    if arrival_eval["status"] != "COMPLETE" or eff is None or eff.get("file_inputs") or not (arr or {}).get("owner_attests_view_only"):
        return "MANUAL_ONLY"
    if unexplained_non_get(eff.get("non_get", []), pre, baseline or set()):
        return "MANUAL_ONLY"
    return "OPEN_CANDIDATE_REQUIRES_REVIEW"


# ---------------------------------------------------------------------------------------------------------------------- matriz
PLATFORM_PATH = "/cl-ti-itmenu2/MenuInternetPlataforma.htm"
PLATFORM_REQUIRED_IDS = ("btnSalir", "iframeApplication", "ulSubmenu", "aOpcionPlataforma", "divBreadcrumb")


def verify_prior_platform(snapshots: list[dict]) -> dict:
    """Integridad de la llegada REAL a la plataforma (reconocimiento v1): se acepta solo si en CADA captura que la contiene hay exactamente UNA Page de la plataforma
    (host+ruta esperados), con los ids estructurales UNA vez, sin controles de login y con la hoja «Declara Fácil» visible; y todas las capturas coinciden.
    La asociacion con el clic de entrada NO existe en v1 (no registraba `opener`): se declara `UNLINKED_V1`, no se inventa."""
    seen = []
    for snap in snapshots:
        if not isinstance(snap, dict):
            continue
        pages = [p for p in (snap.get("pages") or []) if (p.get("url") or {}).get("path") == PLATFORM_PATH]
        if not pages:
            continue
        problems = []
        if len(pages) != 1:
            problems.append(f"{len(pages)} Pages de la plataforma en la captura «{snap.get('label')}»")
        page = pages[0]
        main = next((f for f in page.get("frames", []) if f.get("is_main")), None)
        nodes = list(((main or {}).get("view") or {}).get("nodes") or [])
        counts: dict[str, int] = {}
        for n in nodes:
            if n.get("id") and n["id"] != san.REDACTED:
                counts[n["id"]] = counts.get(n["id"], 0) + 1
        missing = [i for i in PLATFORM_REQUIRED_IDS if counts.get(i) != 1]
        if missing:
            problems.append("ids no unicos o ausentes: " + ", ".join(missing))
        if any(n.get("input_type") == "password" and n.get("visible") for n in nodes):
            problems.append("hay un control de contraseña visible (parece login)")
        leaves = [n for n in nodes if n.get("label") == "DECLARA_FACIL" and n.get("tag") == "span" and n.get("visible")]
        if len(leaves) != 1:
            problems.append(f"la hoja «Declara Fácil» visible aparece {len(leaves)} veces (se esperaba 1)")
        seen.append({"capture": snap.get("label"), "problems": problems, "ids_ok": not missing, "declara_facil_visible": len(leaves) == 1})
    ok = bool(seen) and all(not s["problems"] for s in seen)
    return {"ok": ok, "captures": seen, "independent_captures": len(seen), "window_association": "UNLINKED_V1",
            "note": "Declara Fácil (y el FV 621) se alcanza DENTRO de esta plataforma; el FV 621 en sí no fue observado, solo la hoja de entrada." if ok else "integridad insuficiente"}


def prior_arrivals_from_v1(snapshots: list[dict]) -> dict[str, dict]:
    """Llegadas ya observadas por el reconocimiento v1 (sin vinculo a PRE), por destino. Solo aplica a lo que la evidencia previa muestra de verdad."""
    out: dict[str, dict] = {}
    for snap in snapshots:
        if not isinstance(snap, dict):
            continue
        for page in snap.get("pages") or []:
            u = page.get("url") or {}
            if u.get("path") == PLATFORM_PATH:
                integrity = verify_prior_platform(snapshots)
                if integrity["ok"]:  # solo se reutiliza si la integridad se comprueba
                    out["DECLARACIONES_Y_PAGOS"] = {"kind": "NEW_PAGE", "host": u.get("host"), "path_pattern": u.get("path"), "source": "v1", "integrity": integrity}
            for fr in page.get("frames") or []:
                fu = fr.get("url") or {}
                path = str(fu.get("path") or "")
                if fr.get("is_main") or _is_noise(fr):
                    continue
                if "/libroventas/" in path:
                    out["SIRE_RVIE"] = {"kind": "FRAME", "host": fu.get("host"), "path_pattern": path, "source": "v1"}
                if "/librocompras/" in path:
                    out["SIRE_RCE"] = {"kind": "FRAME", "host": fu.get("host"), "path_pattern": path, "source": "v1"}
    return out


def _with_merged_network(store: "Store", key: str, pre_cp: dict | None, arr_cp: dict | None) -> dict | None:
    """Los intentos de LLEGADA de un mismo recorrido (mismo PRE) se juntan para los EFECTOS: un reintento no puede 'borrar' el trafico visto antes."""
    if arr_cp is None or pre_cp is None:
        return arr_cp
    merged: dict[tuple, dict] = {}
    total = 0
    for c in store.checkpoints:
        if c["destination"] == key and c["kind"] == "ARRIVAL" and c.get("linked_pre") == pre_cp["checkpoint_id"]:
            net = c.get("network") or {}
            total += net.get("total", 0)
            for item in net.get("non_get", []):
                merged[_post_key(item)] = item
    return {**arr_cp, "network": {"total": total, "by_method": (arr_cp.get("network") or {}).get("by_method", {}), "non_get": list(merged.values())}}


def matrix(store: Store, prior: dict[str, dict] | None = None) -> dict:
    prior = prior or {}
    rows = {}
    # Linea base de trafico no-GET: lo que SUNAT emite al abrir las OTRAS pantallas SIRE ya reconocidas (token, menu); sirve para decidir si una apertura
    # emite algo DIFERENTE.
    base_sets = []
    for other in ORDER:
        o_arr = _with_merged_network(store, other, store.pre_for(other), store.active(other, "ARRIVAL"))
        if o_arr:
            base_sets.append((other, {_post_key(i) for i in (o_arr.get("network") or {}).get("non_get", [])}))
    for key in ORDER:
        spec = SPECS[key]
        pre_cp, arr_cp = store.pre_for(key), store.active(key, "ARRIVAL")
        pre_e = evaluate_pre(spec, pre_cp)
        arr_eval_cp = _with_merged_network(store, key, pre_cp, arr_cp)
        arr_e = evaluate_arrival(spec, pre_cp, arr_eval_cp)
        baseline = set().union(*[s for name, s in base_sets if name != key]) if len(base_sets) > 1 else set()
        arrival_state = arr_e["status"]
        if arrival_state == "MISSING" and key in prior:
            arrival_state = "PRIOR_ONLY"
        link = "CONFIRMED" if (arr_cp and arr_cp.get("same_journey_confirmed") is True and not arr_cp.get("link_stale")) else ("UNCONFIRMED" if arr_cp else "NOT_CAPTURED")
        complete = pre_e["status"] == "COMPLETE" and (arr_e["status"] == "COMPLETE" or (spec.arrival_optional_with_prior and arrival_state == "PRIOR_ONLY"))
        todo = []
        if pre_e["status"] != "COMPLETE":
            todo.append("PRE: " + ("; ".join(pre_e["reasons"]) or "capturar"))
        if not (arr_e["status"] == "COMPLETE" or (spec.arrival_optional_with_prior and arrival_state == "PRIOR_ONLY")):
            todo.append("LLEGADA: " + ("; ".join(arr_e["reasons"]) or "capturar"))
        rows[key] = {
            "pre": pre_e["status"], "arrival": arrival_state, "link": link,
            "overall": "COMPLETE" if complete else ("PARTIAL" if pre_cp or arr_cp or key in prior else "MISSING"),
            "todo": todo, "entry": pre_e["entry"], "arrival_evidence": arr_e["arrival"] or prior.get(key), "effects": arr_e["effects"],
            "automation_decision": generation_decision(spec, arr_e, arr_cp, pre=pre_cp, baseline=baseline),
            "entry_kind": spec.entry_kind, "checkpoints": {"pre": (pre_cp or {}).get("checkpoint_id"), "arrival": (arr_cp or {}).get("checkpoint_id")},
        }
        if arr_e["effects"] is not None:
            arr_e["effects"]["unexplained_non_get"] = unexplained_non_get(arr_e["effects"].get("non_get", []), pre_cp, baseline)
    paths = {k: (v["arrival_evidence"] or {}).get("path_pattern") for k, v in rows.items() if v["arrival_evidence"]}
    if paths.get("SIRE_RVIE") and paths.get("SIRE_RVIE") == paths.get("SIRE_RCE"):
        for k in ("SIRE_RVIE", "SIRE_RCE"):
            rows[k]["overall"] = "PARTIAL"
            rows[k]["todo"].append("RVIE y RCE tienen la MISMA ruta de llegada: no se distinguen; no se reutiliza un contrato sin evidencia propia")
    return {"destinations": rows, "resolved_elsewhere": RESOLVED_ELSEWHERE}


def candidate_contracts(mx: dict) -> dict:
    """Candidatos de contrato SOLO para destinos con evidencia completa. NUNCA habilita: la habilitacion es una decision humana posterior."""
    out = {}
    for key, row in mx["destinations"].items():
        if row["overall"] != "COMPLETE" or row["entry"] is None:
            continue
        entry, arrival = row["entry"], row["arrival_evidence"] or {}
        levels = entry.get("header_levels")
        if entry["kind"] == "PUBLIC_CARD":
            entry_out = {"kind": "PUBLIC_CARD", "label_key": entry["label"], "heading_digest": entry.get("heading_digest"), "card_index": entry["card_index"],
                         "ingresar_tag": entry["ingresar_tag"], "agent_support": "IMPLEMENTED"}
        else:
            entry_out = {"kind": "MENU_LEAF", "label_key": entry["label"], "leaf_level": entry["leaf_level"], "header_levels": levels,
                         "ancestor_labels": entry["ancestor_labels"], "selection": entry["resolved_by"]}
        out[key] = {
            "enable": False, "decision": row["automation_decision"] or "REVIEW_REQUIRED",
            "entry": entry_out,
            "arrival": {k: arrival.get(k) for k in ("kind", "host", "path_pattern", "discriminating_ids", "positive_markers", "visible_menu_leaves", "integrity", "source") if k in arrival},
            "derived_max_clicks": (levels + 1) if isinstance(levels, int) and entry["kind"] == "MENU_LEAF" else None,
            "checkpoints": row.get("checkpoints"),
        }
    return out
