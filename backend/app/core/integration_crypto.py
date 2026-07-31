"""Cifrado de credenciales de integraciones externas (Fase 3 / Bloque 3.1,
ver ADR-0064). Sobre versionado AES-256-GCM con AAD obligatorio -- ningun
ciphertext puede moverse de fila/tabla/tenant/usuario/tipo de token sin que
la autenticacion de GCM falle de inmediato. No existe rotacion automatica de
clave en v1: una sola clave activa (`INTEGRATION_TOKEN_ENCRYPTION_KEY` +
`INTEGRATION_TOKEN_KEY_ID`).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

_FORMAT_VERSION = 1
_NONCE_LENGTH = 12
_APP_NAME = "luisiacontable"
_FINGERPRINT_CONTEXT = b"luisiacontable:integration-token-key:v1"


class IntegrationCryptoError(Exception):
    """Error generico de cifrado/descifrado de credenciales de integracion."""


class IntegrationKeyMissingError(IntegrationCryptoError):
    """La clave de cifrado no esta configurada o tiene un formato invalido."""


class IntegrationDecryptionError(IntegrationCryptoError):
    """El descifrado fallo: AAD incorrecto, clave incorrecta, o dato corrupto."""


@dataclass(frozen=True)
class EncryptionContext:
    """Datos que forman el AAD -- atan el ciphertext a esta fila exacta."""

    table_name: str
    row_id: str
    tenant_id: str
    user_id: str
    token_type: str  # "ACCESS" | "REFRESH" | "PKCE_VERIFIER"


def _build_aad(context: EncryptionContext, key_id: str) -> bytes:
    parts = [
        _APP_NAME,
        context.table_name,
        context.row_id,
        context.tenant_id,
        context.user_id,
        context.token_type,
        key_id,
    ]
    return "|".join(parts).encode("utf-8")


def _load_active_key() -> bytes:
    raw = get_settings().integration_token_encryption_key
    if not raw:
        raise IntegrationKeyMissingError("INTEGRATION_TOKEN_ENCRYPTION_KEY no esta configurada")
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as exc:  # noqa: BLE001 -- cualquier fallo de decodificacion es config invalida
        raise IntegrationKeyMissingError("INTEGRATION_TOKEN_ENCRYPTION_KEY no es base64 valido") from exc
    if len(key) != 32:
        raise IntegrationKeyMissingError(
            "INTEGRATION_TOKEN_ENCRYPTION_KEY debe decodificar a exactamente 32 bytes"
        )
    return key


def active_key_id() -> str:
    key_id = get_settings().integration_token_key_id
    if not key_id:
        raise IntegrationKeyMissingError("INTEGRATION_TOKEN_KEY_ID no esta configurado")
    return key_id


def is_encryption_configured() -> bool:
    """True solo si la clave activa es valida (32 bytes) y tiene un key_id no vacio."""
    try:
        _load_active_key()
        active_key_id()
    except IntegrationKeyMissingError:
        return False
    return True


def encrypt_secret(plaintext: str, context: EncryptionContext) -> tuple[bytes, str]:
    """Cifra `plaintext` bajo la clave activa. Devuelve (envelope, key_id)."""
    key = _load_active_key()
    key_id = active_key_id()
    nonce = os.urandom(_NONCE_LENGTH)
    aad = _build_aad(context, key_id)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad)
    envelope = bytes([_FORMAT_VERSION]) + nonce + ciphertext
    return envelope, key_id


def decrypt_secret(envelope: bytes, key_id: str, context: EncryptionContext) -> str:
    """Descifra un sobre previamente producido por `encrypt_secret`.

    Solo funciona si `key_id` coincide con la clave activa actual y si el AAD
    reconstruido a partir de `context` coincide exactamente con el usado al
    cifrar -- cualquier discrepancia (fila, tabla, tenant, usuario, tipo de
    token o key_id distintos) hace fallar la autenticacion de GCM.
    """
    if key_id != active_key_id():
        raise IntegrationDecryptionError(f"key_id de la fila ({key_id}) no coincide con la clave activa")
    key = _load_active_key()
    if len(envelope) < 1 + _NONCE_LENGTH:
        raise IntegrationDecryptionError("sobre de cifrado con formato invalido")
    format_version = envelope[0]
    if format_version != _FORMAT_VERSION:
        raise IntegrationDecryptionError(f"version de sobre no soportada: {format_version}")
    nonce = envelope[1 : 1 + _NONCE_LENGTH]
    ciphertext = envelope[1 + _NONCE_LENGTH :]
    aad = _build_aad(context, key_id)
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
    except InvalidTag as exc:
        raise IntegrationDecryptionError("fallo de autenticacion GCM (AAD o clave incorrectos)") from exc
    return plaintext.decode("utf-8")


def compute_key_fingerprint(key_b64: str | None = None) -> str:
    """HMAC-SHA256(clave activa, contexto fijo) en hexadecimal.

    No secreto (de un solo sentido, no permite reconstruir la clave) --
    permite a backup/restore detectar que dos entornos con el mismo
    `key_id` en realidad tienen valores de clave distintos (ver ADR-0064,
    Seccion 14.3 del plan).
    """
    raw = key_b64 if key_b64 is not None else get_settings().integration_token_encryption_key
    if not raw:
        raise IntegrationKeyMissingError("INTEGRATION_TOKEN_ENCRYPTION_KEY no esta configurada")
    key = base64.b64decode(raw, validate=True)
    return hmac.new(key, _FINGERPRINT_CONTEXT, hashlib.sha256).hexdigest()
