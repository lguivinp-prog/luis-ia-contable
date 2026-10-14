"""M3: catalogo UNICO de destinos (backend) y contratos estructurales del agente: coherencia entre capas, fail-closed y presupuestos de clics
derivados de secuencias validadas."""

from __future__ import annotations

import pytest

from app.core import sunat_destinations as sd
from app.models.sol_agent_access import SolAccessDestination
from app.schemas.sol_access import CreateSolAccessSessionRequest, SolDestination
from typing import get_args
from windows_agent import destination_contract as dc
from windows_agent.portal_identity import ElementInfo, LoginSnapshot

KNOWN = (
    "MENU_SOL", "FICHA_RUC", "SIRE", "SIRE_RVIE", "SIRE_RCE", "SIRE_GENERACION_REGISTROS", "DECLARACIONES_Y_PAGOS", "DECLARA_FACIL", "TRAMITES_Y_CONSULTAS",
)
NOT_VALIDATED_IDS = ("SIRE", "SIRE_GENERACION_REGISTROS", "DECLARA_FACIL")  # RVIE, RCE y Declaraciones y Pagos pasaron la aceptacion real (14/10/2026)
ACCEPTED_IDS = ("SIRE_RVIE", "SIRE_RCE", "DECLARACIONES_Y_PAGOS")


def test_every_layer_knows_exactly_the_same_destinations():
    assert set(sd.all_ids()) == set(KNOWN) and len(sd.all_ids()) == len(KNOWN)
    assert {d.value for d in SolAccessDestination} == set(KNOWN)
    assert set(get_args(SolDestination)) == set(KNOWN)
    assert {d.value for d in dc.Destination} == set(KNOWN)
    assert set(dc.CONTRACTS) == set(KNOWN)


def test_backend_and_agent_agree_on_validation_alias_containment_and_open_only():
    for policy in sd.CATALOG:
        contract = dc.CONTRACTS[policy.id]
        assert (policy.validation, policy.alias_of, policy.contained_in, policy.open_only) == (
            contract.validation, contract.alias_of, contract.contained_in, contract.open_only), policy.id
    assert sd.enabled_ids() == ["MENU_SOL", "FICHA_RUC", "SIRE_RVIE", "SIRE_RCE", "DECLARACIONES_Y_PAGOS"]  # el alias NO se lista dos veces
    for policy in sd.CATALOG:  # la regla de prueba por perfil tambien debe ser la misma en ambas capas: solo los aceptados la exigen
        assert policy.requires_profile_proof == (policy.id in ACCEPTED_IDS), policy.id
        if policy.id in ACCEPTED_IDS + ("SIRE_GENERACION_REGISTROS",):
            assert dc.CONTRACTS[policy.id].evidence == policy.evidence, policy.id
    assert {i for i, c in dc.CONTRACTS.items() if c.enabled and c.alias_of is None} == set(sd.enabled_ids())


def test_tramites_y_consultas_is_an_alias_of_menu_sol_never_a_second_destination_or_authentication():
    assert sd.resolve("TRAMITES_Y_CONSULTAS") == "MENU_SOL" and sd.is_enabled("TRAMITES_Y_CONSULTAS")
    assert sd.resolve("FICHA_RUC") == "FICHA_RUC" and sd.resolve(None) is None
    policy = sd.policy_of("TRAMITES_Y_CONSULTAS")
    assert policy.alias_of == "MENU_SOL" and not policy.offered and policy.evidence == "ALIAS"
    contract = dc.CONTRACTS["TRAMITES_Y_CONSULTAS"]
    assert contract.entry is None and contract.arrival is None and contract.max_clicks == 0  # sin clics propios


def test_declara_facil_is_contained_in_the_platform_and_cannot_be_enabled_independently():
    policy = sd.policy_of("DECLARA_FACIL")
    assert policy.contained_in == "DECLARACIONES_Y_PAGOS" and not policy.offered and not policy.enabled
    assert dc.CONTRACTS["DECLARA_FACIL"].contained_in == "DECLARACIONES_Y_PAGOS" and not dc.CONTRACTS["DECLARA_FACIL"].enabled


def test_legacy_sire_is_superseded_by_the_three_specific_sire_destinations():
    legacy = sd.policy_of("SIRE")
    assert legacy.superseded_by == ("SIRE_RVIE", "SIRE_RCE", "SIRE_GENERACION_REGISTROS") and not legacy.offered
    assert [d.id for d in sd.CATALOG if d.offered] == ["MENU_SOL", "FICHA_RUC", "SIRE_RVIE", "SIRE_RCE", "SIRE_GENERACION_REGISTROS", "DECLARACIONES_Y_PAGOS"]


def test_sire_generacion_registros_is_open_only_complete_with_recon_evidence_but_still_disabled():
    policy, contract = sd.policy_of("SIRE_GENERACION_REGISTROS"), dc.CONTRACTS["SIRE_GENERACION_REGISTROS"]
    assert policy.open_only and contract.open_only and not policy.enabled and not contract.enabled
    assert policy.evidence == contract.evidence == "RECON_COMPLETE" and contract.entry is not None and contract.arrival is not None
    assert contract.max_clicks == 0 and contract.recon_max_clicks == 4  # el presupuesto operativo es 0 mientras no haya aceptacion en vivo
    assert contract.arrival.required_once_ids == () and contract.arrival.min_visible_tables == 2 and contract.arrival.table_selector == "table"
    assert not sd.is_enabled("SIRE_GENERACION_REGISTROS")


def test_evidence_levels_match_what_the_real_recon_observed():
    evidence = {d.id: d.evidence for d in sd.CATALOG}
    assert evidence["MENU_SOL"] == evidence["FICHA_RUC"] == "NONE"  # validados por sesion en vivo M2, no por este reconocimiento
    assert evidence["SIRE_GENERACION_REGISTROS"] == "RECON_COMPLETE"  # reconocida, NUNCA aceptada: sigue manual
    assert evidence["SIRE_RVIE"] == evidence["SIRE_RCE"] == evidence["DECLARACIONES_Y_PAGOS"] == "LIVE_ACCEPTED"  # reconocimiento real + aceptacion en vivo
    assert {i: c.evidence for i, c in dc.CONTRACTS.items() if c.evidence in {"RECON_COMPLETE", "LIVE_ACCEPTED", "ARRIVAL_ONLY"}} == {
        "SIRE_RVIE": "LIVE_ACCEPTED", "SIRE_RCE": "LIVE_ACCEPTED", "SIRE_GENERACION_REGISTROS": "RECON_COMPLETE", "DECLARACIONES_Y_PAGOS": "LIVE_ACCEPTED"}
    for destination in ("SIRE_RVIE", "SIRE_RCE"):  # LIVE_ACCEPTED => entrada + llegada con evidencia real, habilitados con presupuesto derivado
        contract = dc.CONTRACTS[destination]
        assert contract.entry is not None and contract.arrival is not None and contract.enabled and contract.max_clicks == 4 and contract.evidence_ref
    generation = dc.CONTRACTS["SIRE_GENERACION_REGISTROS"]
    assert generation.entry is not None and generation.arrival is not None and not generation.enabled and generation.max_clicks == 0 and generation.open_only
    assert dc.CONTRACTS["SIRE_RVIE"].arrival.path_prefix != dc.CONTRACTS["SIRE_RCE"].arrival.path_prefix != dc.CONTRACTS["SIRE_GENERACION_REGISTROS"].arrival.path_prefix
    dyp = dc.CONTRACTS["DECLARACIONES_Y_PAGOS"]
    assert dyp.arrival.kind == "NEW_PAGE" and dyp.arrival.path_prefix.endswith("MenuInternetPlataforma.htm")
    assert dyp.entry.kind == "PUBLIC_CARD" and dyp.enabled and dyp.max_clicks == 0 and dyp.recon_max_clicks == 0  # NO es una hoja del Menu SOL
    assert dyp.evidence_ref == ("C012", "C013") and dc.public_landing_prefix(dyp.arrival) == "/cl-ti-itmenu2/"


def test_menu_leaf_labels_are_the_exact_texts_identified_by_the_real_recon():
    """La huella del texto de menu capturada en el reconocimiento real permitio fijar la forma EXACTA (RVIE con tilde; RCE y Generacion sin tilde): el motor
    compara el texto con una expresion sensible a tildes, asi que una forma equivocada no encontraria la hoja."""
    import sys
    from pathlib import Path

    scripts = Path(__file__).resolve().parents[3] / "scripts"
    if not (scripts / "m2_recon_sanitizer.py").exists():
        pytest.skip("raiz del repositorio no disponible (Docker)")
    sys.path.insert(0, str(scripts))
    import m2_recon_sanitizer as san

    real_digests = {"SIRE_RVIE": "sipsjrgrlihv", "SIRE_RCE": "rqjuprkqhtjg", "SIRE_GENERACION_REGISTROS": "hviljnrikvhu"}  # del checkpoint real (huellas, no texto)
    for destination, digest in real_digests.items():
        assert san.digest_of(dc.CONTRACTS[destination].entry.label) == digest, destination
        assert dc.CONTRACTS[destination].entry.leaf_level == 4 and dc.CONTRACTS[destination].entry.min_levels == dc.CONTRACTS[destination].entry.max_levels == 3


def test_unvalidated_public_card_and_unknown_entry_kinds_fail_closed_in_the_navigation_engine():
    """El motor SOPORTA PUBLIC_CARD (landing validado por ids), pero un contrato no validado se rechaza antes de tocar nada y un tipo de entrada
    desconocido falla cerrado aun si alguien lo marcara como validado."""
    import dataclasses

    from windows_agent.destination_executor import DestinationNavigator

    navigator = DestinationNavigator.__new__(DestinationNavigator)
    base = dc.CONTRACTS["DECLARACIONES_Y_PAGOS"]
    unvalidated = dataclasses.replace(base, validation=dc.NOT_VALIDATED)
    assert navigator._navigate_contract(unvalidated, page=None) == "destination not validated"
    assert navigator._navigate_contract(dc.CONTRACTS["SIRE_GENERACION_REGISTROS"], page=None) == "destination not validated"  # Generacion: manual
    odd = dataclasses.replace(base, validation=dc.LIVE_VALIDATED, entry=dataclasses.replace(base.entry, kind="SOMETHING_ELSE"))
    assert navigator._navigate_contract(odd, page=None) == "destination entry kind not supported"


def test_no_destination_may_automate_upload_generation_submission_payment_or_rectification():
    assert set(sd.FORBIDDEN_AUTOMATION) == set(dc.FORBIDDEN_AUTOMATION) >= {"UPLOAD_FILE", "GENERATE_RECORDS", "SUBMIT", "SEND", "DECLARE", "PAY", "RECTIFY"}
    assert all(c.human_control for c in dc.CONTRACTS.values())  # tras validar la llegada el navegador pasa al humano


def test_only_ficha_ruc_may_extract_data_in_both_layers():
    assert [d.id for d in sd.CATALOG if d.extraction] == ["FICHA_RUC"]
    assert [c.id for c in dc.CONTRACTS.values() if c.extraction] == ["FICHA_RUC"]


def test_unknown_or_out_of_scope_destinations_are_not_in_the_catalog():
    for name in ("BUZON_SUNAT", "SUNAFIL", "BUZON", "https://sunat.gob.pe", "<script>"):
        assert not sd.is_known(name) and not sd.is_enabled(name)
    assert sd.EXCLUDED_FROM_SCOPE >= {"BUZON_SUNAT", "SUNAFIL"}
    assert not sd.is_known(None) and not sd.is_enabled(None)


def test_unvalidated_contracts_have_zero_click_budget_and_entries_only_where_real_evidence_exists():
    with_entry = {"SIRE_GENERACION_REGISTROS"}
    for destination in NOT_VALIDATED_IDS:
        contract = dc.CONTRACTS[destination]
        assert not contract.enabled and contract.max_clicks == 0
        assert (contract.entry is not None) == (destination in with_entry), destination


def test_click_budget_is_derived_from_the_validated_sequence():
    assert dc.CONTRACTS["MENU_SOL"].max_clicks == 0
    assert dc.CONTRACTS["FICHA_RUC"].max_clicks == dc.FICHA_MAX_NAV_CLICKS == 4  # 3 cabeceras + la hoja
    leaf = dc.DestinationContract("X", "X", dc.LIVE_VALIDATED, entry=dc.EntrySpec("MENU_LEAF", "x", leaf_level=3, min_levels=1, max_levels=2),
                                  arrival=dc.ArrivalSpec("NEW_PAGE", frozenset({"h"}), "/p/"))
    home = dc.DestinationContract("Y", "Y", dc.LIVE_VALIDATED, entry=dc.EntrySpec("HOME_LINK", "y"), arrival=dc.ArrivalSpec("NEW_PAGE", frozenset({"h"}), "/p/"))
    disabled = dc.DestinationContract("Z", "Z", dc.NOT_VALIDATED, entry=dc.EntrySpec("HOME_LINK", "z"), arrival=dc.ArrivalSpec("NEW_PAGE", frozenset({"h"}), "/p/"))
    assert (leaf.max_clicks, home.max_clicks, disabled.max_clicks) == (3, 1, 0)  # un contrato NO validado jamas tiene presupuesto


def test_request_schema_accepts_only_known_ids_and_never_urls_or_selectors():
    base = {"client_id": "00000000-0000-4000-8000-000000000001", "idempotency_key": "00000000-0000-4000-8000-000000000002"}
    assert CreateSolAccessSessionRequest(**base, destination="SIRE_RVIE").destination == "SIRE_RVIE"
    for bad in ("https://e-menu.sunat.gob.pe/x", "BUZON_SUNAT", "sire", "li.nivel4 > span"):
        with pytest.raises(Exception):
            CreateSolAccessSessionRequest(**base, destination=bad)
    with pytest.raises(Exception):
        CreateSolAccessSessionRequest(**base, destination="SIRE", url="https://x")  # extra=forbid


def _info(tag="div", visible=True, name_text="", classes=(), role=None):
    return ElementInfo(tag=tag, type=None, placeholder=None, maxlength=None, visible=visible, classes=tuple(classes), role=role, name_text=name_text)


def _shell(content="APPLICATION", crumb="mi destino", extra_menu_visible=False):
    snap = {}
    for element_id, tag in dc.SHELL_REQUIRED_TAGS.items():
        snap[element_id] = (_info(tag, True, classes=(dc.SHELL_HEADER_CLASS,), role=dc.SHELL_HEADER_ROLE) if element_id == "divCabecera" else _info(tag),)
    app_visible = content == "APPLICATION"
    snap["iDivApplication"] = (_info("div", app_visible),)
    snap["divContainerMenu"] = (_info("div", extra_menu_visible or not app_visible),)
    snap["iframeApplication"] = (_info("iframe", True),)
    snap["liBreadNivel4"] = (_info("li", True, name_text=crumb),)
    return LoginSnapshot(elements=snap)


ARRIVAL = dc.ArrivalSpec("FRAME", frozenset({"app.example"}), "/mod/", "https", ("idA",), 2, "mi destino")


def test_application_top_requires_shell_frame_hidden_menu_and_exact_breadcrumb():
    assert dc.check_application_top(_shell(), ARRIVAL).ok
    assert not dc.check_application_top(_shell(crumb="otro destino"), ARRIVAL).ok  # miga distinta
    assert not dc.check_application_top(_shell(extra_menu_visible=True), ARRIVAL).ok  # menu aun visible
    assert not dc.check_application_top(_shell(content="MENU"), ARRIVAL).ok  # no hay region de aplicacion
    assert dc.check_application_top(_shell(crumb="loquesea"), dc.ArrivalSpec("FRAME", frozenset({"h"}), "/p/")).ok  # sin miga declarada


def test_arrival_frame_and_page_identity_positive_and_negative():
    ok = dc.FichaFrameView("https://app.example/mod/x", {"idA": 1}, 3)
    assert dc.check_arrival_frame(ok, ARRIVAL).ok
    for bad in (
        dc.FichaFrameView("http://app.example/mod/x", {"idA": 1}, 3),  # esquema
        dc.FichaFrameView("https://evil.example/mod/x", {"idA": 1}, 3),  # host
        dc.FichaFrameView("https://app.example/otra/x", {"idA": 1}, 3),  # ruta
        dc.FichaFrameView("https://app.example/mod/x", {"idA": 2}, 3),  # marcador duplicado
        dc.FichaFrameView("https://app.example/mod/x", {}, 3),  # marcador ausente
        dc.FichaFrameView("https://app.example/mod/x", {"idA": 1}, 1),  # pocas tablas
    ):
        assert not dc.check_arrival_frame(bad, ARRIVAL).ok
    page = dc.ArrivalSpec("NEW_PAGE", frozenset({"app.example"}), "/mod/", "https", ("idA",))
    assert dc.check_arrival_page("https://app.example/mod/home", {"idA": 1}, page).ok
    assert not dc.check_arrival_page("https://app.example/mod/home", {"idA": 0}, page).ok
    assert not dc.check_arrival_page("https://other.example/mod/home", {"idA": 1}, page).ok


def test_executor_has_no_code_path_to_upload_generate_submit_or_download():
    """Guardia estatica: el motor de destinos solo navega por el menu y verifica identidad; no tiene ruta de codigo para cargar archivos,
    descargar, ni pulsar los controles de las aplicaciones SIRE (`btn-aceptar`, pestanas de generacion/gestion) cuyos ids solo se LEEN."""
    from pathlib import Path

    source = (Path(dc.__file__).parent / "destination_executor.py").read_text(encoding="utf-8")
    for forbidden in ("set_input_files", "expect_download", "expect_file_chooser", "btn-aceptar", "nav-generacion-registro", "nav-informacion-gestion", "buttonPagarBandeja", "btnBuzon"):
        assert forbidden not in source, forbidden
    contract_source = Path(dc.__file__).read_text(encoding="utf-8")
    # los ids de las aplicaciones aparecen SOLO como marcadores de identidad (tupla de ids unicos), nunca como destino de un clic
    code = contract_source.split("from __future__ import annotations", 1)[1]  # fuera del docstring del modulo
    for line in code.splitlines():
        if "btn-aceptar" in line:
            assert "SIRE_APP_ONCE_IDS" in line, line  # solo la tupla de ids de identidad


def test_open_only_destination_budget_never_exceeds_the_initial_screen():
    contract = dc.CONTRACTS["SIRE_GENERACION_REGISTROS"]
    assert contract.open_only and contract.human_control
    # aun si algun dia se valida, el presupuesto es el de la secuencia de menu hasta la pantalla inicial: nunca clics dentro de la aplicacion
    hypothetical = dc.DestinationContract(
        contract.id, contract.label, dc.LIVE_VALIDATED, entry=dc.EntrySpec("MENU_LEAF", "x", leaf_level=4, min_levels=1, max_levels=3),
        arrival=dc.ArrivalSpec("FRAME", dc.SIRE_APP_HOSTS, "/app/", "https", dc.SIRE_APP_ONCE_IDS), open_only=True,
    )
    assert hypothetical.max_clicks == 4  # 3 cabeceras + la hoja; el motor no tiene ningun paso posterior a la llegada
