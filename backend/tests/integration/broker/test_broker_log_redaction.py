"""Fase 5, Bloque 5.2, Domain D -- seccion 65/informe final: barrido de
logs. El codigo de pairing en claro y el token de job en claro NUNCA deben
aparecer en ninguna linea de log capturada, ni siquiera durante un ciclo de
vida completo (pairing real -> mTLS -> despacho -> reclamo). El token de job
en este diseno NUNCA cruza la red hacia el agente en absoluto (ver
`pending_dispatch.py`) -- este test lo verifica programaticamente, no lo
asume."""

from __future__ import annotations

import logging

from tests.integration.broker.agent_test_client import AgentTestClient
from tests.integration.broker.conftest import (
    cleanup_tenant,
    commit_credential,
    commit_session,
    commit_tenant_user,
    create_pairing_over_service,
)


def test_A_pairing_code_and_job_token_never_appear_in_captured_logs(session_factory, running_broker, caplog):
    from app.broker.dispatch import dispatch_job_to_agent
    from cryptography.hazmat.primitives import serialization

    caplog.set_level(logging.DEBUG)

    setup = commit_tenant_user(session_factory)
    try:
        pairing_id, pairing_code = create_pairing_over_service(
            session_factory, tenant_id=setup["tenant_id"], requested_by_user_id=setup["admin_id"]
        )
        agent = AgentTestClient(
            ca_certificate_pem=running_broker.ca.certificate.public_bytes(serialization.Encoding.PEM),
            pairing_base_url=f"https://127.0.0.1:{running_broker.pairing_bound_port}",
            operational_base_url=f"https://127.0.0.1:{running_broker.operational_bound_port}",
        )
        assert agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code).status_code == 200
        assert agent.hello().status_code == 200
        assert agent.heartbeat().status_code == 200

        client_id, credential_id = commit_credential(session_factory, setup["tenant_id"], setup["admin_id"])
        session_id = commit_session(
            session_factory,
            tenant_id=setup["tenant_id"],
            client_id=client_id,
            credential_id=credential_id,
            requester_id=setup["admin_id"],
        )
        dispatch_session = session_factory()
        try:
            job_id = dispatch_job_to_agent(
                dispatch_session,
                tenant_id=setup["tenant_id"],
                session_id=session_id,
                agent_id=agent.agent_id,
                pending_dispatch=running_broker.pending_dispatch,
            )
            dispatch_session.commit()
        finally:
            dispatch_session.close()

        # Recupera el token en claro DIRECTAMENTE del store interno -- solo
        # para comparar contra los logs; nunca se recibe por HTTP en este
        # diseno (ver `pending_dispatch.py`).
        raw_token = running_broker.pending_dispatch._by_agent[agent.agent_id].raw_token  # type: ignore[attr-defined]
        assert raw_token  # sanity: el token realmente existe en este punto

        agent.claim(job_id)
        agent.report_status(job_id, "COMPLETED")

        full_log_text = caplog.text
        assert pairing_code not in full_log_text
        assert raw_token not in full_log_text
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_B_pairing_create_result_and_job_create_result_reprs_are_redacted():
    """Contrato ya productivo de Domain C -- verificado aqui de nuevo en el
    contexto de Domain D porque un `repr()` accidental de estos objetos
    (ej. en una traza de pytest, en un log de excepcion no controlado) es
    precisamente el tipo de fuga que un barrido de logs debe descartar."""
    from app.services.sol_agent_job_service import JobCreateResult
    from app.services.sol_agent_pairing_service import PairingCreateResult

    fake_job = type("FakeJob", (), {"id": "fake-job-id"})()
    job_result = JobCreateResult(job=fake_job, job_token="super-secret-plaintext-token")
    assert "super-secret-plaintext-token" not in repr(job_result)

    fake_pairing = type("FakePairing", (), {"id": "fake-pairing-id"})()
    pairing_result = PairingCreateResult(pairing=fake_pairing, pairing_code="123456")
    assert "123456" not in repr(pairing_result)
