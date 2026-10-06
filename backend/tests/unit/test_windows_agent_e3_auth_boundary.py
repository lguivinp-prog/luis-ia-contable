"""E3: frontera de credencial y orquestacion (`run_authentication`) con un
ejecutor FALSO (sin navegador), mas auditoria de alcance del codigo
productivo del navegador."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import windows_agent.browser_executor as browser_executor
from windows_agent.broker_client import BrokerProtocolError, CredentialFetchError, CredentialMaterial
from windows_agent.browser_executor import make_result, run_authentication
from windows_agent.portal_contract import AuthOutcome, AuthResult

SECRET = "SENTINEL-PASSWORD-e3-1a2b"


class FakeExecutor:
    def __init__(self, *, open_result=None, submit_result=None, outcome=AuthOutcome.AUTH_SUCCEEDED,
                 cancelled=False, clean=True):
        self.open_result, self.submit_result, self.outcome = open_result, submit_result, outcome
        self._cancelled, self._clean = cancelled, clean
        self.calls: list[str] = []
        self.credential_submissions = 0
        self.post_landing_clicks = 0

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def open_and_identify_login(self):
        self.calls.append("open")
        return self.open_result

    def submit_credentials(self, credential):
        self.calls.append("submit")
        credential._clear()
        self.credential_submissions = 1
        return self.submit_result

    def await_outcome(self):
        self.calls.append("await")
        return make_result(self.outcome, submissions=1)

    def close(self):
        self.calls.append("close")
        return self._clean


class Provider:
    def __init__(self, error=None):
        self.entered = 0
        self.error = error
        self.material = None

    def __call__(self):
        return self

    def __enter__(self):
        self.entered += 1
        if self.error:
            raise self.error
        self.material = CredentialMaterial(ruc="R", sol_username="U", sol_password=SECRET)
        return self.material

    def __exit__(self, *exc):
        self.material._clear()


def test_credential_is_fetched_once_and_only_after_login_is_identified():
    ex, provider = FakeExecutor(), Provider()
    result = run_authentication(ex, provider)
    assert result.outcome is AuthOutcome.AUTH_SUCCEEDED and result.credential_submissions == 1
    assert provider.entered == 1
    assert ex.calls == ["open", "submit", "await", "close"]  # la credencial se usa entre open y await
    assert provider.material.sol_password is None  # referencias soltadas


@pytest.mark.parametrize("early", [AuthOutcome.PORTAL_CHANGED, AuthOutcome.TRANSIENT_NAVIGATION_FAILURE,
                                   AuthOutcome.BROWSER_FAILURE])
def test_failure_before_login_never_fetches_the_credential(early):
    ex, provider = FakeExecutor(open_result=make_result(early)), Provider()
    result = run_authentication(ex, provider)
    assert result.outcome is early
    assert provider.entered == 0 and "submit" not in ex.calls and ex.calls[-1] == "close"


def test_cancel_before_secret_delivery_never_fetches():
    ex, provider = FakeExecutor(cancelled=True), Provider()
    result = run_authentication(ex, provider)
    assert result.outcome is AuthOutcome.CANCELLED and provider.entered == 0


@pytest.mark.parametrize("error", [CredentialFetchError("CREDENTIAL_UNAVAILABLE"), BrokerProtocolError("x")])
def test_delivery_failure_is_terminal_and_never_retried(error):
    ex, provider = FakeExecutor(), Provider(error=error)
    result = run_authentication(ex, provider)
    assert result.outcome is AuthOutcome.BROWSER_FAILURE and result.terminal_reason_code == "CREDENTIAL_UNAVAILABLE"
    assert provider.entered == 1 and "submit" not in ex.calls


def test_cleanup_failure_downgrades_success_but_not_other_outcomes():
    ok = run_authentication(FakeExecutor(clean=False), Provider())
    assert ok.outcome is AuthOutcome.BROWSER_FAILURE and ok.terminal_reason_code == "CLEANUP_FAILED"
    inv = run_authentication(FakeExecutor(outcome=AuthOutcome.INVALID_CREDENTIALS, clean=False), Provider())
    assert inv.outcome is AuthOutcome.INVALID_CREDENTIALS


def test_executor_always_closed_even_if_orchestration_raises():
    class Boom(FakeExecutor):
        def await_outcome(self):
            raise RuntimeError("boom")

    ex = Boom()
    with pytest.raises(RuntimeError):
        run_authentication(ex, Provider())
    assert ex.calls[-1] == "close"


def test_auth_result_and_exceptions_never_carry_secrets():
    result = run_authentication(FakeExecutor(), Provider())
    assert SECRET not in repr(result) and SECRET not in str(result)
    assert SECRET not in repr(CredentialMaterial(ruc="R", sol_username="U", sol_password=SECRET))
    assert isinstance(result, AuthResult)


def test_executor_refuses_a_second_submission():
    from windows_agent.browser_executor import SupervisedAuthExecutor
    from windows_agent.portal_contract import SOL_PORTAL_CONTRACT

    ex = SupervisedAuthExecutor(SOL_PORTAL_CONTRACT)
    ex._submissions = 1
    material = CredentialMaterial(ruc="R", sol_username="U", sol_password=SECRET)
    result = ex.submit_credentials(material)
    assert result.outcome is AuthOutcome.BROWSER_FAILURE and result.detail == "second submission refused"
    assert material.sol_password is None  # tambien se sueltan en el rechazo
    assert ex.credential_submissions == 1


# --- auditoria de alcance del codigo productivo (AST/regex sobre el fuente) ---

_PRODUCT_FILES = ["browser_executor.py", "portal_contract.py", "portal_identity.py"]


def _source() -> str:
    root = Path(browser_executor.__file__).parent
    return "\n".join((root / name).read_text(encoding="utf-8") for name in _PRODUCT_FILES)


def _code_only(src: str) -> str:
    """Quita comentarios y docstrings para auditar solo codigo ejecutable."""
    import ast

    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Module)) and node.body and isinstance(
            node.body[0], ast.Expr
        ) and isinstance(getattr(node.body[0], "value", None), ast.Constant) and isinstance(node.body[0].value.value, str):
            node.body[0].value.value = ""
    return ast.unparse(tree)


@pytest.mark.parametrize(
    "forbidden",
    [r"storage_state", r"launch_persistent_context", r"user_data_dir", r"\.screenshot\(", r"\.pdf\(",
     r"start_tracing|tracing\.", r"record_video", r"accept_downloads=True", r"expect_download|\.on\(.download",
     r"headless=True", r"\.route\(", r"add_cookies|\.cookies\(", r"\.mouse\.", r"keyboard\."],
)
def test_product_browser_code_has_no_persistence_capture_or_free_form_interaction(forbidden):
    code = _code_only(_source())
    assert re.search(forbidden, code) is None, forbidden


def test_product_browser_code_has_only_the_three_authorised_clicks_and_one_goto():
    code = _code_only(_source())
    assert code.count(".goto(") == 1  # unica navegacion directa: la entrada autorizada
    assert code.count(".click(") == 3  # enlace de entrada, modo RUC, enviar (nunca post-landing)


def test_product_code_never_references_buzon_or_other_forbidden_scope():
    code = _code_only(_source()).lower()
    for term in ("buzon", "buzón", "declaraci", "rectific", "pago", "mensaje"):
        assert term not in code, term


def test_runtime_wires_no_credential_fetch_into_poll_and_claim():
    """E2 sigue intacto: reclamar un job NO solicita credencial; E3 la pide solo desde
    `authenticate_claimed_job` y solo si hay fabrica de ejecutor."""
    import inspect

    from windows_agent.runtime import AgentRuntime

    assert "fetch_credential" not in inspect.getsource(AgentRuntime.poll_and_claim_once)


def test_cli_run_never_enables_browser_authentication_by_default():
    """Sin `--enable-sol-auth` el CLI no cablea ejecutor de navegador (el agente nunca toca el portal)."""
    from windows_agent.__main__ import build_parser

    parser = build_parser()
    assert parser.parse_args(["run"]).enable_sol_auth is False
    assert parser.parse_args(["run", "--enable-sol-auth"]).enable_sol_auth is True
    assert not any("password" in a.dest or "secret" in a.dest for a in parser._actions)  # sin argumentos secretos
