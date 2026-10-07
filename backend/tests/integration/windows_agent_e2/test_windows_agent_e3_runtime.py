"""E3: integracion del runtime del agente con el broker REAL (PostgreSQL real +
mTLS real + entrega de credencial E1-B real) usando un ejecutor de navegador
FALSO -- el navegador real se valida aparte contra el portal local. Verifica:
credencial pedida por el endpoint existente y de un solo uso (sin redelivery),
reporte por los contratos existentes, cancelacion que interrumpe y evita la
entrega, y que reclamar un job sin fabrica de ejecutor sigue sin pedir secreto."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.broker.dispatch import dispatch_job_to_agent
from app.core.sol_vault_registry import vault_unlock_registry
from app.models.sol_agent_access import SolAgentJob
from app.services import sol_access_session_service as session_service
from app.services import sol_agent_credential_delivery_service as delivery_service
from tests.integration.broker._secret_delivery_helpers import (
    TEST_SOL_PASSWORD_DO_NOT_USE,
    TEST_SOL_USER,
    get_user_row,
)
from tests.integration.broker.conftest import cleanup_tenant, commit_tenant_user
from tests.integration.windows_agent_e2.test_windows_agent_hygiene_and_scope import (
    _ca_pem,
    _config_for,
    _full_real_session,
    _pair_real_agent,
)
from windows_agent.broker_client import OperationalBrokerClient
from windows_agent.browser_executor import make_result
from windows_agent.portal_contract import AuthOutcome
from windows_agent.runtime import AgentRuntime


class StubExecutor:
    """Ejecutor sin navegador: consume la credencial REAL entregada por el broker."""

    def __init__(self, *, is_cancelled, tick, on_state, outcome=AuthOutcome.AUTH_SUCCEEDED, record=None):
        self._is_cancelled, self._tick, self._outcome, self.record = is_cancelled, tick, outcome, record
        self.credential_submissions = 0
        self.post_landing_clicks = 0

    @property
    def is_cancelled(self) -> bool:
        return bool(self._is_cancelled())

    def open_and_identify_login(self):
        self._tick()  # el runtime envia un heartbeat (entrega `cancel_notice`)
        return None

    def submit_credentials(self, credential):
        self._tick()
        if self.is_cancelled:
            credential._clear()
            return make_result(AuthOutcome.CANCELLED)
        self.record["user"], self.record["password"] = credential.sol_username, credential.sol_password
        credential._clear()
        self.credential_submissions = 1
        return None

    def await_outcome(self):
        return make_result(self._outcome, submissions=1)

    def close(self) -> bool:
        return True


def _setup(session_factory, running_broker, tmp_path, monkeypatch, factory):
    setup = commit_tenant_user(session_factory)
    store = _pair_real_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"], tmp_path)
    identity = store.load()
    ca_pem = _ca_pem(running_broker)
    runtime = AgentRuntime(
        identity=identity,
        config=_config_for(running_broker),
        ssl_context_factory=lambda: store.build_client_ssl_context(ca_certificate_pem=ca_pem),
        auth_executor_factory=factory,
    )
    runtime.connect()
    extra = _full_real_session(session_factory, monkeypatch, setup)
    ds = session_factory()
    try:
        job_id = dispatch_job_to_agent(
            ds, tenant_id=setup["tenant_id"], session_id=extra["session_id"], agent_id=identity.agent_id,
            pending_dispatch=running_broker.pending_dispatch,
        )
        ds.commit()
    finally:
        ds.close()
    assert runtime.poll_and_claim_once() == job_id
    return setup, runtime, extra, job_id


def _authorize(session_factory, running_broker, setup, extra, job_id):
    delivery_service.authorize_credential_delivery(
        session_factory, grant_registry=running_broker.secret_delivery_grants, vault_registry=vault_unlock_registry,
        tenant_id=setup["tenant_id"], session_id=extra["session_id"], job_id=job_id,
        actor=get_user_row(session_factory, setup["admin_id"]), app_session_id=extra["app_session_id"],
    )


def _job_status(session_factory, job_id) -> str:
    s = session_factory()
    try:
        return s.execute(select(SolAgentJob.status).where(SolAgentJob.id == job_id)).scalar_one()
    finally:
        s.close()


def test_E3_credential_via_existing_contract_one_use_and_job_reported_completed(
    session_factory, running_broker, tmp_path, monkeypatch
):
    record: dict = {}
    factory = lambda **hooks: StubExecutor(record=record, **hooks)  # noqa: E731
    setup, runtime, extra, job_id = _setup(session_factory, running_broker, tmp_path, monkeypatch, factory)
    try:
        _authorize(session_factory, running_broker, setup, extra, job_id)
        result = runtime.authenticate_claimed_job(job_id, heartbeat_every_seconds=0)
        assert result.outcome is AuthOutcome.AUTH_SUCCEEDED and result.credential_submissions == 1
        # credencial real, entregada por el endpoint E1-B (sentinelas de prueba)
        assert record == {"user": TEST_SOL_USER, "password": TEST_SOL_PASSWORD_DO_NOT_USE}
        assert _job_status(session_factory, job_id) == "COMPLETED"  # reportado por el contrato existente

        # SIN redelivery: un segundo intento no obtiene el secreto
        record.clear()
        again = runtime.authenticate_claimed_job(job_id, heartbeat_every_seconds=0)
        assert again.outcome is AuthOutcome.BROWSER_FAILURE and again.terminal_reason_code == "CREDENTIAL_UNAVAILABLE"
        assert record == {}
        runtime.stop()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_E3_failed_authentication_is_reported_as_failed_job(session_factory, running_broker, tmp_path, monkeypatch):
    factory = lambda **hooks: StubExecutor(  # noqa: E731
        record={}, outcome=AuthOutcome.INVALID_CREDENTIALS, **hooks
    )
    setup, runtime, extra, job_id = _setup(session_factory, running_broker, tmp_path, monkeypatch, factory)
    try:
        _authorize(session_factory, running_broker, setup, extra, job_id)
        result = runtime.authenticate_claimed_job(job_id, heartbeat_every_seconds=0)
        assert result.outcome is AuthOutcome.INVALID_CREDENTIALS and result.terminal_reason_code == "AUTH_FAILED"
        assert _job_status(session_factory, job_id) == "FAILED"
        runtime.stop()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_E3_cancellation_interrupts_before_secret_fetch_and_reports_nothing(
    session_factory, running_broker, tmp_path, monkeypatch
):
    record: dict = {}
    factory = lambda **hooks: StubExecutor(record=record, **hooks)  # noqa: E731
    setup, runtime, extra, job_id = _setup(session_factory, running_broker, tmp_path, monkeypatch, factory)
    try:
        _authorize(session_factory, running_broker, setup, extra, job_id)
        cs = session_factory()
        try:
            session_service.cancel_session(
                cs, tenant_id=setup["tenant_id"], session_id=extra["session_id"], actor_user_id=setup["admin_id"]
            )
            cs.commit()
        finally:
            cs.close()
        with patch.object(OperationalBrokerClient, "_request_credential_once") as credential_spy:
            result = runtime.authenticate_claimed_job(job_id, heartbeat_every_seconds=0)
            credential_spy.assert_not_called()  # E3_CANCEL_AFTER... nunca pide ni re-entrega el secreto
        assert result.outcome is AuthOutcome.CANCELLED and result.terminal_reason_code == "CANCELLED_BY_USER"
        assert record == {}
        assert _job_status(session_factory, job_id) == "CANCELLED"  # el agente no lo sobrescribe
        runtime.stop()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_E3_claiming_without_executor_factory_still_never_fetches_a_credential(
    session_factory, running_broker, tmp_path, monkeypatch
):
    setup, runtime, _extra, _job_id = _setup(session_factory, running_broker, tmp_path, monkeypatch, None)
    try:
        with patch.object(OperationalBrokerClient, "_request_credential_once") as credential_spy:
            runtime.heartbeat_once()
            credential_spy.assert_not_called()
        with pytest.raises(AssertionError):
            runtime.authenticate_claimed_job(_job_id)  # sin fabrica: no hay ruta de autenticacion
        runtime.stop()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


# --- Domain F: autorizacion JUST-IN-TIME tras PREAUTH_READY (sin `_authorize` previo) -----------------------------


def test_E3_jit_authorization_after_preauth_ready_delivers_the_credential_once(
    session_factory, running_broker, tmp_path, monkeypatch
):
    record: dict = {}
    factory = lambda **hooks: StubExecutor(record=record, **hooks)  # noqa: E731
    setup, runtime, extra, job_id = _setup(session_factory, running_broker, tmp_path, monkeypatch, factory)
    try:
        # contexto humano que la API registra al iniciar la sesion (user + sesion de aplicacion); SIN grant previo
        running_broker.session_authorizations.register(
            tenant_id=setup["tenant_id"], session_id=extra["session_id"], user_id=setup["admin_id"],
            app_session_id=extra["app_session_id"],
        )
        assert running_broker.secret_delivery_grants.peek(tenant_id=setup["tenant_id"], job_id=job_id) is None
        result = runtime.authenticate_claimed_job(job_id, heartbeat_every_seconds=0)
        assert result.outcome is AuthOutcome.AUTH_SUCCEEDED
        assert record == {"user": TEST_SOL_USER, "password": TEST_SOL_PASSWORD_DO_NOT_USE}
        assert _job_status(session_factory, job_id) == "COMPLETED"
        runtime.stop()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_E3_preauth_rejected_without_human_context_never_requests_the_credential(
    session_factory, running_broker, tmp_path, monkeypatch
):
    record: dict = {}
    factory = lambda **hooks: StubExecutor(record=record, **hooks)  # noqa: E731
    setup, runtime, extra, job_id = _setup(session_factory, running_broker, tmp_path, monkeypatch, factory)
    try:
        with patch.object(OperationalBrokerClient, "_request_credential_once") as credential_spy:
            result = runtime.authenticate_claimed_job(job_id, heartbeat_every_seconds=0)
            credential_spy.assert_not_called()
        assert result.outcome is AuthOutcome.BROWSER_FAILURE and result.terminal_reason_code == "CREDENTIAL_UNAVAILABLE"
        assert record == {}
        assert running_broker.secret_delivery_grants.peek(tenant_id=setup["tenant_id"], job_id=job_id) is None
        assert _job_status(session_factory, job_id) == "FAILED"
        runtime.stop()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])
