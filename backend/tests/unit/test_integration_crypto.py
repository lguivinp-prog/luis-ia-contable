"""Cifrado de credenciales de integracion (Fase 3 / Bloque 3.1, ver
ADR-0064): sobre AES-256-GCM con AAD obligatorio + fingerprint de clave."""

import base64

import pytest

from app.core import integration_crypto as ic


def _make_settings(monkeypatch, *, key_b64: str | None, key_id: str = "v1"):
    settings = ic.get_settings()
    monkeypatch.setattr(settings, "integration_token_encryption_key", key_b64 or "", raising=False)
    monkeypatch.setattr(settings, "integration_token_key_id", key_id, raising=False)
    return settings


def _random_key_b64() -> str:
    import os

    return base64.b64encode(os.urandom(32)).decode()


def test_encrypt_decrypt_round_trip(monkeypatch):
    _make_settings(monkeypatch, key_b64=_random_key_b64())
    context = ic.EncryptionContext(
        table_name="google_oauth_connections",
        row_id="row-1",
        tenant_id="tenant-1",
        user_id="user-1",
        token_type="ACCESS",
    )
    envelope, key_id = ic.encrypt_secret("my-access-token", context)
    assert key_id == "v1"
    plaintext = ic.decrypt_secret(envelope, key_id, context)
    assert plaintext == "my-access-token"


def test_decrypt_fails_with_wrong_row_id_in_aad(monkeypatch):
    _make_settings(monkeypatch, key_b64=_random_key_b64())
    context = ic.EncryptionContext(
        table_name="google_oauth_connections",
        row_id="row-1",
        tenant_id="tenant-1",
        user_id="user-1",
        token_type="ACCESS",
    )
    envelope, key_id = ic.encrypt_secret("secret", context)
    wrong_context = ic.EncryptionContext(
        table_name="google_oauth_connections",
        row_id="row-2",
        tenant_id="tenant-1",
        user_id="user-1",
        token_type="ACCESS",
    )
    with pytest.raises(ic.IntegrationDecryptionError):
        ic.decrypt_secret(envelope, key_id, wrong_context)


def test_decrypt_fails_with_wrong_token_type_in_aad(monkeypatch):
    _make_settings(monkeypatch, key_b64=_random_key_b64())
    context = ic.EncryptionContext(
        table_name="google_oauth_connections",
        row_id="row-1",
        tenant_id="tenant-1",
        user_id="user-1",
        token_type="ACCESS",
    )
    envelope, key_id = ic.encrypt_secret("secret", context)
    wrong_context = ic.EncryptionContext(
        table_name="google_oauth_connections",
        row_id="row-1",
        tenant_id="tenant-1",
        user_id="user-1",
        token_type="REFRESH",
    )
    with pytest.raises(ic.IntegrationDecryptionError):
        ic.decrypt_secret(envelope, key_id, wrong_context)


def test_decrypt_fails_with_wrong_tenant_in_aad(monkeypatch):
    _make_settings(monkeypatch, key_b64=_random_key_b64())
    context = ic.EncryptionContext(
        table_name="google_oauth_connections",
        row_id="row-1",
        tenant_id="tenant-1",
        user_id="user-1",
        token_type="ACCESS",
    )
    envelope, key_id = ic.encrypt_secret("secret", context)
    wrong_context = ic.EncryptionContext(
        table_name="google_oauth_connections",
        row_id="row-1",
        tenant_id="tenant-OTHER",
        user_id="user-1",
        token_type="ACCESS",
    )
    with pytest.raises(ic.IntegrationDecryptionError):
        ic.decrypt_secret(envelope, key_id, wrong_context)


def test_missing_key_raises_key_missing_error(monkeypatch):
    _make_settings(monkeypatch, key_b64=None)
    assert ic.is_encryption_configured() is False
    context = ic.EncryptionContext(
        table_name="t", row_id="r", tenant_id="t1", user_id="u1", token_type="ACCESS"
    )
    with pytest.raises(ic.IntegrationKeyMissingError):
        ic.encrypt_secret("x", context)


def test_invalid_length_key_raises_key_missing_error(monkeypatch):
    short_key = base64.b64encode(b"short").decode()
    _make_settings(monkeypatch, key_b64=short_key)
    assert ic.is_encryption_configured() is False


def test_decrypt_fails_when_key_id_does_not_match_active_key(monkeypatch):
    _make_settings(monkeypatch, key_b64=_random_key_b64(), key_id="v1")
    context = ic.EncryptionContext(
        table_name="t", row_id="r", tenant_id="t1", user_id="u1", token_type="ACCESS"
    )
    envelope, key_id = ic.encrypt_secret("secret", context)
    with pytest.raises(ic.IntegrationDecryptionError):
        ic.decrypt_secret(envelope, "v2-does-not-match", context)


def test_fingerprint_stable_for_same_key(monkeypatch):
    key = _random_key_b64()
    _make_settings(monkeypatch, key_b64=key)
    fp1 = ic.compute_key_fingerprint()
    fp2 = ic.compute_key_fingerprint()
    assert fp1 == fp2


def test_fingerprint_differs_for_different_keys(monkeypatch):
    key_a = _random_key_b64()
    key_b = _random_key_b64()
    fp_a = ic.compute_key_fingerprint(key_a)
    fp_b = ic.compute_key_fingerprint(key_b)
    assert fp_a != fp_b


def test_fingerprint_never_reveals_the_key(monkeypatch):
    key = _random_key_b64()
    fingerprint = ic.compute_key_fingerprint(key)
    assert key not in fingerprint
    assert len(fingerprint) == 64  # hex de SHA-256
