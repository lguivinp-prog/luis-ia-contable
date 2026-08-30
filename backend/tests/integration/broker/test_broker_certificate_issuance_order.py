"""Fase 5, Bloque 5.2, Domain D.1 -- Gate 1: orden de emision de
certificado. La firma criptografica real con la CA (`ca.key.sign(...)`,
invocada internamente por `x509.CertificateBuilder.sign`) NUNCA debe
ocurrir para un pairing invalido/expirado/LOCKED/limitado-por-throttle --
solo despues de que el codigo fue verificado como correcto. Se mide la
invocacion REAL de la primitiva de firma (nunca solo si la funcion
`sign_agent_csr`/`_issue_certificate` fue llamada) parcheando
`ca.key.sign` con un espia que envuelve la implementacion real."""

from __future__ import annotations

import threading
from unittest.mock import patch

from tests.integration.broker.agent_test_client import AgentTestClient
from tests.integration.broker.conftest import (
    cleanup_tenant,
    commit_tenant_user,
    create_pairing_over_service,
)


def _agent(running_broker) -> AgentTestClient:
    from cryptography.hazmat.primitives import serialization

    return AgentTestClient(
        ca_certificate_pem=running_broker.ca.certificate.public_bytes(serialization.Encoding.PEM),
        pairing_base_url=f"https://127.0.0.1:{running_broker.pairing_bound_port}",
        operational_base_url=f"https://127.0.0.1:{running_broker.operational_bound_port}",
    )


def _sign_spy(running_broker):
    """Espia sobre `app.broker.pki._perform_ca_signature` -- el UNICO punto
    del modulo PKI que ejecuta la primitiva de firma real (ver el docstring
    de esa funcion). Envuelve (nunca reemplaza) la implementacion real, asi
    que una firma exitosa sigue produciendo un certificado valido de
    verdad. No se puede parchear `ca.key.sign` directamente: es un atributo
    de solo-lectura de un objeto nativo respaldado por Rust."""
    import app.broker.pki as pki_module

    return patch.object(pki_module, "_perform_ca_signature", wraps=pki_module._perform_ca_signature)


def test_A_wrong_pairing_code_never_signs(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    try:
        pairing_id, _real_code = create_pairing_over_service(
            session_factory, tenant_id=setup["tenant_id"], requested_by_user_id=setup["admin_id"]
        )
        agent = _agent(running_broker)
        with _sign_spy(running_broker) as spy:
            response = agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code="000000")
        assert response.status_code == 422
        assert spy.call_count == 0
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_B_expired_pairing_never_signs(session_factory, running_broker):
    from app.models.sol_agent_access import SolAgentPairing

    setup = commit_tenant_user(session_factory)
    try:
        pairing_id, pairing_code = create_pairing_over_service(
            session_factory, tenant_id=setup["tenant_id"], requested_by_user_id=setup["admin_id"]
        )
        session = session_factory()
        try:
            row = session.get(SolAgentPairing, pairing_id)
            from datetime import UTC, datetime, timedelta

            row.created_at = datetime.now(UTC) - timedelta(seconds=300)
            row.expires_at = datetime.now(UTC) - timedelta(seconds=180)
            session.commit()
        finally:
            session.close()

        agent = _agent(running_broker)
        with _sign_spy(running_broker) as spy:
            response = agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code)
        assert response.status_code == 422
        assert spy.call_count == 0
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_C_locked_pairing_never_signs(session_factory, running_broker):
    from app.models.sol_agent_access import SolAgentPairing

    setup = commit_tenant_user(session_factory)
    try:
        pairing_id, pairing_code = create_pairing_over_service(
            session_factory, tenant_id=setup["tenant_id"], requested_by_user_id=setup["admin_id"]
        )
        session = session_factory()
        try:
            row = session.get(SolAgentPairing, pairing_id)
            from datetime import UTC, datetime

            row.status = "LOCKED"
            row.locked_at = datetime.now(UTC)
            row.pairing_code_verifier = None
            session.commit()
        finally:
            session.close()

        agent = _agent(running_broker)
        with _sign_spy(running_broker) as spy:
            response = agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code)
        assert response.status_code == 422
        assert spy.call_count == 0
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_D_verification_rate_limited_never_signs(session_factory, running_broker, monkeypatch):
    from app.core.config import get_settings

    setup = commit_tenant_user(session_factory)
    try:
        pairing_id, pairing_code = create_pairing_over_service(
            session_factory, tenant_id=setup["tenant_id"], requested_by_user_id=setup["admin_id"]
        )
        # Cooldown alto para forzar throttle en el segundo intento -- el
        # primer intento (codigo incorrecto, a proposito) consume el
        # throttle sin llegar a firmar; el segundo intento (codigo
        # correcto) debe ser rechazado por el throttle, todavia sin firmar.
        monkeypatch.setattr(get_settings(), "pairing_verification_throttle_seconds", 120.0, raising=False)

        agent_wrong = _agent(running_broker)
        first = agent_wrong.submit_pairing_csr(pairing_id=pairing_id, pairing_code="000000")
        assert first.status_code == 422

        agent_correct = _agent(running_broker)
        with _sign_spy(running_broker) as spy:
            second = agent_correct.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code)
        assert second.status_code == 422
        assert second.json()["error"] in ("CERTIFICATE_INVALID",)
        assert spy.call_count == 0
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_E_invalid_csr_never_completes_a_real_signature(session_factory, running_broker):
    """CSR con firma invalida: `pki.sign_agent_csr` valida ANTES de invocar
    la primitiva real de firma de la CA -- una firma de CSR corrupta se
    rechaza sin que `ca.key.sign` llegue a ejecutarse ni una sola vez."""
    setup = commit_tenant_user(session_factory)
    try:
        pairing_id, pairing_code = create_pairing_over_service(
            session_factory, tenant_id=setup["tenant_id"], requested_by_user_id=setup["admin_id"]
        )
        agent = _agent(running_broker)
        agent.generate_key_and_csr()
        corrupted = bytearray(agent.raw_csr_pem())
        for i, byte in enumerate(corrupted):
            if byte not in (0x0A, 0x2D) and 40 < i < len(corrupted) - 40:
                corrupted[i] = (byte + 1) % 256
                break
        agent._csr_pem = bytes(corrupted)

        with _sign_spy(running_broker) as spy:
            response = agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code)
        assert response.status_code == 422
        assert response.json()["error"] == "CERTIFICATE_INVALID"
        assert spy.call_count == 0
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_F_valid_code_and_valid_csr_signs_exactly_once(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    try:
        pairing_id, pairing_code = create_pairing_over_service(
            session_factory, tenant_id=setup["tenant_id"], requested_by_user_id=setup["admin_id"]
        )
        agent = _agent(running_broker)
        with _sign_spy(running_broker) as spy:
            response = agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code)
        assert response.status_code == 200, response.text
        assert spy.call_count == 1
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_G_db_failure_after_signature_before_commit_never_delivers_or_persists(session_factory, running_broker):
    """Simula un fallo de BD DESPUES de que `_issue_certificate` ya firmo
    (dentro de `confirm_pairing`, ya con `db.flush()` exitoso) pero ANTES de
    que `_run` complete `db.commit()` -- envolviendo el `commit` de la
    sesion que el broker usara para ESTA peticion con un stub que falla una
    sola vez. El certificado nunca debe llegar por HTTP, y una lectura
    posterior con una sesion LIMPIA nunca debe ver el pairing COMPLETED ni
    el agente persistido."""
    from app.broker.server import BrokerServer

    setup = commit_tenant_user(session_factory)
    try:
        pairing_id, pairing_code = create_pairing_over_service(
            session_factory, tenant_id=setup["tenant_id"], requested_by_user_id=setup["admin_id"]
        )

        def _failing_commit_factory():
            session = session_factory()
            original_commit = session.commit
            state = {"used": False}

            def _commit_once_then_fail():
                if not state["used"]:
                    state["used"] = True
                    raise RuntimeError("SIMULATED_DB_FAILURE_AFTER_SIGN_BEFORE_COMMIT")
                return original_commit()

            session.commit = _commit_once_then_fail
            return session

        broker = BrokerServer(
            host="127.0.0.1",
            pairing_port=0,
            operational_port=0,
            ca=running_broker.ca,
            session_factory=_failing_commit_factory,
        )
        broker.start()
        try:
            agent = AgentTestClient(
                ca_certificate_pem=_ca_pem(running_broker),
                pairing_base_url=f"https://127.0.0.1:{broker.pairing_bound_port}",
                operational_base_url=f"https://127.0.0.1:{broker.operational_bound_port}",
            )
            response = agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code)
            assert response.status_code == 500
            assert response.json()["error"] == "INTERNAL_ERROR"
        finally:
            broker.stop()

        verify_session = session_factory()
        try:
            from app.models.sol_agent_access import SolAgent, SolAgentPairing

            pairing_row = verify_session.get(SolAgentPairing, pairing_id)
            assert pairing_row.status == "PENDING"
            assert pairing_row.confirmed_agent_id is None

            agents = verify_session.query(SolAgent).filter(SolAgent.tenant_id == setup["tenant_id"]).all()
            assert agents == []
        finally:
            verify_session.close()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def _ca_pem(running_broker) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return running_broker.ca.certificate.public_bytes(serialization.Encoding.PEM)


def test_H_double_confirmation_race_at_most_one_committed_agent(session_factory, running_broker):
    setup = commit_tenant_user(session_factory)
    try:
        pairing_id, pairing_code = create_pairing_over_service(
            session_factory, tenant_id=setup["tenant_id"], requested_by_user_id=setup["admin_id"]
        )
        agent_a = _agent(running_broker)
        agent_b = _agent(running_broker)
        agent_a.generate_key_and_csr()
        agent_b.generate_key_and_csr()

        start_barrier = threading.Barrier(2)
        results: dict[str, int] = {}

        def _attempt(agent: AgentTestClient, key: str) -> None:
            start_barrier.wait(timeout=5.0)
            response = agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code)
            results[key] = response.status_code

        t1 = threading.Thread(target=_attempt, args=(agent_a, "a"))
        t2 = threading.Thread(target=_attempt, args=(agent_b, "b"))
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)

        assert sorted(results.values()) == [200, 422]

        from app.models.sol_agent_access import SolAgent

        session = session_factory()
        try:
            agents = session.query(SolAgent).filter(SolAgent.tenant_id == setup["tenant_id"]).all()
            assert len(agents) == 1
        finally:
            session.close()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])
