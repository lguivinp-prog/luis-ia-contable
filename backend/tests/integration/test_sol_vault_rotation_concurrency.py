"""Rotacion atomica de `credential_dek` (Fase 5, Bloque 5.1, ver ADR-0068/
ADR-0070): excepcion forzada a mitad de la rotacion (monkeypatch, mismo
patron que ADR-0037) con verificacion de rollback atomico usando DOS
conexiones PostgreSQL reales -- sin ningun sleep de duracion fija."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core import clock as clock_module
from app.core import sol_vault_crypto as crypto
from app.core.security import hash_password
from app.core.sol_vault_registry import VaultUnlockRegistry
from app.models.client import Client
from app.models.client_sol_credential import ClientSolCredential
from app.models.session import Session as AppSession
from app.models.sol_vault import SolVault, SolVaultDataKey
from app.models.tenant import Tenant
from app.models.user import User
from app.services import client_sol_credential_service
from app.services import sol_vault_service as svc
from app.services.ruc_validator import compute_check_digit

_NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
_FAST_PARAMS = crypto.Argon2Params(time_cost=1, memory_cost_kib=8 * 1024, parallelism=1)


@pytest.fixture()
def session_factory(test_engine):
    return sessionmaker(bind=test_engine, future=True)


def _fake_ruc() -> str:
    base8 = f"{uuid.uuid4().int % 10**8:08d}"
    first_ten = "20" + base8
    return first_ten + str(compute_check_digit(first_ten))


def _setup(session_factory) -> dict:
    session = session_factory()
    try:
        clock_module.set_clock(lambda: _NOW)
        tenant = Tenant(name="Tenant rollback rotacion SOL")
        session.add(tenant)
        session.flush()
        user = User(
            tenant_id=tenant.id,
            email=f"actor-rollback-{uuid.uuid4().hex[:8]}@sol-vault-tests.dev",
            full_name="Actor rollback",
            password_hash=hash_password("TestPassword123!"),
            role="admin",
            is_active=True,
        )
        session.add(user)
        session.flush()
        session_row = AppSession(
            tenant_id=tenant.id, user_id=user.id, token_hash=uuid.uuid4().hex,
            csrf_token_hash=uuid.uuid4().hex, created_at=_NOW, last_seen_at=_NOW,
            expires_at=_NOW + timedelta(hours=8),
        )
        session.add(session_row)
        session.flush()

        registry = VaultUnlockRegistry()
        result = svc.initialize_vault(
            session, registry, tenant_id=tenant.id, actor=user, app_session_id=session_row.id,
            personal_vault_password="contrasena-ficticia-rollback",
        )
        svc.confirm_recovery(
            session, tenant_id=tenant.id, vault_id=result.vault.id, recovery_code=result.recovery_code
        )

        plaintexts = {}
        for i in range(3):
            client = Client(
                tenant_id=tenant.id, ruc=_fake_ruc(), business_name=f"Cliente rollback {i}",
                person_type="juridica", ruc_status="ACTIVO", fiscal_address_condition="HABIDO",
                tax_regime="GENERAL", service_status="activo",
            )
            session.add(client)
            session.flush()
            username, password = f"usuario-{i}", f"clave-{i}"
            credential = client_sol_credential_service.create_credential(
                session, registry, tenant_id=tenant.id, vault_id=result.vault.id, client_id=client.id,
                actor=user, app_session_id=session_row.id, credential_kind="SECONDARY",
                sol_username=username, sol_password=password, authorization_basis="autorizacion ficticia",
            )
            plaintexts[credential.id] = (client.id, username, password)
        session.commit()
        return {
            "tenant_id": tenant.id, "vault_id": result.vault.id, "user_id": user.id,
            "app_session_id": session_row.id, "plaintexts": plaintexts, "registry": registry,
        }
    finally:
        session.close()
        clock_module.reset_clock()


def _cleanup(session_factory, tenant_id) -> None:
    session = session_factory()
    try:
        from sqlalchemy import delete

        from app.models.audit_log import AuditLog
        from app.models.sol_vault import SolVaultInvitation, SolVaultKeySlot

        session.execute(delete(ClientSolCredential).where(ClientSolCredential.tenant_id == tenant_id))
        session.execute(delete(SolVaultInvitation).where(SolVaultInvitation.tenant_id == tenant_id))
        session.execute(delete(SolVaultDataKey).where(SolVaultDataKey.tenant_id == tenant_id))
        session.execute(delete(SolVaultKeySlot).where(SolVaultKeySlot.tenant_id == tenant_id))
        session.execute(delete(SolVault).where(SolVault.tenant_id == tenant_id))
        session.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
        session.execute(delete(AppSession).where(AppSession.tenant_id == tenant_id))
        session.execute(delete(User).where(User.tenant_id == tenant_id))
        session.execute(delete(Client).where(Client.tenant_id == tenant_id))
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        session.commit()
    finally:
        session.close()


def test_forced_exception_mid_rotation_rolls_back_atomically_with_real_concurrency(
    session_factory, monkeypatch
):
    setup = _setup(session_factory)
    tenant_id, vault_id = setup["tenant_id"], setup["vault_id"]
    user_id, app_session_id = setup["user_id"], setup["app_session_id"]
    registry = setup["registry"]
    plaintexts = setup["plaintexts"]

    a_holds_locks = threading.Event()
    b_about_to_block = threading.Event()
    release_a = threading.Event()
    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    original_encrypt = crypto.encrypt_credential_field
    call_count = {"n": 0}

    def flaky_encrypt(plaintext, key, aad):
        call_count["n"] += 1
        # Falla tras recifrar 1 de los 2 campos (usuario+password) de la
        # primera credencial -- deja trabajo real a medio hacer.
        if call_count["n"] > 1:
            raise RuntimeError("fallo simulado a mitad de la rotacion")
        return original_encrypt(plaintext, key, aad)

    def setup_actor(session, uid):
        return session.get(User, uid)

    def worker_a():
        session_a = session_factory()
        try:
            clock_module.set_clock(lambda: _NOW)
            monkeypatch.setattr(
                "app.services.sol_vault_service.crypto.encrypt_credential_field", flaky_encrypt
            )
            try:
                svc.rotate_data_key(
                    session_a, registry, tenant_id=tenant_id, vault_id=vault_id,
                    actor=setup_actor(session_a, user_id), app_session_id=app_session_id, expected_version=1,
                )
                raise AssertionError("se esperaba una excepcion forzada")
            except RuntimeError:
                pass

            # la transaccion sigue abierta (sin commit ni rollback): retiene
            # el advisory lock y los FOR UPDATE de vault/data key/creds.
            a_holds_locks.set()
            release_a.wait(timeout=5)
            session_a.rollback()
            results["a"] = "rolled_back"
        except BaseException as exc:  # noqa: BLE001 -- reportado en el hilo principal
            errors["a"] = exc
        finally:
            session_a.close()

    def worker_b():
        session_b = session_factory()
        try:
            a_holds_locks.wait(timeout=5)

            rows = session_b.execute(
                select(ClientSolCredential).where(ClientSolCredential.vault_id == vault_id)
            ).scalars().all()
            assert len(rows) == 3
            for row in rows:
                assert row.data_key_version == 1, (
                    "no debe verse ninguna fila recifrada de una txn no comiteada"
                )

            from sqlalchemy import text

            b_about_to_block.set()
            session_b.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": str(vault_id)})
            session_b.commit()

            verify_session = session_factory()
            try:
                vault = verify_session.get(SolVault, vault_id)
                assert vault.version == 1
                data_keys = verify_session.execute(
                    select(SolVaultDataKey).where(SolVaultDataKey.vault_id == vault_id)
                ).scalars().all()
                assert len(data_keys) == 1
                assert data_keys[0].status == "ACTIVE"
                assert data_keys[0].data_key_version == 1

                credentials = verify_session.execute(
                    select(ClientSolCredential).where(ClientSolCredential.vault_id == vault_id)
                ).scalars().all()
                assert len(credentials) == 3
                clock_module.set_clock(lambda: _NOW)
                verify_registry = VaultUnlockRegistry()
                actor_row = verify_session.get(User, user_id)
                svc.unlock(
                    verify_session, verify_registry, tenant_id=tenant_id, vault_id=vault_id, actor=actor_row,
                    app_session_id=app_session_id, personal_vault_password="contrasena-ficticia-rollback",
                )
                for credential in credentials:
                    assert credential.data_key_version == 1
                    expected_client_id, expected_username, expected_password = plaintexts[credential.id]
                    entry = verify_registry.resolve(
                        verify_session, tenant_id=tenant_id, vault_id=vault_id, user_id=user_id,
                        app_session_id=app_session_id, operation="VERIFY_ACCESS", now=_NOW,
                    )
                    data_key_row = data_keys[0]
                    credential_dek = crypto.unwrap_data_key(
                        data_key_row.wrapped_data_key, bytes(entry.vault_root_key),
                        crypto.aad_wrapped_data_key(
                            data_key_row_id=data_key_row.id, tenant_id=tenant_id, vault_id=vault_id,
                            data_key_version=1,
                        ),
                    )
                    username = crypto.decrypt_credential_field(
                        credential.sol_username_encrypted, credential_dek,
                        crypto.aad_credential_field(
                            credential_id=credential.id, tenant_id=tenant_id,
                            client_id=expected_client_id, field_name="sol_username", data_key_version=1,
                        ),
                    )
                    assert username == expected_username
            finally:
                verify_session.close()
            results["b"] = "verified_intact"
        except BaseException as exc:  # noqa: BLE001 -- reportado en el hilo principal
            errors["b"] = exc
        finally:
            session_b.close()

    thread_a = threading.Thread(target=worker_a)
    thread_b = threading.Thread(target=worker_b)
    thread_a.start()
    thread_b.start()
    assert b_about_to_block.wait(timeout=5), "el hilo B nunca llego al intento de bloqueo"
    release_a.set()
    thread_a.join(timeout=15)
    thread_b.join(timeout=15)

    try:
        assert not errors, f"no se esperaban errores: {errors}"
        assert results.get("a") == "rolled_back"
        assert results.get("b") == "verified_intact"
    finally:
        clock_module.reset_clock()
        _cleanup(session_factory, tenant_id)
