"""M3 v2 -- logica PURA del reconocedor de checkpoints (sanitizacion, evaluacion PRE/LLEGADA, vinculo, matriz, almacen). Datos SINTETICOS.
Las herramientas viven en `scripts/` (se omite si no es visible, p. ej. en Docker). El navegador se prueba con `scripts/m3_recon_selftest.py`."""

import copy
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
pytestmark = pytest.mark.skipif(not (SCRIPTS / "m3_recon_core.py").exists(), reason="raiz del repositorio no disponible (Docker)")
if (SCRIPTS / "m3_recon_core.py").exists():
    sys.path.insert(0, str(SCRIPTS))
    import m2_recon_sanitizer as san  # noqa: E402
    import m3_recon_core as core  # noqa: E402


def mnode(li, label, level, visible=True, ancestors=(), dup=1, kind="script", act_visible=True):
    return {"li_id": li, "level": level, "is_leaf": True, "label": label, "digest": "g" * 12, "visible": visible, "chain_visible": visible, "ancestors": list(ancestors),
            "cuerpos": len(ancestors), "dup_index": 0, "dup_count": dup, "container_id": "ulSubmenu",
            "actionable": {"tag": "a", "id": None, "kind": kind, "visible": act_visible}}


def frame(fid, nodes=None, path="/x", host="e-menu.sunat.gob.pe", main=True, ids=(), counts=None):
    view = {"menu_nodes": nodes or [], "nodes": [{"tag": "div", "id": i} for i in ids], "counts": counts or {"file_inputs": 0, "password_controls_visible": 0, "tables_visible": 0}}
    return {"frame_id": fid, "is_main": main, "url": {"scheme": "https", "host": host, "path": path}, "view": view}


def cp(dest, kind, windows, **kw):
    base = {"checkpoint_id": kw.pop("cid", "C001"), "destination": dest, "kind": kind, "status": "ACTIVE", "windows": windows, "linked_pre": None,
            "same_journey_confirmed": None, "observed": None}
    base.update(kw)
    return base


def win(wid, frames, opener=None):
    return {"window_id": wid, "opener": opener, "frames": frames, "active": True}


RVIE_NODES = [mnode("nivel1_10", "EMPRESAS", 1), mnode("nivel2_10_1", "SIRE_ROOT", 2, ancestors=["nivel1_10"]),
              mnode("nivel3_10_1_1", "SIRE_RVIE_REGISTRO", 3, ancestors=["nivel1_10", "nivel2_10_1"]),
              mnode("nivel4_10_1_1_1", "SIRE_RVIE_GESTION", 4, ancestors=["nivel1_10", "nivel2_10_1", "nivel3_10_1_1"])]


def spec(key="SIRE_RVIE"):
    return core.SPECS[key]


def good_pre(nodes=None):
    return cp("SIRE_RVIE", "PRE", [win("W1", [frame("F1", nodes or copy.deepcopy(RVIE_NODES))])], cid="C001")


def test_pre_requires_a_visible_leaf_with_the_full_ancestor_route_and_an_actionable():
    ev = core.evaluate_pre(spec(), good_pre())
    assert ev["status"] == "COMPLETE" and ev["entry"]["ancestor_labels"] == ["EMPRESAS", "SIRE_ROOT", "SIRE_RVIE_REGISTRO"] and ev["entry"]["header_levels"] == 3
    hidden = copy.deepcopy(RVIE_NODES)
    hidden[-1].update(visible=False, chain_visible=False)
    assert core.evaluate_pre(spec(), good_pre(hidden))["status"] == "PARTIAL"  # menu colapsado: hoja oculta
    assert core.evaluate_pre(spec(), good_pre(RVIE_NODES[:3]))["status"] == "MISSING"  # la hoja no esta
    no_root = [n for n in copy.deepcopy(RVIE_NODES) if n["label"] != "SIRE_ROOT"]
    assert any("SIRE_ROOT" in r for r in core.evaluate_pre(spec(), good_pre(no_root))["reasons"])  # ruta sin el encabezado esperado
    inert = copy.deepcopy(RVIE_NODES)
    inert[-1]["actionable"] = {"tag": None, "id": None, "kind": "none", "visible": False}
    # El menu REAL de SOL no usa <a> ni onclick: la ausencia de atributos accionables NO bloquea el PRE (se anota; la llegada vinculada lo confirma).
    inert_ev = core.evaluate_pre(spec(), good_pre(inert))
    assert inert_ev["status"] == "COMPLETE" and inert_ev["entry"]["click_target"] == "label-span" and inert_ev["entry"]["notes"]
    assert core.evaluate_pre(spec(), None)["status"] == "MISSING"


def test_duplicated_nodes_are_resolved_by_hierarchy_and_visibility_never_by_text_alone():
    dyp = core.DestSpec("X", "x", ("DECLARACIONES_Y_PAGOS",), "", "", allow_header_target=True)  # nodo con hijos en un menu (el reconocedor real ya no usa esta ruta para DYP)
    base = [mnode("nivel1_10", "EMPRESAS", 1), mnode("nivel2_10_2", None, 2, ancestors=["nivel1_10"])]
    anc = ["nivel1_10", "nivel2_10_2"]
    one_visible = base + [mnode("nivel3_86_2_1", "DECLARACIONES_Y_PAGOS", 3, ancestors=anc, dup=2), mnode("nivel3_86_2_1", "DECLARACIONES_Y_PAGOS", 3, visible=False, ancestors=anc, dup=2)]
    ev = core.evaluate_pre(dyp, cp("DECLARACIONES_Y_PAGOS", "PRE", [win("W1", [frame("F1", one_visible)])]))
    assert ev["status"] == "COMPLETE" and ev["entry"]["resolved_by"] == "hierarchy+visibility" and ev["entry"]["duplicate_nodes_in_li"] == 2
    two_visible = base + [mnode("nivel3_86_2_1", "DECLARACIONES_Y_PAGOS", 3, ancestors=anc, dup=2) for _ in range(2)]
    amb = core.evaluate_pre(dyp, cp("DECLARACIONES_Y_PAGOS", "PRE", [win("W1", [frame("F1", two_visible)])]))
    assert amb["status"] == "PARTIAL" and any("mismo li" in r for r in amb["reasons"])
    diff_li = base + [mnode("nivel3_86_2_1", "DECLARACIONES_Y_PAGOS", 3, ancestors=["nivel1_10"]), mnode("nivel3_86_9_1", "DECLARACIONES_Y_PAGOS", 3, ancestors=["nivel1_10"])]
    assert any("li distintos" in r for r in core.evaluate_pre(dyp, cp("DECLARACIONES_Y_PAGOS", "PRE", [win("W1", [frame("F1", diff_li)])]))["reasons"])


def arrival(**kw):
    pre = good_pre()
    shell = frame("F1", copy.deepcopy(RVIE_NODES), ids=["divContainerMenu"])
    app = frame("F2", path="/app/libroventas/x", host="e-factura.sunat.gob.pe", main=False, ids=["btn-aceptar", "nav-generacion-registro", "app-root"],
                counts={"file_inputs": 0, "password_controls_visible": 0, "tables_visible": 1})
    noise = frame("F3", path="/ww1/campanhaNotificacion", host="ww1.sunat.gob.pe", main=False, ids=["btnBuzon"])
    arr = cp("SIRE_RVIE", "ARRIVAL", [win("W1", [shell, app, noise])], cid="C002", linked_pre="C001", same_journey_confirmed=True,
             network={"total": 3, "by_method": {"GET": 3}, "non_get": []})
    arr.update(kw)
    return pre, arr


def test_arrival_needs_link_confirmation_new_target_and_discriminating_markers():
    pre, arr = arrival()
    ev = core.evaluate_arrival(spec(), pre, arr)
    assert ev["status"] == "COMPLETE" and ev["arrival"]["kind"] == "FRAME" and ev["arrival"]["frame"] == "F2" and "btn-aceptar" in ev["arrival"]["discriminating_ids"]
    assert core.evaluate_arrival(spec(), pre, dict(arr, same_journey_confirmed=None))["status"] == "PARTIAL"
    assert core.evaluate_arrival(spec(), pre, dict(arr, linked_pre="C009"))["status"] == "PARTIAL"
    assert core.evaluate_arrival(spec(), pre, dict(arr, link_stale=True))["status"] == "PARTIAL"
    assert core.evaluate_arrival(spec(), None, arr)["status"] == "PARTIAL"
    assert core.evaluate_arrival(spec(), pre, None)["status"] == "MISSING"
    few = copy.deepcopy(arr)
    few["windows"][0]["frames"][1]["view"]["nodes"] = [{"tag": "div", "id": "solo-uno"}]
    few["windows"][0]["frames"][1]["view"]["counts"].update(tables_visible=0, buttons_visible=0, forms=0)  # sin estructura visible: 1 solo id => no basta
    assert any("marcador" in r for r in core.evaluate_arrival(spec(), pre, few)["reasons"])
    login = copy.deepcopy(arr)
    login["windows"][0]["frames"][1]["view"]["counts"]["password_controls_visible"] = 1
    assert any("contraseña" in r for r in core.evaluate_arrival(spec(), pre, login)["reasons"])
    only_noise = copy.deepcopy(arr)  # la campana con btnBuzon jamas es la llegada
    only_noise["windows"][0]["frames"] = [only_noise["windows"][0]["frames"][0], only_noise["windows"][0]["frames"][2]]
    assert core.evaluate_arrival(spec(), pre, only_noise)["status"] == "PARTIAL"


def test_ids_of_a_preexisting_frame_with_the_same_path_do_not_count_but_those_of_another_app_still_loaded_do():
    # (a) el PRE ya tenia un frame con la MISMA ruta y los mismos ids: no cambio nada
    pre, arr = arrival()
    same = frame("F9", path="/app/libroventas/x", host="e-factura.sunat.gob.pe", main=False, ids=["btn-aceptar", "nav-generacion-registro", "app-root"])
    pre["windows"][0]["frames"].append(same)
    ev = core.evaluate_arrival(spec(), pre, arr)
    assert ev["status"] == "PARTIAL" and ev["arrival"] is None and any("no apareció ningún frame ni ventana nuevos" in r for r in ev["reasons"])
    # y si el propietario fija ese frame aun asi, la ruta repetida se rechaza explicitamente
    arr["observed"] = {"window": "W1", "frame": "F2"}
    forced = core.evaluate_arrival(spec(), pre, arr)
    assert forced["status"] == "PARTIAL" and any("ya existía en el PRE" in r for r in forced["reasons"]) and forced["arrival"]["positive_markers"]["path_new"] is False
    # (b) CASO REAL RCE/Generacion: una OTRA aplicacion (otra ruta) sigue cargada en el iframe y comparte los ids de la plataforma SIRE: NO descuenta marcadores
    pre, arr = arrival()
    stale = frame("F9", path="/app/librocompras/previa", host="e-factura.sunat.gob.pe", main=False, ids=["btn-aceptar", "nav-generacion-registro", "app-root"])
    pre["windows"][0]["frames"].append(stale)
    ok = core.evaluate_arrival(spec(), pre, arr)
    markers = ok["arrival"]["positive_markers"]
    assert ok["status"] == "COMPLETE" and markers["path_new"] is True and markers["shared_framework_ids"] is True and len(markers["ids"]) == 3


def test_generation_screen_without_ids_is_identified_by_new_path_structure_and_title_label():
    gen = spec("SIRE_GENERACION_REGISTROS")
    pre = cp("SIRE_GENERACION_REGISTROS", "PRE", [win("W1", [frame("F1", [mnode("nivel4_60_2_3_1_1", "SIRE_GENERACION_REGISTROS", 4)])])], cid="C010")
    app = frame("F8", path="/app/contribuyentems/servicio/libro/mige/generacionlibro/1.0.0/generacion-registro/x", host="e-factura.sunat.gob.pe", main=False,
                counts={"file_inputs": 0, "password_controls_visible": 0, "tables_visible": 2, "buttons_visible": 3, "forms": 0})
    app["view"]["nodes"] = [{"tag": "span", "id": None, "label": "SIRE_GENERACION_REGISTROS", "visible": True}]
    arr = cp("SIRE_GENERACION_REGISTROS", "ARRIVAL", [win("W1", [frame("F1", []), app])], cid="C011", linked_pre="C010", same_journey_confirmed=True)
    ev = core.evaluate_arrival(gen, pre, arr)
    assert ev["status"] == "COMPLETE" and ev["arrival"]["positive_markers"] == {"path_new": True, "ids": [], "structure": ["tables", "buttons"], "labels": ["SIRE_GENERACION_REGISTROS"],
                                                                           "units": 3, "shared_framework_ids": False}
    bare = copy.deepcopy(arr)
    bare["windows"][0]["frames"][1]["view"]["counts"].update(tables_visible=0, buttons_visible=0)
    bare["windows"][0]["frames"][1]["view"]["nodes"] = []
    assert core.evaluate_arrival(gen, pre, bare)["status"] == "PARTIAL"  # sin ids, estructura ni rotulo la llegada NO se acepta (criterio de seguridad intacto)


def test_arrival_of_another_destination_is_rejected_by_the_expected_path():
    pre, arr = arrival()
    wrong = copy.deepcopy(arr)
    wrong["windows"][0]["frames"][1]["url"]["path"] = "/app/contribuyentems/servicio/librocompras/mige/x"
    ev = core.evaluate_arrival(spec("SIRE_RVIE"), pre, wrong)  # se pide RVIE pero llego RCE
    assert ev["status"] == "PARTIAL" and any("no corresponde al destino esperado" in r for r in ev["reasons"])
    assert core.evaluate_arrival(spec("SIRE_RCE"), pre, wrong)["status"] == "COMPLETE"


def card(label_key=None, digest="g" * 12, visible=True, ingresar_visible=True, index=0):
    return {"headings": [{"tag": "h2", "label": label_key, "digest": digest, "visible": True}], "index": index, "visible": visible,
            "ingresar": {"tag": "span", "id": None, "classes": ["mr-4"], "visible": ingresar_visible, "parent_tag": "a"}, "chain": [{"tag": "a", "id": None, "classes": ["text-xl"]}]}


def public_pre(cards, host="www.sunat.gob.pe"):
    fr = frame("F1", path="/sol.html", host=host)
    fr["view"]["cards"] = cards
    return cp("DECLARACIONES_Y_PAGOS", "PRE", [win("W1", [fr])], cid="C020")


def test_public_card_entry_is_recognized_in_the_public_page_before_login_and_never_in_the_authenticated_menu():
    dyp = spec("DECLARACIONES_Y_PAGOS")
    assert dyp.entry_kind == "PUBLIC_CARD" and dyp.public_label == "MIS_DECLARACIONES_Y_PAGOS"
    ok = core.evaluate_pre(dyp, public_pre([card("TRAMITES_Y_CONSULTAS", index=0), card("MIS_DECLARACIONES_Y_PAGOS", "h" * 12, index=1), card(None, index=2)]))
    assert ok["status"] == "COMPLETE" and ok["entry"]["kind"] == "PUBLIC_CARD" and ok["entry"]["card_index"] == 1 and ok["entry"]["ingresar_tag"] == "span"
    assert core.evaluate_pre(dyp, public_pre([card("TRAMITES_Y_CONSULTAS"), card(None)]))["status"] == "MISSING"  # ninguna tarjeta con ese encabezado
    assert core.evaluate_pre(dyp, public_pre([card("MIS_DECLARACIONES_Y_PAGOS", ingresar_visible=False)]))["status"] == "PARTIAL"
    assert core.evaluate_pre(dyp, public_pre([card("MIS_DECLARACIONES_Y_PAGOS", index=0), card("MIS_DECLARACIONES_Y_PAGOS", index=1)]))["status"] == "PARTIAL"
    assert core.evaluate_pre(dyp, public_pre([card("MIS_DECLARACIONES_Y_PAGOS")], host="e-menu.sunat.gob.pe"))["status"] == "MISSING"  # no es la pagina publica
    menu_only = cp("DECLARACIONES_Y_PAGOS", "PRE", [win("W1", [frame("F1", [mnode("nivel3_86_2_1", "DECLARACIONES_Y_PAGOS", 3, ancestors=["nivel1_86"])])])])
    assert core.evaluate_pre(dyp, menu_only)["status"] == "MISSING"  # la ruta del Menu SOL ya NO sirve como entrada de este destino
    assert "DECLARA_FACIL" not in core.ORDER and "DECLARA_FACIL" in core.RESOLVED_ELSEWHERE


def test_prior_platform_arrival_is_reused_only_after_an_integrity_check():
    def plat(label, ids=("btnSalir", "iframeApplication", "ulSubmenu", "aOpcionPlataforma", "divBreadcrumb"), leaf=True, password=False):
        nodes = [{"tag": "div", "id": i, "visible": True, "label": None, "input_type": None} for i in ids]
        if leaf:
            nodes.append({"tag": "span", "id": None, "visible": True, "label": "DECLARA_FACIL", "input_type": None})
        if password:
            nodes.append({"tag": "input", "id": None, "visible": True, "label": None, "input_type": "password"})
        return {"label": label, "pages": [{"url": {"host": "e-menu.sunat.gob.pe", "path": core.PLATFORM_PATH}, "frames": [{"is_main": True, "url": {"host": "e-menu.sunat.gob.pe"}, "view": {"nodes": nodes}}]}]}

    good = [plat("DECLARACIONES_Y_PAGOS"), plat("DECLARA_FACIL")]
    report = core.verify_prior_platform(good)
    assert report["ok"] and report["independent_captures"] == 2 and report["window_association"] == "UNLINKED_V1"
    assert "DECLARACIONES_Y_PAGOS" in core.prior_arrivals_from_v1(good)
    for bad in (plat("X", ids=("btnSalir", "iframeApplication")), plat("X", leaf=False), plat("X", password=True), plat("X", ids=("btnSalir", "btnSalir", "iframeApplication", "ulSubmenu", "aOpcionPlataforma", "divBreadcrumb"))):
        assert not core.verify_prior_platform([bad])["ok"] and "DECLARACIONES_Y_PAGOS" not in core.prior_arrivals_from_v1([bad])
    assert not core.verify_prior_platform([])["ok"]


def test_post_requests_of_an_already_loaded_app_and_of_the_sire_baseline_are_explained_but_others_are_not():
    pre = cp("SIRE_RCE", "PRE", [win("W1", [frame("F1", []), frame("F6", path="/app/contribuyentems/servicio/libroventas/mige/gestionlibroventas/1.0.0/x", host="e-factura.sunat.gob.pe", main=False)])])
    base = {("POST", "e-menu.sunat.gob.pe", "/cl-ti-itmenu/MenuInternet.htm", "xhr")}
    posts = [{"method": "POST", "host": "e-menu.sunat.gob.pe", "path": "/cl-ti-itmenu/MenuInternet.htm", "resource_type": "xhr"},
             {"method": "POST", "host": "ww1.sunat.gob.pe", "path": "/app/contribuyentems/servicio/libroventas/mige/loader/gestionlibroventas.html", "resource_type": "xhr"}]
    assert core.unexplained_non_get(posts, pre, base) == []  # linea base + aplicacion YA cargada (RVIE) en el PRE
    stranger = {"method": "POST", "host": "ww1.sunat.gob.pe", "path": "/app/contribuyentems/servicio/libro/mige/otro/accion", "resource_type": "xhr"}
    assert core.unexplained_non_get(posts + [stranger], pre, base) == [stranger]
    gen = core.SPECS["SIRE_GENERACION_REGISTROS"]
    good_arr = {"status": "COMPLETE", "effects": {"file_inputs": 0, "non_get": posts}}
    assert core.generation_decision(gen, good_arr, {"owner_attests_view_only": True}, pre=pre, baseline=base) == "OPEN_CANDIDATE_REQUIRES_REVIEW"
    assert core.generation_decision(gen, {"status": "COMPLETE", "effects": {"file_inputs": 0, "non_get": posts + [stranger]}}, {"owner_attests_view_only": True}, pre=pre, baseline=base) == "MANUAL_ONLY"
    assert core.generation_decision(gen, {"status": "COMPLETE", "effects": {"file_inputs": 1, "non_get": []}}, {"owner_attests_view_only": True}, pre=pre, baseline=base) == "MANUAL_ONLY"
    assert core.generation_decision(gen, good_arr, {"owner_attests_view_only": False}, pre=pre, baseline=base) == "MANUAL_ONLY"


def test_new_window_popup_is_detected_with_its_opener_and_selection_resolves_ambiguity():
    pre, arr = arrival()
    arr["windows"].append(win("W2", [frame("F9", path="/app/libroventas/popup", ids=["aOpcionPlataforma", "divBreadcrumb"])], opener="W1"))
    ev = core.evaluate_arrival(spec(), pre, arr)  # frame nuevo en W1 + ventana nueva W2 => ambiguo
    assert ev["status"] == "PARTIAL" and any("candidatas" in r for r in ev["reasons"])
    arr["observed"] = {"window": "W2", "frame": "F9"}
    ok = core.evaluate_arrival(spec(), pre, arr)
    assert ok["status"] == "COMPLETE" and ok["arrival"]["kind"] == "NEW_PAGE" and ok["arrival"]["opener"] == "W1"
    arr["observed"] = {"window": "W7", "frame": "F1"}
    assert core.evaluate_arrival(spec(), pre, arr)["status"] == "PARTIAL"


def test_generation_is_manual_unless_the_opening_is_proven_harmless_and_attested():
    gen = spec("SIRE_GENERACION_REGISTROS")
    pre, arr = arrival()
    ev = core.evaluate_arrival(spec(), pre, arr)
    assert core.generation_decision(gen, ev, dict(arr, owner_attests_view_only=True)) == "OPEN_CANDIDATE_REQUIRES_REVIEW"
    assert core.generation_decision(gen, ev, dict(arr, owner_attests_view_only=False)) == "MANUAL_ONLY"
    post = dict(arr, network={"total": 2, "by_method": {"GET": 1, "POST": 1}, "non_get": [{"method": "POST", "host": "x", "path": "/y", "resource_type": "xhr"}]})
    assert core.generation_decision(gen, core.evaluate_arrival(spec(), pre, post), dict(post, owner_attests_view_only=True)) == "MANUAL_ONLY"
    filey = copy.deepcopy(arr)
    filey["windows"][0]["frames"][1]["view"]["counts"]["file_inputs"] = 1
    assert core.generation_decision(gen, core.evaluate_arrival(spec(), pre, filey), dict(filey, owner_attests_view_only=True)) == "MANUAL_ONLY"
    assert core.generation_decision(spec(), ev, arr) is None  # solo aplica a Generacion


def test_store_supersedes_without_deleting_is_atomic_and_resumable(tmp_path):
    store = core.Store(tmp_path / "r.json")
    store.add(good_pre())
    store.add(dict(good_pre(), checkpoint_id="C002"))
    assert [c["status"] for c in store.checkpoints] == ["SUPERSEDED", "ACTIVE"] and store.active("SIRE_RVIE", "PRE")["checkpoint_id"] == "C002"
    store.add(dict(arrival()[1], checkpoint_id="C003", linked_pre="C002"))
    store.add(dict(good_pre(), checkpoint_id="C004"))  # repetir el PRE invalida el vinculo de la llegada previa (sin borrarla)
    assert store.checkpoints[2].get("link_stale") is True and len(store.checkpoints) == 4
    again = core.Store(tmp_path / "r.json")
    assert len(again.checkpoints) == 4 and not list(tmp_path.glob("*.tmp"))


def test_matrix_distinguishes_complete_partial_prior_and_missing_and_flags_identical_rvie_rce(tmp_path):
    store = core.Store(tmp_path / "r.json")
    prior = {"SIRE_RVIE": {"path_pattern": "/same"}, "SIRE_RCE": {"path_pattern": "/same"}, "DECLARACIONES_Y_PAGOS": {"path_pattern": "/p"}}
    mx = core.matrix(store, prior)["destinations"]
    assert mx["SIRE_RVIE"]["arrival"] == "PRIOR_ONLY" and mx["SIRE_RVIE"]["overall"] == "PARTIAL" and mx["SIRE_GENERACION_REGISTROS"]["overall"] == "MISSING"
    assert any("MISMA ruta" in t for t in mx["SIRE_RCE"]["todo"])  # no se reutiliza un contrato sin evidencia propia
    store.add(good_pre())
    store.add(arrival()[1])
    full = core.matrix(store, {})["destinations"]["SIRE_RVIE"]
    assert full["overall"] == "COMPLETE" and full["link"] == "CONFIRMED"
    cands = core.candidate_contracts(core.matrix(store, {}))
    assert cands["SIRE_RVIE"]["enable"] is False and cands["SIRE_RVIE"]["derived_max_clicks"] == 4 and set(cands) == {"SIRE_RVIE"}


def test_dyp_needs_only_the_public_card_pre_when_the_verified_prior_platform_arrival_exists(tmp_path):
    store = core.Store(tmp_path / "r.json")
    store.add(public_pre([card("TRAMITES_Y_CONSULTAS", index=0), card("MIS_DECLARACIONES_Y_PAGOS", "h" * 12, index=1)]))
    prior = {"DECLARACIONES_Y_PAGOS": {"path_pattern": core.PLATFORM_PATH, "kind": "NEW_PAGE", "integrity": {"ok": True}}}
    row = core.matrix(store, prior)["destinations"]["DECLARACIONES_Y_PAGOS"]
    assert spec("DECLARACIONES_Y_PAGOS").arrival_optional_with_prior and row["overall"] == "COMPLETE" and row["arrival"] == "PRIOR_ONLY" and row["entry_kind"] == "PUBLIC_CARD"
    cand = core.candidate_contracts(core.matrix(store, prior))["DECLARACIONES_Y_PAGOS"]
    assert cand["enable"] is False and cand["entry"]["kind"] == "PUBLIC_CARD" and cand["entry"]["agent_support"] == "IMPLEMENTED" and cand["derived_max_clicks"] is None
    assert core.matrix(store, {})["destinations"]["DECLARACIONES_Y_PAGOS"]["overall"] == "PARTIAL"  # sin llegada previa verificada sigue haciendo falta la LLEGADA


def test_sanitizer_v2_keeps_structure_only_and_rejects_free_text_urls_and_bad_digests():
    node = san.sanitize_menu_node({"li_id": "nivel3_10_1_1", "level": 3, "is_leaf": True, "text_probe": "Declaraciones y Pagos", "digest": san.digest_of("Mi menu"),
                                   "visible": True, "chain_visible": True, "ancestors": ["nivel1_10", "RUC 20123456789", "x" * 80], "cuerpos": 2, "dup_index": 0,
                                   "dup_count": 2, "container_id": "ulSubmenu", "actionable": {"tag": "a", "id": "btn1", "kind": "javascript:alert(1)", "visible": True}})
    assert node["label"] == "DECLARACIONES_Y_PAGOS" and node["actionable"]["kind"] == "none"
    assert node["ancestors"][0] == "nivel1_10" and "[redacted]" in node["ancestors"] and not any("20123456789" in str(a) for a in node["ancestors"])
    assert san.sanitize_menu_node({"li_id": "n", "level": 3, "text_probe": "Texto libre del contribuyente JUAN", "digest": "no-valido"})["label"] is None
    assert san.sanitize_menu_node({"li_id": "n", "level": 3, "digest": "no-valido"})["digest"] is None
    assert san.sanitize_menu_node({"level": 99}) is None and san.sanitize_menu_node("x") is None
    net = san.sanitize_network([{"method": "POST", "url": "https://e-menu.sunat.gob.pe/api/accion/123456?token=abc&ruc=20123456789", "resource_type": "xhr"},
                                {"method": "GET", "url": "https://x.example/a", "resource_type": "image"}] * 3)
    dumped = json.dumps(net)
    assert net["by_method"] == {"POST": 3, "GET": 3} and len(net["non_get"]) == 1 and "token" not in dumped and "20123456789" not in dumped and "?" not in dumped
    view = san.sanitize_view_v2({"nodes": [], "menu_nodes": [{"level": 3, "li_id": "n3"}], "counts": {"file_inputs": 2, "forms": "x", "evil": 9}, "title": "Mi nombre"})
    assert view["counts"]["file_inputs"] == 2 and view["counts"]["forms"] == 0 and "evil" not in view["counts"] and view["title_key"] == "[not-allowlisted]"


def test_digest_is_stable_case_and_space_insensitive_and_has_no_digits():
    assert san.digest_of("Gestión de Compras") == san.digest_of("  gestión   DE compras ")
    assert san.digest_of("a") != san.digest_of("b") and not any(ch.isdigit() for ch in san.digest_of("cualquier texto 12345"))
    assert san.safe_digest(san.digest_of("x")) and san.safe_digest("abcdefghijkl") is None


def test_header_and_leaf_with_the_same_label_resolve_to_the_leaf_and_header_only_targets_are_explicit():
    gen = spec("SIRE_GENERACION_REGISTROS")
    head = mnode("nivel3_60_2_3", "SIRE_GENERACION_REGISTROS", 3, ancestors=["nivel1_60", "nivel2_60_2"])
    head["is_leaf"] = False
    leaf = mnode("nivel4_60_2_3_1_1", "SIRE_GENERACION_REGISTROS", 4, ancestors=["nivel1_60", "nivel2_60_2", "nivel3_60_2_3"])
    base = [mnode("nivel1_60", "SIRE_ROOT", 1), mnode("nivel2_60_2", None, 2, ancestors=["nivel1_60"])]
    ev = core.evaluate_pre(gen, cp("SIRE_GENERACION_REGISTROS", "PRE", [win("W1", [frame("F1", base + [head, leaf])])]))
    assert ev["status"] == "COMPLETE" and ev["entry"]["leaf_li_id"] == "nivel4_60_2_3_1_1" and any("cabecera" in n for n in ev["entry"]["notes"])
    only_head = core.evaluate_pre(gen, cp("SIRE_GENERACION_REGISTROS", "PRE", [win("W1", [frame("F1", base + [head])])]))
    assert only_head["status"] == "PARTIAL" and any("solo aparece como cabecera" in r for r in only_head["reasons"])
    dyp = core.DestSpec("X", "x", ("DECLARACIONES_Y_PAGOS",), "", "", allow_header_target=True)  # un destino de menu que es un nodo con hijos: la cabecera visible SI es el objetivo
    node = mnode("nivel3_86_2_1", "DECLARACIONES_Y_PAGOS", 3, ancestors=["nivel1_86"])
    node["is_leaf"] = False
    assert core.evaluate_pre(dyp, cp("DECLARACIONES_Y_PAGOS", "PRE", [win("W1", [frame("F1", [mnode("nivel1_86", None, 1), node])])]))["status"] == "COMPLETE"


def test_visible_leaf_in_several_windows_is_ambiguous_until_the_owner_fixes_window_and_frame():
    nodes = lambda: copy.deepcopy(RVIE_NODES)  # noqa: E731
    both = cp("SIRE_RVIE", "PRE", [win("W1", [frame("F1", nodes())]), win("W2", [frame("F2", nodes())])])
    amb = core.evaluate_pre(spec(), both)
    assert amb["status"] == "PARTIAL" and any("elija con V" in r and "W1/F1" in r and "W2/F2" in r for r in amb["reasons"])
    both["observed"] = {"window": "W2", "frame": "F2"}
    ok = core.evaluate_pre(spec(), both)
    assert ok["status"] == "COMPLETE" and ok["entry"]["window"] == "W2" and ok["entry"]["frame"] == "F2"
    both["observed"] = {"window": "W9", "frame": "F9"}
    assert core.evaluate_pre(spec(), both)["status"] == "MISSING"  # lo fijado no existe: no se cae a otra ventana por su cuenta


def test_one_expanded_menu_capture_evidences_several_destinations_without_duplicating_the_file(tmp_path):
    store = core.Store(tmp_path / "r.json")
    nodes = copy.deepcopy(RVIE_NODES) + [
        mnode("nivel3_10_1_2", "SIRE_RCE_REGISTRO", 3, ancestors=["nivel1_10", "nivel2_10_1"]),
        mnode("nivel4_10_1_2_1", "SIRE_RCE_GESTION", 4, ancestors=["nivel1_10", "nivel2_10_1", "nivel3_10_1_2"])]
    nodes[1]["label"] = "SIRE_ROOT"
    store.add(cp("SIRE_RVIE", "PRE", [win("W1", [frame("F1", nodes)])], cid="C001"))
    added = store.derive_shared_pre()
    assert added == ["SIRE_RCE"]  # Generacion/DYP/Declara Facil no tienen hoja visible en esa captura: no se inventan
    rce = store.pre_for("SIRE_RCE")
    assert rce["checkpoint_id"] != "C001" and rce["destination"] == "SIRE_RCE" and rce["reuse_of"] == "C001"
    assert core.evaluate_pre(spec("SIRE_RCE"), rce)["status"] == "COMPLETE"
    assert store.derive_shared_pre() == []  # idempotente
    assert sum(1 for c in store.checkpoints if c["windows"]) == 1  # una sola captura pesada en el archivo
    # una llegada vinculada al PRE reutilizado usa SU id (no el del original)
    assert rce["checkpoint_id"] == store.active("SIRE_RCE", "PRE")["checkpoint_id"]
    ctx = core.Store(tmp_path / "r.json")
    assert ctx.pre_for("SIRE_RCE")["reuse_of"] == "C001"  # sobrevive a reanudar


def test_pointer_affordance_is_a_valid_actionable_kind_and_unknown_kinds_collapse_to_none():
    ok = san.sanitize_menu_node({"li_id": "n", "level": 4, "actionable": {"tag": "span", "kind": "pointer", "visible": True}})
    assert ok["actionable"]["kind"] == "pointer"
    assert san.sanitize_menu_node({"li_id": "n", "level": 4, "actionable": {"tag": "span", "kind": "eval(1)", "visible": True}})["actionable"]["kind"] == "none"


def test_owner_can_point_to_the_public_card_by_position_when_its_heading_text_is_not_in_the_whitelist():
    dyp = spec("DECLARACIONES_Y_PAGOS")
    pre = public_pre([card(None, "a" * 12, index=0), card(None, "b" * 12, index=1), card("TRAMITES_Y_CONSULTAS", index=2)])
    miss = core.evaluate_pre(dyp, pre)
    assert miss["status"] == "MISSING" and "K <n>" in miss["reasons"][0] and "0..2" in miss["reasons"][0]
    pre["card_choice"] = 1
    chosen = core.evaluate_pre(dyp, pre)
    assert chosen["status"] == "COMPLETE" and chosen["entry"]["card_index"] == 1 and chosen["entry"]["chosen_by_owner_position"] is True and chosen["entry"]["heading_digest"] == "b" * 12
    pre["card_choice"] = 9
    assert core.evaluate_pre(dyp, pre)["status"] == "MISSING"
    pre["card_choice"] = 1
    pre["windows"][0]["frames"][0]["view"]["cards"][1]["ingresar"]["visible"] = False
    assert core.evaluate_pre(dyp, pre)["status"] == "PARTIAL"  # la posicion elegida no se acepta si su INGRESAR no esta visible
