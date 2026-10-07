"""Domain G: herramienta de operador `python -m app.broker.pair_cli` (misma autoridad de servicio que el backend)."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.broker import pair_cli
from app.models.sol_agent_access import SolAgentPairing
from tests.integration.broker.conftest import cleanup_tenant, commit_tenant_user


def test_pair_cli_creates_a_pending_pairing_and_shows_the_code_once(session_factory, pairing_key, monkeypatch, capsys):
    monkeypatch.setattr(pair_cli, "SessionLocal", session_factory)
    setup = commit_tenant_user(session_factory)
    try:
        rc = pair_cli.main(["--tenant-id", str(setup["tenant_id"]), "--user-id", str(setup["admin_id"])])
        out = capsys.readouterr().out
        assert rc == 0
        kv = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        code = kv["PAIRING_CODE"]
        s = session_factory()
        try:
            row = s.execute(select(SolAgentPairing).where(SolAgentPairing.id == uuid.UUID(kv["PAIRING_ID"]))).scalar_one()
            assert row.status == "PENDING" and row.tenant_id == setup["tenant_id"]
            assert code not in repr(row.pairing_code_verifier)  # el codigo en claro nunca se persiste
        finally:
            s.close()
        # un segundo pairing mientras hay uno pendiente se rechaza (misma politica del servicio)
        assert pair_cli.main(["--tenant-id", str(setup["tenant_id"]), "--user-id", str(setup["admin_id"])]) == 3
        assert "PAIRING_CODE" not in capsys.readouterr().out
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_pair_cli_rejects_unknown_or_foreign_users(session_factory, pairing_key, monkeypatch, capsys):
    monkeypatch.setattr(pair_cli, "SessionLocal", session_factory)
    a, b = commit_tenant_user(session_factory), commit_tenant_user(session_factory)
    try:
        assert pair_cli.main(["--tenant-id", str(a["tenant_id"]), "--user-id", str(uuid.uuid4())]) == 2
        assert pair_cli.main(["--tenant-id", str(a["tenant_id"]), "--user-id", str(b["admin_id"])]) == 2  # otro tenant
        assert "PAIRING_CODE" not in capsys.readouterr().out
    finally:
        cleanup_tenant(session_factory, a["tenant_id"])
        cleanup_tenant(session_factory, b["tenant_id"])
