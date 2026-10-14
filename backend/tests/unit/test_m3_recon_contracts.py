"""M3 -- el analizador de reconocimiento convierte un JSON SANEADO en candidatos de contrato SIN habilitar nada y declara por que la evidencia
aun no basta. Datos SINTETICOS (no son una observacion de SUNAT). Las herramientas viven en `scripts/` (se omite si no es visible)."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
pytestmark = pytest.mark.skipif(not (SCRIPTS / "m3_contracts_from_recon.py").exists(), reason="raiz del repositorio no disponible (Docker)")

if (SCRIPTS / "m3_contracts_from_recon.py").exists():
    sys.path.insert(0, str(SCRIPTS))
    import m2_recon_sanitizer as san  # noqa: E402
    import m3_contracts_from_recon as rc  # noqa: E402


def node(**kw):
    base = {"tag": "div", "id": None, "classes": [], "role": None, "input_type": None, "label": None, "aria_label": None, "href_path": None, "visible": True,
            "depth": 5, "sibling_index": 0, "parent_tag": "div", "parent_id": None, "has_onclick": False, "ancestor_cuerpos": None}
    base.update(kw)
    return base


def frame(url, nodes, main=True):
    return {"is_main": main, "url": url, "view": {"nodes": nodes}}


SHELL = {"scheme": "https", "host": "e-menu.sunat.gob.pe", "path": "/cl-ti-itmenu/MenuInternet.htm"}


def menu_leaf(label, level=3, cuerpos=2):
    return node(tag="span", label=label, parent_tag="li", parent_id=f"nivel{level}_10_1_1", ancestor_cuerpos=cuerpos)


def snapshot(label, pages):
    return {"label": label, "page_count": len(pages), "pages": pages}


def frame_snapshot(label):
    shell = {"url": SHELL, "frames": [
        frame(SHELL, [menu_leaf(label), node(tag="li", id="liBreadNivel4", label=label)]),
        frame({"scheme": "https", "host": "ww1.sunat.gob.pe", "path": "/mod-app/{id}"}, [node(tag="input", id="marcadorUno", input_type="hidden"),
              node(tag="table", classes=["form-table"]), node(tag="table", classes=["form-table"])], main=False)]}
    return snapshot(label, [shell])


def page_snapshot(label):
    shell = {"url": SHELL, "frames": [frame(SHELL, [menu_leaf(label, level=4, cuerpos=3)])]}
    other = {"url": {"scheme": "https", "host": "orion.sunat.gob.pe", "path": "/app/inicio"}, "frames": [frame({"scheme": "https", "host": "orion.sunat.gob.pe", "path": "/app/inicio"}, [node(tag="div", id="appRoot")])]}
    return snapshot(label, [shell, other])


def test_frame_destination_with_full_structural_evidence_is_a_sufficient_candidate_but_never_enables_anything():
    report = rc.analyze([frame_snapshot("SIRE")])
    d = report["destinations"]["SIRE"]
    assert d["evidence_sufficient"] is True and d["reasons"] == []
    assert d["entry"] == {"kind": "MENU_LEAF", "label": "SIRE", "leaf_level": 3, "levels": 2, "visible_at_capture": True}
    assert d["arrival"]["kind"] == "FRAME" and d["arrival"]["host"] == "ww1.sunat.gob.pe" and d["arrival"]["path_pattern"] == "/mod-app/{id}"
    assert d["arrival"]["once_ids"] == ["marcadorUno"] and d["arrival"]["visible_tables"] == 2 and d["breadcrumb_label"] == "SIRE"
    assert any("NO habilita" in w for w in report["warnings"])
    assert "enabled" not in json.dumps(report).lower().replace("evidence_sufficient", "")  # el informe no contiene ninguna orden de habilitacion


def test_new_page_destination_is_detected_by_host_and_path_pattern():
    d = rc.analyze([page_snapshot("DECLARACIONES_Y_PAGOS")])["destinations"]["DECLARACIONES_Y_PAGOS"]
    assert d["arrival"]["kind"] == "NEW_PAGE" and d["arrival"]["host"] == "orion.sunat.gob.pe" and d["arrival"]["once_ids"] == ["appRoot"]
    assert d["entry"]["levels"] == 3 and d["entry"]["leaf_level"] == 4 and d["evidence_sufficient"] is True


def test_missing_ambiguous_or_incomplete_evidence_is_reported_not_guessed():
    report = rc.analyze([frame_snapshot("SIRE")])
    # destinos que el propietario no registro (sin permiso o no recorridos)
    for other in ("DECLARACIONES_Y_PAGOS", "TRAMITES_Y_CONSULTAS"):
        assert report["destinations"][other]["observed"] is False and report["destinations"][other]["evidence_sufficient"] is False
    # entrada ausente del menu de la cuenta
    snap = frame_snapshot("SIRE")
    snap["pages"][0]["frames"][0]["view"]["nodes"] = [node(tag="li", id="liBreadNivel4", label="SIRE")]
    d = rc.analyze([snap])["destinations"]["SIRE"]
    assert d["evidence_sufficient"] is False and any("no se observo la entrada" in r for r in d["reasons"])
    # etiqueta ambigua
    snap = frame_snapshot("SIRE")
    snap["pages"][0]["frames"][0]["view"]["nodes"].append(menu_leaf("SIRE"))
    assert any("AMBIGUA" in r for r in rc.analyze([snap])["destinations"]["SIRE"]["reasons"])
    # sin profundidad registrada (reconocimiento antiguo)
    snap = frame_snapshot("SIRE")
    snap["pages"][0]["frames"][0]["view"]["nodes"][0]["ancestor_cuerpos"] = None
    assert any("ancestor_cuerpos" in r for r in rc.analyze([snap])["destinations"]["SIRE"]["reasons"])
    # miga de pan que no identifica el destino
    snap = frame_snapshot("SIRE")
    snap["pages"][0]["frames"][0]["view"]["nodes"][1]["label"] = None
    assert any("miga de pan" in r for r in rc.analyze([snap])["destinations"]["SIRE"]["reasons"])
    # llegada sin marcadores
    snap = frame_snapshot("SIRE")
    snap["pages"][0]["frames"][1]["view"]["nodes"] = []
    assert any("marcadores" in r for r in rc.analyze([snap])["destinations"]["SIRE"]["reasons"])


def test_two_new_pages_or_two_frames_are_ambiguous_and_insufficient():
    snap = page_snapshot("SIRE")
    snap["pages"].append(dict(snap["pages"][1]))
    assert any("varias Pages" in r for r in rc.analyze([snap])["destinations"]["SIRE"]["reasons"])
    snap = frame_snapshot("SIRE")
    snap["pages"][0]["frames"].append(frame({"scheme": "https", "host": "ww1.sunat.gob.pe", "path": "/otro"}, [], main=False))
    assert any("varios frames" in r for r in rc.analyze([snap])["destinations"]["SIRE"]["reasons"])


def test_sanitizer_now_records_menu_depth_as_a_bounded_integer_only():
    raw = {"tag": "span", "id": "x", "classes": [], "role": None, "type": None, "text_probe": "sire", "aria_probe": None, "href": None, "visible": True,
           "depth": 3, "sibling_index": 0, "parent_tag": "li", "parent_id": "nivel3_10", "has_onclick": False, "ancestor_cuerpos": 2}
    assert san.sanitize_node(raw)["ancestor_cuerpos"] == 2
    assert san.sanitize_node({**raw, "ancestor_cuerpos": "<script>"})["ancestor_cuerpos"] is None
    assert san.sanitize_node({**raw, "ancestor_cuerpos": 9999})["ancestor_cuerpos"] is None


# --- correccion de alcance: Tramites y Consultas = Menu SOL; Declara Facil dentro de la plataforma; SIRE por aplicacion; ruido del shell ---
PLATFORM = {"scheme": "https", "host": "e-menu.sunat.gob.pe", "path": "/cl-ti-itmenu2/MenuInternetPlataforma.htm"}
NOISE = [frame({"scheme": "https", "host": "ww1.sunat.gob.pe", "path": "/{id}/campanhaNotificacion"}, [node(tag="button", id="btnBuzon", visible=False)], main=False),
         frame({"scheme": "https", "host": "ww1.sunat.gob.pe", "path": "/time/gettime.pl"}, [], main=False)]
PUBLIC = {"scheme": "https", "host": "www.sunat.gob.pe", "path": "/sol.html"}


def test_tramites_y_consultas_is_the_public_card_that_leads_to_the_full_menu_sol_not_a_distinct_destination():
    snap = snapshot("TRAMITES_Y_CONSULTAS", [{"url": PUBLIC, "frames": [frame(PUBLIC, [node(tag="h2", label="TRAMITES_Y_CONSULTAS")])]}, {"url": SHELL, "frames": [frame(SHELL, [])]}])
    d = rc.analyze([snap])["destinations"]["TRAMITES_Y_CONSULTAS"]
    assert d["alias_of"] == "MENU_SOL" and d["evidence_sufficient"] is True
    no_shell = snapshot("TRAMITES_Y_CONSULTAS", [{"url": PUBLIC, "frames": [frame(PUBLIC, [node(tag="h2", label="TRAMITES_Y_CONSULTAS")])]}])
    assert rc.analyze([no_shell])["destinations"]["TRAMITES_Y_CONSULTAS"]["evidence_sufficient"] is False


def test_declara_facil_inside_the_platform_is_not_an_independent_destination():
    leaf = node(tag="span", label="DECLARA_FACIL", parent_tag="li", parent_id="nivel3_55_1_1", ancestor_cuerpos=2)
    snap = snapshot("DECLARA_FACIL", [{"url": SHELL, "frames": [frame(SHELL, [])]}, {"url": PLATFORM, "frames": [frame(PLATFORM, [leaf, dict(leaf, visible=False)])]}])
    d = rc.analyze([snap])["destinations"]["DECLARA_FACIL"]
    assert d["contained_in"] == "DECLARACIONES_Y_PAGOS" and d["evidence_sufficient"] is False and d["platform_leaf"]["visible_nodes"] == 1


def test_duplicate_menu_node_in_the_same_li_keeps_the_entry_insufficient():
    leaf = menu_leaf("DECLARACIONES_Y_PAGOS", level=3, cuerpos=2)
    snap = snapshot("DECLARACIONES_Y_PAGOS", [{"url": SHELL, "frames": [frame(SHELL, [leaf, dict(leaf)])]}, {"url": PLATFORM, "frames": [frame(PLATFORM, [node(tag="div", id="iframeApplication")])]}])
    d = rc.analyze([snap])["destinations"]["DECLARACIONES_Y_PAGOS"]
    assert d["evidence_sufficient"] is False and any("MISMO li" in r for r in d["reasons"]) and d["arrival"]["path_pattern"].endswith("MenuInternetPlataforma.htm")


def test_unrelated_shell_frames_are_ignored_and_sire_apps_are_classified_by_path_without_entry_evidence():
    rvie = frame({"scheme": "https", "host": "e-factura.sunat.gob.pe", "path": "/app/x/libroventas/y"}, [node(tag="button", id="btn-aceptar")], main=False)
    rce = frame({"scheme": "https", "host": "e-factura.sunat.gob.pe", "path": "/app/x/librocompras/y"}, [node(tag="button", id="btn-aceptar")], main=False)
    snap = snapshot("SIRE", [{"url": SHELL, "frames": [frame(SHELL, [])] + NOISE + [rvie]}, {"url": SHELL, "frames": [frame(SHELL, [])] + NOISE + [rce]}])
    report = rc.analyze([snap])["destinations"]
    assert report["SIRE_RVIE"]["arrival"]["path_pattern"].endswith("/libroventas/y") and report["SIRE_RCE"]["arrival"]["path_pattern"].endswith("/librocompras/y")
    for key in ("SIRE_RVIE", "SIRE_RCE", "SIRE_GENERACION_REGISTROS"):
        assert report[key]["entry"] is None and report[key]["evidence_sufficient"] is False  # sin entrada de menu observada NUNCA es suficiente
    assert report["SIRE_GENERACION_REGISTROS"]["arrival"] is None
    assert "btnBuzon" not in json.dumps(report)  # el ruido (campana con boton de Buzon) no entra en ningun contrato candidato
