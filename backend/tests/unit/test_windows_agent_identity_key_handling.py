"""E2.1-A: la clave privada del agente nunca se escribe en claro a disco.
Ejercita el codigo productivo independiente de plataforma
(`encrypt_private_key_pem` / `build_mtls_client_context`)."""

from __future__ import annotations

import ssl
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization

from app.broker import pki
from windows_agent import identity


def _material():
    ca = pki.generate_ca(common_name="unit test CA")
    key, csr_pem = pki.build_agent_csr(common_name="unit-test-agent")
    cert = pki.sign_agent_csr(ca=ca, csr_pem=csr_pem, agent_common_name="unit-test-agent")
    plain = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return ca, cert.public_bytes(serialization.Encoding.PEM), plain


def test_encrypted_pem_is_not_plaintext_and_context_loads_via_memory_passphrase(tmp_path: Path):
    ca, cert_pem, plain = _material()
    encrypted, passphrase = identity.encrypt_private_key_pem(plain)
    assert b"ENCRYPTED PRIVATE KEY" in encrypted
    assert b"-----BEGIN PRIVATE KEY-----" not in encrypted
    assert plain not in encrypted
    assert passphrase.isascii() and len(passphrase) == 64

    (tmp_path / "c.pem").write_bytes(cert_pem)
    (tmp_path / "k.pem").write_bytes(encrypted)
    ctx = identity.build_mtls_client_context(
        ca_certificate_pem=ca.certificate.public_bytes(serialization.Encoding.PEM),
        certificate_path=tmp_path / "c.pem",
        encrypted_key_path=tmp_path / "k.pem",
        passphrase=passphrase,
    )
    assert isinstance(ctx, ssl.SSLContext)


def test_wrong_passphrase_fails_closed(tmp_path: Path):
    ca, cert_pem, plain = _material()
    encrypted, _ = identity.encrypt_private_key_pem(plain)
    (tmp_path / "c.pem").write_bytes(cert_pem)
    (tmp_path / "k.pem").write_bytes(encrypted)
    with pytest.raises(ssl.SSLError):
        identity.build_mtls_client_context(
            ca_certificate_pem=ca.certificate.public_bytes(serialization.Encoding.PEM),
            certificate_path=tmp_path / "c.pem",
            encrypted_key_path=tmp_path / "k.pem",
            passphrase=b"0" * 64,
        )


def test_garbage_private_key_rejected_without_echo():
    with pytest.raises(identity.IdentityCorruptError) as excinfo:
        identity.encrypt_private_key_pem(b"not a key")
    assert "not a key" not in str(excinfo.value)


def test_no_plaintext_private_key_tempfile_mechanism_in_production_package():
    root = Path(identity.__file__).parent
    for py in root.glob("*.py"):
        if py.name == "_manual_windows_validation.py":
            continue
        text = py.read_text(encoding="utf-8")
        assert ".key-bridge-" not in text, py.name
        assert "NoEncryption" not in text or py.name == "pairing.py", py.name  # solo en memoria (pairing)
        assert "mkstemp" not in text and "NamedTemporaryFile" not in text, py.name
