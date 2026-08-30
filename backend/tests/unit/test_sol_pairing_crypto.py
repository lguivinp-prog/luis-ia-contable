"""Verificador de pairing HMAC-SHA-256 (Fase 5, Bloque 5.2, Domain A.3/C,
ver docs/phase5/block5_2/BLOCK5_2_DATA_MODEL_DRAFT.md seccion 4). Mismo
patron de pruebas que test_integration_crypto.py."""

from __future__ import annotations

import base64
import os
import uuid
from unittest.mock import patch

import pytest

from app.core import sol_pairing_crypto as pc


def _make_settings(monkeypatch, *, key_b64: str | None):
    settings = pc.get_settings()
    monkeypatch.setattr(settings, "pairing_verifier_key", key_b64 or "", raising=False)
    return settings


def _random_key_b64() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def test_generate_pairing_code_is_six_ascii_digits():
    for _ in range(200):
        code = pc.generate_pairing_code()
        assert isinstance(code, str)
        assert len(code) == 6
        assert code.isdigit()


def test_generate_pairing_code_allows_leading_zeroes():
    # Con 200 muestras, la probabilidad de nunca ver un cero inicial (si el
    # generador fuera correcto) es (0.9)^200 ~ 7e-10 -- si esto falla de
    # forma persistente, indica un bug de padding, no mala suerte.
    codes = [pc.generate_pairing_code() for _ in range(200)]
    assert any(code.startswith("0") for code in codes)


def test_generate_pairing_code_uses_secrets_randbelow_not_random_module():
    with patch("app.core.sol_pairing_crypto.secrets.randbelow", wraps=pc.secrets.randbelow) as spy:
        pc.generate_pairing_code()
        spy.assert_called_once_with(10**6)


def test_verifier_round_trip_correct_code(monkeypatch):
    _make_settings(monkeypatch, key_b64=_random_key_b64())
    tenant_id = uuid.uuid4()
    pairing_id = uuid.uuid4()
    code = "042017"
    verifier = pc.compute_pairing_verifier(tenant_id=tenant_id, pairing_id=pairing_id, code=code)
    assert pc.verify_pairing_code(
        tenant_id=tenant_id, pairing_id=pairing_id, code=code, stored_verifier=verifier
    )


def test_verifier_rejects_wrong_code(monkeypatch):
    _make_settings(monkeypatch, key_b64=_random_key_b64())
    tenant_id = uuid.uuid4()
    pairing_id = uuid.uuid4()
    verifier = pc.compute_pairing_verifier(tenant_id=tenant_id, pairing_id=pairing_id, code="042017")
    assert not pc.verify_pairing_code(
        tenant_id=tenant_id, pairing_id=pairing_id, code="042018", stored_verifier=verifier
    )


def test_verifier_rejects_malformed_code_without_raising(monkeypatch):
    _make_settings(monkeypatch, key_b64=_random_key_b64())
    tenant_id = uuid.uuid4()
    pairing_id = uuid.uuid4()
    verifier = pc.compute_pairing_verifier(tenant_id=tenant_id, pairing_id=pairing_id, code="042017")
    assert not pc.verify_pairing_code(
        tenant_id=tenant_id, pairing_id=pairing_id, code="not-digits", stored_verifier=verifier
    )
    assert not pc.verify_pairing_code(
        tenant_id=tenant_id, pairing_id=pairing_id, code="42017", stored_verifier=verifier
    )


def test_domain_separation_by_pairing_id(monkeypatch):
    """Un verificador valido para un pairing NUNCA valida para otro, aunque
    compartan tenant y codigo (Domain A.3 seccion 5)."""
    _make_settings(monkeypatch, key_b64=_random_key_b64())
    tenant_id = uuid.uuid4()
    pairing_a = uuid.uuid4()
    pairing_b = uuid.uuid4()
    code = "042017"
    verifier_a = pc.compute_pairing_verifier(tenant_id=tenant_id, pairing_id=pairing_a, code=code)
    assert not pc.verify_pairing_code(
        tenant_id=tenant_id, pairing_id=pairing_b, code=code, stored_verifier=verifier_a
    )


def test_domain_separation_by_tenant(monkeypatch):
    _make_settings(monkeypatch, key_b64=_random_key_b64())
    pairing_id = uuid.uuid4()
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    code = "042017"
    verifier_a = pc.compute_pairing_verifier(tenant_id=tenant_a, pairing_id=pairing_id, code=code)
    assert not pc.verify_pairing_code(
        tenant_id=tenant_b, pairing_id=pairing_id, code=code, stored_verifier=verifier_a
    )


def test_canonical_message_is_utf8_with_unit_separator_and_lowercase_uuid():
    tenant_id = uuid.uuid4()
    pairing_id = uuid.uuid4()
    message = pc._canonical_message(tenant_id=tenant_id, pairing_id=pairing_id, code="042017")
    assert message == (
        b"sol-agent-pairing:v1"
        + b"\x1f"
        + str(tenant_id).lower().encode("utf-8")
        + b"\x1f"
        + str(pairing_id).lower().encode("utf-8")
        + b"\x1f"
        + b"042017"
    )


def test_verify_uses_constant_time_compare(monkeypatch):
    _make_settings(monkeypatch, key_b64=_random_key_b64())
    tenant_id = uuid.uuid4()
    pairing_id = uuid.uuid4()
    code = "042017"
    verifier = pc.compute_pairing_verifier(tenant_id=tenant_id, pairing_id=pairing_id, code=code)
    with patch("app.core.sol_pairing_crypto.hmac.compare_digest", wraps=pc.hmac.compare_digest) as spy:
        pc.verify_pairing_code(tenant_id=tenant_id, pairing_id=pairing_id, code=code, stored_verifier=verifier)
        spy.assert_called_once()


def test_missing_key_raises_misconfigured_error(monkeypatch):
    _make_settings(monkeypatch, key_b64=None)
    assert pc.is_pairing_configured() is False
    with pytest.raises(pc.PairingVerifierKeyMisconfiguredError):
        pc.compute_pairing_verifier(tenant_id=uuid.uuid4(), pairing_id=uuid.uuid4(), code="042017")


def test_malformed_base64_key_raises_misconfigured_error(monkeypatch):
    _make_settings(monkeypatch, key_b64="not-valid-base64-!!!")
    assert pc.is_pairing_configured() is False
    with pytest.raises(pc.PairingVerifierKeyMisconfiguredError):
        pc.compute_pairing_verifier(tenant_id=uuid.uuid4(), pairing_id=uuid.uuid4(), code="042017")


def test_wrong_length_key_raises_misconfigured_error(monkeypatch):
    short_key = base64.b64encode(b"too-short").decode()
    _make_settings(monkeypatch, key_b64=short_key)
    assert pc.is_pairing_configured() is False


def test_pairing_verifier_key_never_appears_in_error_messages(monkeypatch):
    key = _random_key_b64()
    _make_settings(monkeypatch, key_b64=key)
    try:
        raise pc.PairingCryptoError("codigo invalido")
    except pc.PairingCryptoError as exc:
        assert key not in str(exc)
