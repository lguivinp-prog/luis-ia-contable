"""Fase 5, Bloque 5.2, Domain D -- pruebas unitarias de `app/broker/pki.py`:
fail-closed en arranque ante material de CA ausente/invalido, ida-y-vuelta
real de guardado/carga en disco, y validacion de CSR (tamano de clave,
extensiones solicitadas, firma). Ninguna prueba de este archivo requiere
PostgreSQL ni red -- pura criptografia/filesystem."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.broker import pki


def test_load_ca_fails_closed_when_directory_is_empty(tmp_path: Path):
    with pytest.raises(pki.CaMaterialMissingError):
        pki.load_ca(ca_dir=tmp_path)


def test_load_ca_fails_closed_when_key_file_is_corrupt(tmp_path: Path):
    ca = pki.generate_ca()
    pki.save_ca(ca, ca_dir=tmp_path)
    (tmp_path / "ca.key.pem").write_bytes(b"not a real PEM key")

    with pytest.raises(pki.CaMaterialInvalidError):
        pki.load_ca(ca_dir=tmp_path)


def test_save_then_load_ca_round_trip_produces_the_same_certificate(tmp_path: Path):
    ca = pki.generate_ca(common_name="round trip CA")
    pki.save_ca(ca, ca_dir=tmp_path)

    loaded = pki.load_ca(ca_dir=tmp_path)
    assert loaded.certificate.fingerprint(hashes.SHA256()) == ca.certificate.fingerprint(hashes.SHA256())
    assert loaded.certificate.subject == ca.certificate.subject


def test_load_or_generate_ca_only_generates_when_directory_is_empty(tmp_path: Path):
    first = pki.load_or_generate_ca(ca_dir=tmp_path)
    second = pki.load_or_generate_ca(ca_dir=tmp_path)
    assert first.certificate.fingerprint(hashes.SHA256()) == second.certificate.fingerprint(hashes.SHA256())


def test_valid_csr_is_accepted_and_signed():
    ca = pki.generate_ca()
    _key, csr_pem = pki.build_agent_csr(common_name="valid-agent")
    cert = pki.sign_agent_csr(ca=ca, csr_pem=csr_pem, agent_common_name="broker-assigned-name")
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "broker-assigned-name"


def test_csr_below_minimum_rsa_key_size_is_rejected():
    ca = pki.generate_ca()
    small_key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "weak-key-agent")]))
        .sign(small_key, hashes.SHA256())
    )
    from cryptography.hazmat.primitives import serialization

    csr_pem = csr.public_bytes(serialization.Encoding.PEM)

    with pytest.raises(pki.CsrInvalidError):
        pki.sign_agent_csr(ca=ca, csr_pem=csr_pem, agent_common_name="x")


def test_csr_requesting_any_extension_is_rejected():
    ca = pki.generate_ca()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "extension-agent")]))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("harmless.example")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    from cryptography.hazmat.primitives import serialization

    csr_pem = csr.public_bytes(serialization.Encoding.PEM)

    with pytest.raises(pki.CsrInvalidError):
        pki.sign_agent_csr(ca=ca, csr_pem=csr_pem, agent_common_name="x")


def test_malformed_csr_bytes_are_rejected():
    ca = pki.generate_ca()
    with pytest.raises(pki.CsrInvalidError):
        pki.sign_agent_csr(ca=ca, csr_pem=b"not a real CSR at all", agent_common_name="x")


def test_signed_agent_certificate_is_bounded_never_eternal():
    ca = pki.generate_ca()
    _key, csr_pem = pki.build_agent_csr(common_name="ttl-agent")
    cert = pki.sign_agent_csr(ca=ca, csr_pem=csr_pem, agent_common_name="ttl-agent")
    lifetime = cert.not_valid_after_utc - cert.not_valid_before_utc
    assert lifetime.days <= pki.AGENT_CERT_TTL_DAYS + 1
    assert lifetime.days > 0


def test_agent_certificate_has_no_ca_basic_constraint():
    ca = pki.generate_ca()
    _key, csr_pem = pki.build_agent_csr(common_name="not-a-ca")
    cert = pki.sign_agent_csr(ca=ca, csr_pem=csr_pem, agent_common_name="not-a-ca")
    basic_constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert basic_constraints.ca is False
