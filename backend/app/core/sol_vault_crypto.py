"""Jerarquia criptografica de dos capas de la boveda SOL (Fase 5, Bloque
5.1, ver ADR-0068/ADR-0069). Validada experimentalmente en el Spike C
(rama `spike/phase5-sol-vault-crypto`, commit `2096a9b`) -- este modulo es
la implementacion productiva, no depende del spike en tiempo de ejecucion.

Jerarquia:

    slot KEK (Argon2id sobre la contrasena personal o el codigo de
    recuperacion)
        -> desenvuelve vault_root_key (32 bytes aleatorios, una por
           boveda, nunca cifra directamente ninguna credencial)
            -> desenvuelve credential_dek (32 bytes aleatorios, version
               ACTIVE unica por boveda)
                -> descifra sol_username / sol_password

Mismas primitivas ya en uso productivo en `app/core/integration_crypto.py`:
AES-256-GCM (`cryptography`) con sobre versionado + AAD obligatorio, y
Argon2id vía la API de bajo nivel de `argon2-cffi` (nunca
`argon2.PasswordHasher`, que administra su propia sal internamente y esta
pensada para verificar un hash de login, no para derivar una clave de
envoltura). Ninguna criptografia propia."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_APP_NAME = "luisiacontable"
_NONCE_LENGTH = 12
_FORMAT_VERSION = 1
_ARGON2_ALGORITHM_VERSION = 19  # Argon2 v1.3 (argon2.low_level.ARGON2_VERSION)

# Parametros Argon2id productivos iniciales (Seccion 5 del cierre del
# Spike C): medidos en el hardware de desarrollo dentro de la ventana
# objetivo 500-1000ms, con memoria moderada. Nunca disminuidos
# automaticamente por presion de memoria -- un cambio de estos valores es
# una decision explicita de despliegue, documentada en ADR-0069.
PRODUCTION_ARGON2_MEMORY_COST_KIB = 393216  # 384 MiB
PRODUCTION_ARGON2_TIME_COST = 2
PRODUCTION_ARGON2_PARALLELISM = 1
PRODUCTION_ARGON2_HASH_LEN = 32
PRODUCTION_ARGON2_KDF_VERSION = 1


class SolVaultCryptoError(Exception):
    """Error generico de cifrado/descifrado de la boveda SOL."""


class SolVaultDecryptionError(SolVaultCryptoError):
    """AAD, clave o ciphertext incorrectos, o formato no soportado -- fallo
    cerrado, nunca se reintenta con una clave alternativa ni se devuelve
    texto parcial."""


@dataclass(frozen=True)
class Argon2Params:
    """Parametros KDF versionados. `version` es la version del propio
    algoritmo Argon2 (19 = Argon2 v1.3); `kdf_version` (fuera de esta clase,
    columna independiente en `sol_vault_key_slots`) versiona un *conjunto*
    de parametros propio de esta aplicacion -- nunca confundir ambos."""

    time_cost: int
    memory_cost_kib: int
    parallelism: int
    hash_len: int = 32
    version: int = _ARGON2_ALGORITHM_VERSION


def production_argon2_params() -> Argon2Params:
    return Argon2Params(
        time_cost=PRODUCTION_ARGON2_TIME_COST,
        memory_cost_kib=PRODUCTION_ARGON2_MEMORY_COST_KIB,
        parallelism=PRODUCTION_ARGON2_PARALLELISM,
        hash_len=PRODUCTION_ARGON2_HASH_LEN,
    )


def derive_kek(password: str, salt: bytes, params: Argon2Params) -> bytes:
    return hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=params.time_cost,
        memory_cost=params.memory_cost_kib,
        parallelism=params.parallelism,
        hash_len=params.hash_len,
        type=Type.ID,
        version=params.version,
    )


def generate_salt(length: int = 16) -> bytes:
    return os.urandom(length)


def generate_key(length: int = 32) -> bytes:
    """Genera `vault_root_key`/`credential_dek` -- 32 bytes aleatorios."""
    return os.urandom(length)


def _seal(plaintext: bytes, key: bytes, aad: bytes) -> bytes:
    nonce = os.urandom(_NONCE_LENGTH)  # nunca reutilizado, generado en cada llamada
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    return bytes([_FORMAT_VERSION]) + nonce + ciphertext


def _open(envelope: bytes, key: bytes, aad: bytes) -> bytes:
    if len(envelope) < 1 + _NONCE_LENGTH:
        raise SolVaultDecryptionError("sobre de cifrado con formato invalido")
    format_version = envelope[0]
    if format_version != _FORMAT_VERSION:
        raise SolVaultDecryptionError(f"version de formato no soportada: {format_version}")
    nonce = envelope[1 : 1 + _NONCE_LENGTH]
    ciphertext = envelope[1 + _NONCE_LENGTH :]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, aad)
    except InvalidTag as exc:
        raise SolVaultDecryptionError("fallo de autenticacion GCM (AAD o clave incorrectos)") from exc


# --- construccion centralizada y determinista del AAD (Seccion 6) ---


def aad_wrapped_vault_root_key(
    *,
    slot_id: uuid.UUID,
    tenant_id: uuid.UUID,
    vault_id: uuid.UUID,
    user_id_or_recovery: str,
    kdf_version: int,
) -> bytes:
    parts = [
        _APP_NAME,
        "sol_vault_key_slots",
        str(slot_id),
        str(tenant_id),
        str(vault_id),
        user_id_or_recovery,
        str(kdf_version),
    ]
    return "|".join(parts).encode("utf-8")


def aad_wrapped_data_key(
    *, data_key_row_id: uuid.UUID, tenant_id: uuid.UUID, vault_id: uuid.UUID, data_key_version: int
) -> bytes:
    parts = [
        _APP_NAME,
        "sol_vault_data_keys",
        str(data_key_row_id),
        str(tenant_id),
        str(vault_id),
        str(data_key_version),
    ]
    return "|".join(parts).encode("utf-8")


def aad_credential_field(
    *,
    credential_id: uuid.UUID,
    tenant_id: uuid.UUID,
    client_id: uuid.UUID,
    field_name: str,
    data_key_version: int,
) -> bytes:
    parts = [
        _APP_NAME,
        "client_sol_credentials",
        str(credential_id),
        str(tenant_id),
        str(client_id),
        field_name,
        str(data_key_version),
    ]
    return "|".join(parts).encode("utf-8")


# --- operaciones de alto nivel de la jerarquia de 2 capas ---


def wrap_vault_root_key(vault_root_key: bytes, kek: bytes, aad: bytes) -> bytes:
    return _seal(vault_root_key, kek, aad)


def unwrap_vault_root_key(wrapped: bytes, kek: bytes, aad: bytes) -> bytes:
    return _open(wrapped, kek, aad)


def wrap_data_key(data_key: bytes, vault_root_key: bytes, aad: bytes) -> bytes:
    return _seal(data_key, vault_root_key, aad)


def unwrap_data_key(wrapped: bytes, vault_root_key: bytes, aad: bytes) -> bytes:
    return _open(wrapped, vault_root_key, aad)


def encrypt_credential_field(plaintext: str, credential_dek: bytes, aad: bytes) -> bytes:
    return _seal(plaintext.encode("utf-8"), credential_dek, aad)


def decrypt_credential_field(envelope: bytes, credential_dek: bytes, aad: bytes) -> str:
    return _open(envelope, credential_dek, aad).decode("utf-8")


def zeroize(buffer: bytearray) -> None:
    """Sobrescribe un `bytearray` mutable con ceros in-place. Mitigacion de
    mejor esfuerzo (ver ADR-0071): Python no garantiza el borrado fisico de
    todas las copias posibles en memoria (recolector de basura,
    copy-on-write al bifurcar procesos, memoria de intercambio, volcados de
    nucleo quedan fuera de este control) -- reduce la ventana de
    exposicion, no la elimina de forma absoluta."""
    for i in range(len(buffer)):
        buffer[i] = 0
