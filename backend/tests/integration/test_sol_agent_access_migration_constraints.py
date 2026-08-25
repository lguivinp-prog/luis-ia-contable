"""Migracion 0018 (Fase 5, Bloque 5.2): verifica por inspeccion real de
`pg_constraint`/`pg_indexes` que los nombres exactos de constraints e
indices declarados en `app/models/sol_agent_access.py` existen tal como
los declara el modelo -- mismo patron que
test_sol_vault_migration_constraints.py. `db_session`/`test_engine` crean
el esquema desde `Base.metadata` (el modelo ORM), no desde la migracion --
la paridad modelo/migracion 0018 se verifica adicionalmente comparando
estos mismos nombres contra la migracion real aplicada (ver
test_sol_agent_access_migration.py e informe de Domain B)."""

from __future__ import annotations

from sqlalchemy import text


def _index_names(db_session, table: str) -> set[str]:
    rows = db_session.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename = :table"), {"table": table}
    ).fetchall()
    return {row[0] for row in rows}


def _constraint_names(db_session, table: str) -> set[str]:
    rows = db_session.execute(
        text(
            "SELECT conname FROM pg_constraint c "
            "JOIN pg_class t ON c.conrelid = t.oid WHERE t.relname = :table"
        ),
        {"table": table},
    ).fetchall()
    return {row[0] for row in rows}


def test_sol_agents_constraints_and_indexes_exist(db_session):
    constraints = _constraint_names(db_session, "sol_agents")
    indexes = _index_names(db_session, "sol_agents")
    assert "uq_sol_agents_id_tenant" in constraints
    assert "uq_sol_agents_certificate_fingerprint" in constraints
    assert "ck_sol_agents_status" in constraints
    assert "ck_sol_agents_version_positive" in constraints
    assert "ck_sol_agents_revoked_coherence" in constraints
    assert "ix_sol_agents_tenant_status" in indexes
    assert "ix_sol_agents_last_seen_at" in indexes


def test_sol_access_sessions_constraints_and_indexes_exist(db_session):
    constraints = _constraint_names(db_session, "sol_access_sessions")
    indexes = _index_names(db_session, "sol_access_sessions")
    assert "uq_sol_access_sessions_id_tenant" in constraints
    assert "uq_sol_access_sessions_idempotency_key" in constraints
    assert "ck_sol_access_sessions_scope" in constraints
    assert "ck_sol_access_sessions_status" in constraints
    assert "ck_sol_access_sessions_operation_result" in constraints
    assert "ck_sol_access_sessions_terminal_reason_code" in constraints
    assert "ck_sol_access_sessions_version_positive" in constraints
    assert "fk_sol_access_sessions_client_tenant" in constraints
    assert "fk_sol_access_sessions_credential_client_tenant" in constraints
    assert "fk_sol_access_sessions_requester_tenant" in constraints
    assert "fk_sol_access_sessions_agent_tenant" in constraints
    assert "ix_sol_access_sessions_tenant_status" in indexes
    assert "ix_sol_access_sessions_expires_at" in indexes
    assert "ux_sol_access_sessions_active_per_credential" in indexes


def test_sol_agent_jobs_constraints_and_indexes_exist(db_session):
    constraints = _constraint_names(db_session, "sol_agent_jobs")
    indexes = _index_names(db_session, "sol_agent_jobs")
    assert "uq_sol_agent_jobs_job_token_hash" in constraints
    assert "ck_sol_agent_jobs_status" in constraints
    assert "ck_sol_agent_jobs_version_positive" in constraints
    assert "fk_sol_agent_jobs_session_tenant" in constraints
    assert "fk_sol_agent_jobs_agent_tenant" in constraints
    assert "ix_sol_agent_jobs_session_status" in indexes
    assert "ix_sol_agent_jobs_agent_status" in indexes
    assert "ix_sol_agent_jobs_expires_at" in indexes
    assert "ux_sol_agent_jobs_active_per_session" in indexes
    assert "ux_sol_agent_jobs_active_per_agent" in indexes
    assert "ux_sol_agent_jobs_one_delivery_per_session" in indexes


def test_sol_agent_pairings_constraints_and_indexes_exist(db_session):
    constraints = _constraint_names(db_session, "sol_agent_pairings")
    indexes = _index_names(db_session, "sol_agent_pairings")
    assert "ck_sol_agent_pairings_status" in constraints
    assert "ck_sol_agent_pairings_version_positive" in constraints
    assert "ck_sol_agent_pairings_expires_after_created" in constraints
    assert "ck_sol_agent_pairings_failed_attempt_count_non_negative" in constraints
    assert "ck_sol_agent_pairings_verifier_coherence" in constraints
    assert "fk_sol_agent_pairings_requester_tenant" in constraints
    assert "fk_sol_agent_pairings_confirmed_agent_tenant" in constraints
    assert "ix_sol_agent_pairings_tenant_status" in indexes
    assert "ix_sol_agent_pairings_expires_at" in indexes
    assert "ux_sol_agent_pairings_pending_per_requester" in indexes


def test_no_previous_table_was_dropped_by_the_new_model(db_session):
    """El modelo 0018 solo agrega tablas nuevas -- confirma que tablas
    previas (Bloque 5.1) siguen presentes en el metadata compartido."""
    rows = db_session.execute(
        text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename IN ('client_sol_credentials', 'sol_vaults', 'users', 'tenants', 'clients')"
        )
    ).fetchall()
    assert {row[0] for row in rows} == {
        "client_sol_credentials", "sol_vaults", "users", "tenants", "clients",
    }
