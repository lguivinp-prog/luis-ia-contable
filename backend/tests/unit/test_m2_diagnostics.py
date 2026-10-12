"""M2: diagnostico estructural saneado (modulo puro). Etapa y codigo estables; lista cerrada replicada en el backend."""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from app.broker.protocol import DIAGNOSTIC_CODES as BACKEND_CODES
from windows_agent import diagnostics as d
from windows_agent.portal_contract import AuthOutcome, AuthResult

AGENT_DIR = pathlib.Path(d.__file__).parent


def test_the_closed_code_list_is_identical_in_agent_and_backend():
    assert d.DIAGNOSTIC_CODES == BACKEND_CODES
    assert all(re.fullmatch(r"[A-Z0-9_]{3,48}", c) for c in d.DIAGNOSTIC_CODES)


def _literal_details() -> set[str]:
    """Todas las frases fijas con las que el agente rechaza un portal (`detail=` / `_fail(` / ShellCheck(False, ...))."""
    found: set[str] = set()
    for name in ("browser_executor.py", "destination_executor.py", "portal_identity.py", "destination_contract.py"):
        source = (AGENT_DIR / name).read_text(encoding="utf-8")
        for pattern in (r'detail="([^"]+)"', r'_fail\(\s*"([^"]+)"', r'ShellCheck\(False,\s*f?"([^"]+)"', r'return "([a-z][^"]{8,80})"', r'_fail\("([^"]+)"'):
            found.update(re.findall(pattern, source))
    return {x for x in found if " " in x}


def test_every_fixed_rejection_phrase_has_a_stable_non_generic_code():
    unclassified = []
    for phrase in sorted(_literal_details()):
        # las frases con parte variable (`{element_id}`) se instancian con un valor de ejemplo
        sample = re.sub(r"\{[^}]+\}", "x", phrase)
        stage, code = d.classify(sample, AuthOutcome.PORTAL_CHANGED)
        if code in {"UNCLASSIFIED_PORTAL_CHANGE", "UNEXPECTED_ERROR"}:
            unclassified.append(phrase)
    assert unclassified == [], unclassified


@pytest.mark.parametrize(
    ("detail", "outcome", "expected"),
    [
        ("entry link not found", AuthOutcome.PORTAL_CHANGED, ("ENTRY", "ENTRY_LINK_NOT_FOUND")),
        ("landing before authentication", AuthOutcome.PORTAL_CHANGED, ("LOGIN_SURFACE", "LANDING_BEFORE_AUTHENTICATION")),
        ("landing transit did not resolve to a login form", AuthOutcome.PORTAL_CHANGED, ("LOGIN_SURFACE", "LANDING_TRANSIT_UNRESOLVED")),
        ("login control 'txtRuc' missing, ambiguous or structurally different", AuthOutcome.PORTAL_CHANGED, ("LOGIN_FORM", "LOGIN_CONTROL_MISMATCH")),
        ("login page path does not match the expected pattern", AuthOutcome.PORTAL_CHANGED, ("LOGIN_SURFACE", "LOGIN_PATH_MISMATCH")),
        ("page outside the known portal surface", AuthOutcome.PORTAL_CHANGED, ("LOGIN_SURFACE", "UNKNOWN_PAGE_OR_HOST")),
        ("shell marker 'ulMenu01' missing or ambiguous", AuthOutcome.PORTAL_CHANGED, ("SHELL", "SHELL_MARKER_MISSING_OR_AMBIGUOUS")),
        ("navigation blocked by an intermediate screen", AuthOutcome.PORTAL_CHANGED, ("NAVIGATION", "BLOCKED_BY_INTERMEDIATE_SCREEN")),
        ("ficha frame identity not validated", AuthOutcome.PORTAL_CHANGED, ("DESTINATION", "FICHA_FRAME_NOT_VALIDATED")),
        ("ficha extraction did not identify the taxpayer", AuthOutcome.PORTAL_CHANGED, ("EXTRACTION", "FICHA_TAXPAYER_NOT_IDENTIFIED")),
        ("ficha top something else", AuthOutcome.PORTAL_CHANGED, ("DESTINATION", "FICHA_DESTINATION_MISMATCH")),
        (None, AuthOutcome.BROWSER_FAILURE, ("BROWSER", "BROWSER_CLOSED_OR_FAILED")),
        ("texto desconocido con datos 20123456786", AuthOutcome.PORTAL_CHANGED, ("UNKNOWN", "UNCLASSIFIED_PORTAL_CHANGE")),
    ],
)
def test_classification_is_by_stage_and_never_echoes_free_text(detail, outcome, expected):
    assert d.classify(detail, outcome) == expected


def test_safe_detail_drops_unexpected_characters_and_limits_length():
    assert d.safe_detail("login control 'txtRuc' <script>alert(1)</script>\n\t") == "login control 'txtRuc' scriptalert(1)script"
    assert len(d.safe_detail("a" * 500)) == 120 and d.safe_detail(None) == ""


def test_annotate_adds_stage_code_and_observation_only_to_failures():
    failure = AuthResult(AuthOutcome.PORTAL_CHANGED, terminal_reason_code="PORTAL_CHANGED", detail="entry link not found")
    annotated = d.annotate(failure, observation={"page_count": 1}, last_action="ENTRY_LOADED")
    assert (annotated.stage, annotated.diagnostic_code) == ("ENTRY", "ENTRY_LINK_NOT_FOUND")
    assert annotated.observation == {"page_count": 1, "last_safe_action": "ENTRY_LOADED"}
    ok = AuthResult(AuthOutcome.AUTH_SUCCEEDED)
    assert d.annotate(ok, observation={"x": 1}).diagnostic_code is None
    assert d.annotate(AuthResult(AuthOutcome.CANCELLED), observation={"x": 1}).observation is None


def test_page_observation_has_classes_and_booleans_but_no_urls_titles_or_paths():
    from windows_agent.portal_contract import PortalContract  # noqa: F401 -- tipo del contrato
    from windows_agent.portal_identity import PageView, _host
    from tests.unit.test_windows_agent_e3_portal_identity import C

    views = (
        PageView(url="https://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm?ruc=20123456786", title="SUNAT - Menú SOL"),
        PageView(url="https://e-menu.sunat.gob.pe/aviso/telefono-999888777", title="Confirme su teléfono 999888777", frame_urls=("https://x/y",)),
        PageView(url="https://evil.example.com/x", title="x"),
    )
    pages = d.observe_pages(views, C, _host)
    blob = json.dumps(pages)
    for secret in ("20123456786", "999888777", "evil.example.com", "Confirme", "MenuInternet", "http"):
        assert secret not in blob, secret
    assert [p["class"] for p in pages] == ["landing", "landing", "unknown"]
    assert pages[0]["landing_path_prefix_ok"] is True and pages[1]["frames"] == 1 and pages[2]["scheme_ok"] is True


def test_auth_navigation_stalled_is_a_stable_diagnostic_and_not_a_human_wait():
    from windows_agent import diagnostics as d
    from windows_agent.portal_contract import AuthOutcome

    assert d.classify("auth navigation stalled", AuthOutcome.PORTAL_CHANGED) == ("SUBMIT", "AUTH_NAVIGATION_STALLED")
    assert "AUTH_NAVIGATION_STALLED" in d.DIAGNOSTIC_CODES


def test_auth_page_after_submit_keeps_its_login_identity_flag_and_challenge_only_when_positive():
    from windows_agent.portal_contract import SOL_PORTAL_CONTRACT as c
    from windows_agent.portal_identity import PageView, SurfaceKind, classify_surface

    host = next(iter(c.auth_hosts))
    login = f"https://{host}/v1/clientessol/abc/oauth2/loginMenuSol"
    welcome = f"https://{host}/v1/clientessol/abc/oauth2/otraRuta"
    s = classify_surface((PageView(login, "Menú SOL", challenge_visible=False),), c, strict_auth_identity=False)
    assert s.kind is SurfaceKind.LOGIN and s.auth_login_identity and not s.human_challenge
    s = classify_surface((PageView(welcome, "Bienvenidos a SUNAT"),), c, strict_auth_identity=False)
    assert s.kind is SurfaceKind.LOGIN and not s.auth_login_identity and not s.human_challenge
    s = classify_surface((PageView(welcome, "Bienvenidos a SUNAT", challenge_visible=True),), c, strict_auth_identity=False)
    assert s.human_challenge
