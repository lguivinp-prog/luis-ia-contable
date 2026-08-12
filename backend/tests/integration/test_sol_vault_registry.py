"""`VaultUnlockRegistry` productivo (Fase 5, Bloque 5.1, ver ADR-0071):
verificacion en vivo contra `sessions` reales."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.security import hash_password
from app.core.sol_vault_registry import (
    VaultLockedError,
    VaultOperationNotAuthorizedError,
    VaultUnlockExpiredError,
    VaultUnlockRegistry,
)
from app.models.session import Session as AppSession
from app.models.tenant import Tenant
from app.models.user import User

_NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
_TTL = 3600


@pytest.fixture()
def registry() -> VaultUnlockRegistry:
    return VaultUnlockRegistry()


@pytest.fixture()
def actor(db_session):
    tenant = Tenant(name="Tenant registry SOL vault")
    db_session.add(tenant)
    db_session.flush()
    user = User(
        tenant_id=tenant.id,
        email=f"actor-{uuid.uuid4().hex[:8]}@sol-vault-tests.dev",
        full_name="Actor de prueba",
        password_hash=hash_password("TestPassword123!"),
        role="contador",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    session_row = AppSession(
        tenant_id=tenant.id,
        user_id=user.id,
        token_hash=uuid.uuid4().hex,
        csrf_token_hash=uuid.uuid4().hex,
        created_at=_NOW,
        last_seen_at=_NOW,
        expires_at=_NOW + timedelta(hours=8),
    )
    db_session.add(session_row)
    db_session.flush()
    return {"tenant_id": tenant.id, "user_id": user.id, "app_session_id": session_row.id}


def _issue(registry, actor, **overrides):
    kwargs = {
        "vault_root_key": b"clave-raiz-ficticia-32-bytes!!!!",
        "tenant_id": actor["tenant_id"],
        "vault_id": uuid.uuid4(),
        "user_id": actor["user_id"],
        "app_session_id": actor["app_session_id"],
        "allowed_operations": frozenset({"CREDENTIAL_MANAGEMENT"}),
        "authorized_client_ids": None,
        "ttl_seconds": _TTL,
        "now": _NOW,
    }
    kwargs.update(overrides)
    return kwargs["vault_id"], registry.issue(**kwargs)


def test_resolve_succeeds_for_a_freshly_issued_entry(registry, db_session, actor):
    vault_id, handle = _issue(registry, actor)
    entry = registry.resolve(
        db_session, tenant_id=actor["tenant_id"], vault_id=vault_id, user_id=actor["user_id"],
        app_session_id=actor["app_session_id"], operation="CREDENTIAL_MANAGEMENT", now=_NOW,
    )
    assert entry is not None
    assert bytes(entry.vault_root_key) == b"clave-raiz-ficticia-32-bytes!!!!"


def test_resolve_purges_entry_when_session_is_revoked(registry, db_session, actor):
    vault_id, handle = _issue(registry, actor)
    session_row = db_session.get(AppSession, actor["app_session_id"])
    session_row.revoked_at = _NOW
    db_session.flush()

    with pytest.raises(VaultUnlockExpiredError):
        registry.resolve(
            db_session, tenant_id=actor["tenant_id"], vault_id=vault_id, user_id=actor["user_id"],
            app_session_id=actor["app_session_id"], operation="CREDENTIAL_MANAGEMENT", now=_NOW,
        )
    assert registry.get_entry(handle) is None


def test_resolve_purges_entry_when_session_is_expired(registry, db_session, actor):
    vault_id, handle = _issue(registry, actor)
    session_row = db_session.get(AppSession, actor["app_session_id"])
    session_row.expires_at = _NOW - timedelta(seconds=1)
    db_session.flush()

    with pytest.raises(VaultUnlockExpiredError):
        registry.resolve(
            db_session, tenant_id=actor["tenant_id"], vault_id=vault_id, user_id=actor["user_id"],
            app_session_id=actor["app_session_id"], operation="CREDENTIAL_MANAGEMENT", now=_NOW,
        )
    assert registry.get_entry(handle) is None


def test_ttl_expiry_of_the_unlock_entry_itself(registry, db_session, actor):
    vault_id, handle = _issue(registry, actor, ttl_seconds=60)
    later = _NOW + timedelta(seconds=61)
    with pytest.raises(VaultUnlockExpiredError):
        registry.resolve(
            db_session, tenant_id=actor["tenant_id"], vault_id=vault_id, user_id=actor["user_id"],
            app_session_id=actor["app_session_id"], operation="CREDENTIAL_MANAGEMENT", now=later,
        )
    assert registry.get_entry(handle) is None


def test_manual_lock_purges_immediately(registry, db_session, actor):
    vault_id, handle = _issue(registry, actor)
    registry.lock(
        tenant_id=actor["tenant_id"], vault_id=vault_id, user_id=actor["user_id"],
        app_session_id=actor["app_session_id"],
    )
    assert registry.get_entry(handle) is None
    with pytest.raises(VaultLockedError):
        registry.resolve(
            db_session, tenant_id=actor["tenant_id"], vault_id=vault_id, user_id=actor["user_id"],
            app_session_id=actor["app_session_id"], operation="CREDENTIAL_MANAGEMENT", now=_NOW,
        )


def test_lock_all_for_session_purges_every_vault_of_that_session(registry, db_session, actor):
    vault_a, _ = _issue(registry, actor)
    vault_b, _ = _issue(registry, actor, vault_id=uuid.uuid4())
    assert registry.entry_count() == 2
    registry.lock_all_for_session(actor["app_session_id"])
    assert registry.entry_count() == 0


def test_reset_simulates_backend_restart(registry, db_session, actor):
    _issue(registry, actor)
    assert registry.entry_count() == 1
    registry.reset()
    assert registry.entry_count() == 0


def test_operation_not_allowed_is_rejected_without_purging(registry, db_session, actor):
    vault_id, handle = _issue(registry, actor, allowed_operations=frozenset({"VERIFY_ACCESS"}))
    with pytest.raises(VaultOperationNotAuthorizedError):
        registry.resolve(
            db_session, tenant_id=actor["tenant_id"], vault_id=vault_id, user_id=actor["user_id"],
            app_session_id=actor["app_session_id"], operation="DATA_KEY_ROTATION", now=_NOW,
        )
    assert registry.get_entry(handle) is not None


def test_client_id_scope_is_enforced(registry, db_session, actor):
    authorized_client = uuid.uuid4()
    other_client = uuid.uuid4()
    vault_id, handle = _issue(
        registry, actor, authorized_client_ids=frozenset({authorized_client})
    )
    ok = registry.resolve(
        db_session, tenant_id=actor["tenant_id"], vault_id=vault_id, user_id=actor["user_id"],
        app_session_id=actor["app_session_id"], operation="CREDENTIAL_MANAGEMENT", now=_NOW,
        client_id=authorized_client,
    )
    assert ok is not None
    with pytest.raises(VaultOperationNotAuthorizedError):
        registry.resolve(
            db_session, tenant_id=actor["tenant_id"], vault_id=vault_id, user_id=actor["user_id"],
            app_session_id=actor["app_session_id"], operation="CREDENTIAL_MANAGEMENT", now=_NOW,
            client_id=other_client,
        )


def test_issue_rejects_unrecognized_operations(registry, actor):
    with pytest.raises(ValueError):
        registry.issue(
            vault_root_key=b"clave-raiz-ficticia-32-bytes!!!!",
            tenant_id=actor["tenant_id"],
            vault_id=uuid.uuid4(),
            user_id=actor["user_id"],
            app_session_id=actor["app_session_id"],
            allowed_operations=frozenset({"NOT_A_REAL_OPERATION"}),
            authorized_client_ids=None,
            ttl_seconds=_TTL,
            now=_NOW,
        )


def test_zeroize_overwrites_key_on_purge(registry, db_session, actor):
    vault_id, handle = _issue(registry, actor)
    entry = registry.get_entry(handle)
    key_reference = entry.vault_root_key
    assert any(b != 0 for b in key_reference)
    registry.lock(
        tenant_id=actor["tenant_id"], vault_id=vault_id, user_id=actor["user_id"],
        app_session_id=actor["app_session_id"],
    )
    assert all(b == 0 for b in key_reference)
