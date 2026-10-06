"""ARNES DE PRUEBA UNICAMENTE (Fase 5, Bloque 5.2, Domain E2 / E2.1-A):
NUNCA la implementacion productiva. `WindowsDpapiIdentityStore`
(`windows_agent/identity.py`) requiere DPAPI real de Windows, no
disponible en el entorno de pruebas Linux/Docker. Este arnes reutiliza el
MISMO codigo productivo de cifrado y de construccion del contexto mTLS
(`encrypt_private_key_pem` / `build_mtls_client_context`): la clave
privada tampoco se escribe aqui en claro. UNICA diferencia: la frase de
paso se guarda sin proteger (en lugar de DPAPI). La proteccion DPAPI real
se valida por separado, directamente en Windows."""

from __future__ import annotations

import json
import ssl
import uuid
from pathlib import Path

from cryptography import x509

from windows_agent.identity import (
    AgentIdentity,
    IdentityCorruptError,
    NotPairedError,
    build_mtls_client_context,
    encrypt_private_key_pem,
)


class FakeIdentityStore:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._cert_path = data_dir / "agent.cert.pem"
        self._key_path = data_dir / "agent.key.pem"  # PKCS#8 CIFRADO, nunca en claro
        self._passphrase_path = data_dir / "agent.keypass"  # arnes de prueba: sin DPAPI
        self._meta_path = data_dir / "identity.json"

    def is_paired(self) -> bool:
        return (
            self._cert_path.is_file()
            and self._key_path.is_file()
            and self._passphrase_path.is_file()
            and self._meta_path.is_file()
        )

    def save(self, *, agent_id: uuid.UUID, certificate_pem: bytes, private_key_pem: bytes) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        encrypted_pem, passphrase = encrypt_private_key_pem(private_key_pem)
        self._cert_path.write_bytes(certificate_pem)
        self._key_path.write_bytes(encrypted_pem)
        self._passphrase_path.write_bytes(passphrase)
        self._meta_path.write_text(json.dumps({"agent_id": str(agent_id)}), encoding="ascii")

    def load(self) -> AgentIdentity:
        if not self.is_paired():
            raise NotPairedError()
        try:
            meta = json.loads(self._meta_path.read_text(encoding="ascii"))
            certificate_pem = self._cert_path.read_bytes()
            cert = x509.load_pem_x509_certificate(certificate_pem)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise IdentityCorruptError() from exc
        return AgentIdentity(
            agent_id=uuid.UUID(meta["agent_id"]),
            certificate_pem=certificate_pem,
            certificate_not_after=cert.not_valid_after_utc,
        )

    def build_client_ssl_context(self, *, ca_certificate_pem: bytes) -> ssl.SSLContext:
        if not self.is_paired():
            raise NotPairedError()
        return build_mtls_client_context(
            ca_certificate_pem=ca_certificate_pem,
            certificate_path=self._cert_path,
            encrypted_key_path=self._key_path,
            passphrase=self._passphrase_path.read_bytes(),
        )
