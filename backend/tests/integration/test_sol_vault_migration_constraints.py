"""Migracion 0017 (Fase 5, Bloque 5.1, ver ADR-0068): verifica por
inspeccion real de `pg_constraint`/`pg_indexes` que los nombres exactos de
constraints e indices existen tal como los declara la migracion."""

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


def test_sol_vaults_constraints_and_indexes_exist(db_session):
    constraints = _constraint_names(db_session, "sol_vaults")
    assert "uq_sol_vaults_tenant" in constraints
    assert "uq_sol_vaults_id_tenant" in constraints
    assert "ck_sol_vaults_version_positive" in constraints
    assert "ck_sol_vaults_status" in constraints
    assert "ck_sol_vaults_disabled_coherence" in constraints
    assert "fk_sol_vaults_initialized_by_tenant" in constraints
    assert "fk_sol_vaults_disabled_by_tenant" in constraints


def test_sol_vault_key_slots_constraints_and_indexes_exist(db_session):
    constraints = _constraint_names(db_session, "sol_vault_key_slots")
    indexes = _index_names(db_session, "sol_vault_key_slots")
    assert "uq_sol_vault_key_slots_id_tenant" in constraints
    assert "ck_sol_vault_key_slots_kind" in constraints
    assert "ck_sol_vault_key_slots_status" in constraints
    assert "ck_sol_vault_key_slots_kind_user_coherence" in constraints
    assert "ck_sol_vault_key_slots_revocation_coherence" in constraints
    assert "ck_sol_vault_key_slots_failed_attempts_non_negative" in constraints
    assert "ck_sol_vault_key_slots_kdf_params_positive" in constraints
    assert "ck_sol_vault_key_slots_recovery_confirmed_only_recovery" in constraints
    assert "ux_sol_vault_key_slots_user_active" in indexes
    assert "ux_sol_vault_key_slots_recovery_confirmed_active" in indexes
    assert "ux_sol_vault_key_slots_recovery_pending_active" in indexes


def test_sol_vault_data_keys_constraints_and_indexes_exist(db_session):
    constraints = _constraint_names(db_session, "sol_vault_data_keys")
    indexes = _index_names(db_session, "sol_vault_data_keys")
    assert "uq_sol_vault_data_keys_tenant_vault_version" in constraints
    assert "ck_sol_vault_data_keys_version_positive" in constraints
    assert "ck_sol_vault_data_keys_status" in constraints
    assert "ck_sol_vault_data_keys_retired_coherence" in constraints
    assert "ux_sol_vault_data_keys_active" in indexes


def test_sol_vault_invitations_constraints_and_indexes_exist(db_session):
    constraints = _constraint_names(db_session, "sol_vault_invitations")
    indexes = _index_names(db_session, "sol_vault_invitations")
    assert "uq_sol_vault_invitations_id_tenant" in constraints
    assert "ck_sol_vault_invitations_status" in constraints
    assert "ck_sol_vault_invitations_inviter_not_target" in constraints
    assert "ck_sol_vault_invitations_status_coherence" in constraints
    assert "ux_sol_vault_invitations_pending_per_target" in indexes


def test_client_sol_credentials_constraints_and_indexes_exist(db_session):
    constraints = _constraint_names(db_session, "client_sol_credentials")
    indexes = _index_names(db_session, "client_sol_credentials")
    assert "uq_client_sol_credentials_id_tenant_client" in constraints
    assert "ck_client_sol_credentials_kind" in constraints
    assert "ck_client_sol_credentials_status" in constraints
    assert "ck_client_sol_credentials_disabled_coherence" in constraints
    assert "fk_client_sol_credentials_data_key_version" in constraints
    assert "ux_client_sol_credentials_active_per_client" in indexes


def test_no_previous_table_was_dropped_by_the_new_migration(db_session):
    """La migracion 0017 solo crea tablas nuevas -- confirma que tablas
    previas (0016) siguen presentes."""
    rows = db_session.execute(
        text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename IN ('communication_drafts', 'users', 'tenants', 'clients')"
        )
    ).fetchall()
    assert {row[0] for row in rows} == {"communication_drafts", "users", "tenants", "clients"}
