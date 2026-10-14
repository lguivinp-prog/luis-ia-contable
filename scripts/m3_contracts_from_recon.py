"""M3 -- convierte el JSON SANEADO del reconocimiento supervisado (`m2_sol_recon.py`) en CANDIDATOS de contrato por destino. SOLO LECTURA y
OFFLINE: no abre navegador, no toca SUNAT ni la red; lee un archivo que el propietario generó FUERA del repositorio.

Uso:
    .\\.venv-win-backend\\Scripts\\python.exe scripts\\m3_contracts_from_recon.py "<ruta>\\m2-recon-out\\<archivo>.json"

La salida es un informe estructural: por destino, la evidencia observada (entrada de menu o enlace de inicio, profundidad, llegada en frame o en
nueva Page, host + patron de ruta, marcadores unicos, tablas visibles, miga de pan) y los MOTIVOS por los que la evidencia aun no basta.
NUNCA habilita nada: un humano revisa el informe y, si es suficiente, escribe el contrato en `windows_agent/destination_contract.py` y
añade pruebas de identidad positivas y negativas. Una sola observacion NO prueba la ausencia de otros caminos: el informe lo advierte."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DESTINATIONS = ("SIRE", "DECLARACIONES_Y_PAGOS", "DECLARA_FACIL", "TRAMITES_Y_CONSULTAS")
# Destinos SIRE por aplicacion: el reconocimiento los observa como FRAMES de aplicacion (su etiqueta de menu no estaba en la lista blanca de M2).
SIRE_APPS = {"SIRE_RVIE": "/libroventas/", "SIRE_RCE": "/librocompras/", "SIRE_GENERACION_REGISTROS": None}
SHELL_HOST = "e-menu.sunat.gob.pe"
SHELL_PATH = "/cl-ti-itmenu/MenuInternet.htm"
# Frames que el shell lleva SIEMPRE y que no son la aplicacion destino (campana de avisos con boton de Buzon, reloj del servidor): se ignoran.
NOISE_FRAME_MARKERS = ("campanhaNotificacion", "gettime.pl")
ENTRY_HOSTS = {"www.sunat.gob.pe", "sunat.gob.pe"}
AUTH_HOSTS = {"api-seguridad.sunat.gob.pe"}
MAX_UNIQUE_IDS = 6


def _nodes(frame: dict) -> list[dict]:
    return list((frame.get("view") or {}).get("nodes") or [])


def _level_of(classes: list[str]) -> int | None:
    for token in classes:
        if token.startswith("nivel") and token[5:].isdigit():
            return int(token[5:])
    return None


def _find_entry(shell_nodes: list[dict], label: str) -> tuple[dict | None, str | None]:
    """(evidencia de entrada, motivo de insuficiencia). Prefiere la hoja de menu (span de nivelN dentro de li); acepta un enlace de inicio."""
    leaves = [n for n in shell_nodes if n.get("label") == label and n.get("tag") == "span" and n.get("parent_tag") == "li"]
    anchors = [n for n in shell_nodes if n.get("label") == label and n.get("tag") == "a"]
    if len(leaves) == 1:
        leaf = leaves[0]
        cuerpos = leaf.get("ancestor_cuerpos")
        match = re.match(r"nivel(\d+)(?:_|$)", str(leaf.get("parent_id") or ""))
        level = int(match.group(1)) if match else None  # el id del li padre es `nivelN_...`
        if cuerpos is None:
            return None, "profundidad del menu (ancestor_cuerpos) no registrada: ejecute el reconocimiento actualizado de M3"
        if level is None:
            return None, "nivel de la hoja de menu no determinable (id del li padre sin prefijo nivelN)"
        return {"kind": "MENU_LEAF", "label": label, "leaf_level": level, "levels": cuerpos, "visible_at_capture": bool(leaf.get("visible"))}, None
    if len(leaves) > 1:
        same_li = len({n.get("parent_id") for n in leaves}) == 1
        extra = " dentro del MISMO li (nodo duplicado, p. ej. version escritorio/movil): no se sabe cual es accionable; falta una captura con el menu expandido" if same_li else ""
        return None, "etiqueta de menu AMBIGUA (mas de una hoja con la misma etiqueta)" + extra
    if len(anchors) == 1:
        return {"kind": "HOME_LINK", "label": label, "href_path": anchors[0].get("href_path"), "visible_at_capture": bool(anchors[0].get("visible"))}, None
    if len(anchors) > 1:
        return None, "enlace de inicio AMBIGUO (mas de un enlace con la misma etiqueta)"
    return None, "no se observo la entrada en el menu de esta cuenta (sin permiso, otra ruta o el portal cambio): no se infiere"


def _arrival(pages: list[dict]) -> tuple[dict | None, str | None]:
    # Nueva Page de nivel superior en un host que no es entrada, login ni shell
    others = []
    for page in pages:
        url = page.get("url") or {}
        host = url.get("host")
        is_shell = host == SHELL_HOST and url.get("path") == SHELL_PATH
        if host and host not in ENTRY_HOSTS | AUTH_HOSTS and not is_shell:
            others.append(page)
    if len(others) == 1:
        page = others[0]
        main = next((f for f in page.get("frames", []) if f.get("is_main")), {})
        return {"kind": "NEW_PAGE", "host": page["url"].get("host"), "path_pattern": page["url"].get("path"), "once_ids": _once_ids(_nodes(main)), "visible_tables": _tables(_nodes(main))}, None
    if len(others) > 1:
        return None, "varias Pages nuevas simultaneas: el contrato exige exactamente una"
    # Frame de aplicacion dentro del shell
    for page in pages:
        if (page.get("url") or {}).get("host") != SHELL_HOST:
            continue
        frames = [f for f in page.get("frames", []) if not f.get("is_main") and (f.get("url") or {}).get("host") and not _is_noise(f)]
        if len(frames) == 1:
            frame = frames[0]
            return {"kind": "FRAME", "host": frame["url"].get("host"), "path_pattern": frame["url"].get("path"), "once_ids": _once_ids(_nodes(frame)),
                    "visible_tables": _tables(_nodes(frame))}, None
        if len(frames) > 1:
            return None, "varios frames de aplicacion: el contrato exige exactamente uno que cumpla la identidad"
    return None, "no se observo ni una nueva Page ni un frame de aplicacion distinto del shell"


def _is_noise(frame: dict) -> bool:
    return any(marker in str((frame.get("url") or {}).get("path") or "") for marker in NOISE_FRAME_MARKERS)


def _app_frames(snapshots: list[dict]) -> list[dict]:
    """Todos los frames de aplicacion (no ruido) observados en cualquier captura, sin repetir host+ruta."""
    seen, out = set(), []
    for snap in snapshots:
        for page in (snap.get("pages") or []) if isinstance(snap, dict) else []:
            if (page.get("url") or {}).get("host") != SHELL_HOST:
                continue
            for frame in page.get("frames", []):
                url = frame.get("url") or {}
                if frame.get("is_main") or not url.get("host") or _is_noise(frame):
                    continue
                key = (url.get("host"), url.get("path"))
                if key not in seen:
                    seen.add(key)
                    out.append(frame)
    return out


def _sire_destination(marker: str | None, snapshots: list[dict]) -> dict:
    reasons = ["no se observo la entrada de menu: su etiqueta no estaba en la lista blanca del reconocimiento (se desconoce nivel, profundidad y nodo accionable)"]
    if marker is None:
        return {"observed": False, "entry": None, "arrival": None, "evidence_sufficient": False,
                "reasons": reasons + ["no hay NINGUNA identidad de llegada observada para este destino"]}
    matches = [f for f in _app_frames(snapshots) if marker in str(f["url"].get("path") or "")]
    if len(matches) != 1:
        return {"observed": False, "entry": None, "arrival": None, "evidence_sufficient": False,
                "reasons": reasons + [f"identidad de llegada: {len(matches)} frames coinciden; se requiere exactamente uno"]}
    frame = matches[0]
    arrival = {"kind": "FRAME", "host": frame["url"].get("host"), "path_pattern": frame["url"].get("path"), "once_ids": _once_ids(_nodes(frame)), "visible_tables": _tables(_nodes(frame))}
    reasons.append("la miga de pan del shell no se registro con etiqueta para este destino")
    return {"observed": True, "entry": None, "arrival": arrival, "breadcrumb_label": None, "evidence_sufficient": False, "reasons": reasons}


def _once_ids(nodes: list[dict]) -> list[str]:
    counts: dict[str, int] = {}
    for n in nodes:
        ident = n.get("id")
        if ident and ident != "[redacted]":
            counts[ident] = counts.get(ident, 0) + 1
    return sorted(i for i, c in counts.items() if c == 1)[:MAX_UNIQUE_IDS * 4]


def _tables(nodes: list[dict]) -> int:
    return sum(1 for n in nodes if n.get("tag") == "table" and n.get("visible") and "form-table" in (n.get("classes") or []))


def _breadcrumb(shell_nodes: list[dict], label: str) -> str | None:
    for n in shell_nodes:
        if n.get("id") == "liBreadNivel4" and n.get("visible") and n.get("label") == label:
            return label
    return None


def analyze(snapshots: list[dict]) -> dict:
    report: dict = {"tool": "m3_contracts_from_recon", "destinations": {}, "warnings": [
        "Una sola observacion no prueba la ausencia de otros caminos ni los casos negativos: las pruebas de identidad negativas se escriben con fixtures.",
        "El informe NO habilita nada; la habilitacion exige revision humana, el contrato en destination_contract.py y la validacion de integracion.",
    ]}
    by_label = {s.get("label"): s for s in snapshots if isinstance(s, dict) and s.get("label")}
    for destination in DESTINATIONS + tuple(d for d in SIRE_APPS if d in by_label):  # con etiqueta propia se analiza la entrada de menu completa
        snap = by_label.get(destination)
        if snap is None:
            report["destinations"][destination] = {"observed": False, "evidence_sufficient": False, "reasons": ["el propietario no registro este destino (sin permiso o no lo recorrio)"]}
            continue
        pages = snap.get("pages") or []
        shell_main = []
        for page in pages:
            url = page.get("url") or {}
            if url.get("host") == SHELL_HOST and url.get("path") == SHELL_PATH:  # el shell autenticado (no la plataforma de la nueva Page)
                shell_main = _nodes(next((f for f in page.get("frames", []) if f.get("is_main")), {}))
        if destination == "TRAMITES_Y_CONSULTAS":
            # "Trámites y Consultas" es la tarjeta de la pagina publica que conduce al login y, autenticado, al Menu SOL completo: NO es un destino distinto.
            public = [n for p in pages if (p.get("url") or {}).get("host") in ENTRY_HOSTS for f in p.get("frames", []) if f.get("is_main") for n in _nodes(f)]
            card = [n for n in public if n.get("label") == destination and n.get("tag") in ("h2", "h3", "a", "span")]
            shell_pages = [p for p in pages if (p.get("url") or {}).get("host") == SHELL_HOST and (p.get("url") or {}).get("path") == SHELL_PATH]
            ok = len(card) >= 1 and len(shell_pages) == 1
            report["destinations"][destination] = {
                "observed": True, "alias_of": "MENU_SOL" if ok else None, "entry": None, "arrival": None, "page_count": snap.get("page_count"),
                "evidence_sufficient": ok,
                "reasons": [] if ok else ["no se observo a la vez la tarjeta publica y el shell autenticado del Menu SOL"],
            }
            continue
        if destination == "DECLARA_FACIL":
            platform = [p for p in pages if (p.get("url") or {}).get("path") == "/cl-ti-itmenu2/MenuInternetPlataforma.htm"]
            leaves = [n for p in platform for f in p.get("frames", []) if f.get("is_main") for n in _nodes(f)
                      if n.get("label") == destination and n.get("tag") == "span" and n.get("parent_tag") == "li"]
            report["destinations"][destination] = {
                "observed": True, "contained_in": "DECLARACIONES_Y_PAGOS" if platform and leaves else None, "entry": None, "arrival": None,
                "platform_leaf": {"parent_id": leaves[0].get("parent_id"), "levels": leaves[0].get("ancestor_cuerpos"), "visible_nodes": sum(1 for n in leaves if n.get("visible"))} if leaves else None,
                "page_count": snap.get("page_count"), "evidence_sufficient": False,
                "reasons": ["Declara Facil es una hoja DENTRO de la plataforma de Declaraciones y Pagos: no es un destino independiente (no se duplica) y se alcanza desde esa plataforma"],
            }
            continue
        entry, entry_reason = _find_entry(shell_main, destination) if shell_main else (None, "no se observo el shell autenticado en la captura")
        arrival, arrival_reason = _arrival(pages)
        reasons = [r for r in (entry_reason, arrival_reason) if r]
        if arrival is not None and not arrival["once_ids"] and not arrival["visible_tables"]:
            reasons.append("la llegada no muestra marcadores estructurales utilizables (ids unicos ni tablas)")
        if arrival is not None and arrival["kind"] == "FRAME" and _breadcrumb(shell_main, destination) is None:
            reasons.append("la miga de pan no identifica el destino (liBreadNivel4 con la etiqueta exacta)")
        report["destinations"][destination] = {
            "observed": True, "entry": entry, "arrival": arrival,
            "breadcrumb_label": _breadcrumb(shell_main, destination), "page_count": snap.get("page_count"),
            "evidence_sufficient": not reasons and entry is not None and arrival is not None, "reasons": reasons,
        }
    for destination, marker in SIRE_APPS.items():
        if destination not in report["destinations"]:
            report["destinations"][destination] = _sire_destination(marker, snapshots)
    return report


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    path = Path(argv[1])
    snapshots = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps(analyze(snapshots), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
