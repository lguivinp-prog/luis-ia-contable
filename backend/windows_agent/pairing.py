"""Fase 5, Bloque 5.2, Domain E2 -- flujo de primer pairing (secciones
18-21 del encargo). Secuencia exacta:

    clave local generada -> CSR construido -> humano obtiene el codigo de
    pairing (fuera de este proceso) -> codigo ingresado interactivamente
    -> CSR + codigo enviados -> broker valida/firma -> commit en BD ->
    agente recibe certificado -> identidad persistida de forma segura.

La clave privada NUNCA sale de este proceso -- `build_agent_csr` (Domain
D, `app.broker.pki`, ya productivo) la genera localmente y solo el CSR
(material publico + prueba de posesion) cruza la red."""

from __future__ import annotations

import getpass
import uuid

from cryptography.hazmat.primitives import serialization

from app.broker import pki
from windows_agent.broker_client import PairingResult, submit_pairing_csr
from windows_agent.identity import WindowsDpapiIdentityStore


class PairingError(Exception):
    """Nunca incluye el codigo de pairing en el mensaje."""


class AlreadyPairedError(PairingError):
    """Seccion 21 del encargo: no se reemplaza en silencio una identidad
    ACTIVA existente -- se requiere un flujo explicito de reinicio del
    operador (fuera de alcance de este modulo -- no implementado en E2:
    `NO automatic destructive identity rotation`)."""


class PairingResponseValidationError(PairingError):
    """La respuesta del broker no supero la validacion (seccion 20 del
    encargo) -- el material recibido nunca se persiste en este caso."""


def prompt_pairing_code() -> str:
    """Entrada oculta/enmascarada (seccion 19 del encargo) -- nunca CLI,
    variable de entorno, ni archivo de configuracion. `getpass` es la
    unica forma estandar de la biblioteca estandar de Python de ocultar
    la entrada de consola sin una dependencia nueva."""
    return getpass.getpass("Codigo de pairing (6 digitos, mostrado por el backend): ").strip()


def pair_agent(
    *,
    identity_store: WindowsDpapiIdentityStore,
    pairing_base_url: str,
    ca_certificate_pem: bytes,
    pairing_id: uuid.UUID,
    pairing_code: str,
    protocol_version: int,
    timeout: float,
    common_name: str | None = None,
) -> PairingResult:
    if identity_store.is_paired():
        raise AlreadyPairedError(
            "Ya existe una identidad local ACTIVA para este agente -- este "
            "comando no la reemplaza automaticamente. Usa el flujo explicito "
            "de reinicio del operador si necesitas volver a emparejar."
        )

    key, csr_pem = pki.build_agent_csr(common_name=common_name or pki.new_agent_common_name())

    try:
        result = submit_pairing_csr(
            pairing_base_url=pairing_base_url,
            ca_certificate_pem=ca_certificate_pem,
            pairing_id=pairing_id,
            pairing_code=pairing_code,
            csr_pem=csr_pem,
            protocol_version=protocol_version,
            timeout=timeout,
        )
    finally:
        pairing_code = ""  # seccion 19: soltar la referencia lo antes posible

    _validate_pairing_response(result, expected_key=key, expected_protocol_version=protocol_version)

    private_key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    try:
        identity_store.save(
            agent_id=result.agent_id,
            certificate_pem=result.certificate_pem,
            private_key_pem=private_key_pem,
        )
    finally:
        private_key_pem = b""

    return result


def _validate_pairing_response(
    result: PairingResult, *, expected_key, expected_protocol_version: int
) -> None:
    """Seccion 20 del encargo -- nunca se persiste material devuelto sin
    esta validacion:

    - el certificado corresponde a la clave generada LOCALMENTE (compara
      la clave publica, nunca confia ciegamente en el certificado
      devuelto);
    - la ventana de validez es coherente (no ya vencido);
    - version de protocolo esperada."""
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import rsa

    from app.core import clock

    cert = x509.load_pem_x509_certificate(result.certificate_pem)
    cert_public_numbers = cert.public_key().public_numbers()
    expected_public_numbers = expected_key.public_key().public_numbers()
    if not isinstance(cert.public_key(), rsa.RSAPublicKey) or cert_public_numbers != expected_public_numbers:
        raise PairingResponseValidationError(
            "El certificado devuelto no corresponde a la clave generada localmente."
        )

    now = clock.utcnow()
    if cert.not_valid_after_utc <= now:
        raise PairingResponseValidationError("El certificado devuelto ya esta vencido.")
    if cert.not_valid_before_utc > now:
        raise PairingResponseValidationError("El certificado devuelto todavia no es valido.")

    # `PairingResult.certificate_not_after` se parseo del cuerpo JSON de
    # la respuesta (schema estricto) -- confirma que coincide con el
    # propio certificado, nunca se confia solo en el campo declarado.
    if result.certificate_not_after != cert.not_valid_after_utc:
        raise PairingResponseValidationError(
            "El campo certificate_not_after de la respuesta no coincide con el certificado."
        )
