"""Verificador de pairing del agente Windows (Fase 5, Bloque 5.2, ver
docs/phase5/block5_2/BLOCK5_2_DATA_MODEL_DRAFT.md seccion 4 -- Domain A.3,
Pairing Verifier Cryptographic Freeze). HMAC-SHA-256 keyed, no Argon2id, no
hash simple -- ver la justificacion completa en el documento congelado.

Politica de clave: mismo formato ya exigido por
`integration_token_encryption_key` (ADR-0064, unico precedente real de
politica de secretos de este repo) -- base64 estandar que decodifica a
exactamente 32 bytes. `PAIRING_VERIFIER_KEY` es una clave de proposito
especifico, nunca reutiliza `session_secret`, `integration_token_encryption_key`,
ni ninguna clave del vault SOL (evita domain confusion, ver Domain A.3
seccion 6)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid

from app.core.config import get_settings

_DOMAIN_LABEL = b"sol-agent-pairing:v1"
_SEPARATOR = b"\x1f"  # ASCII Unit Separator -- fuera del alfabeto de UUIDs/digitos
_KEY_LENGTH_BYTES = 32
_PAIRING_CODE_DIGITS = 6
_PAIRING_CODE_MODULUS = 10**_PAIRING_CODE_DIGITS


class PairingCryptoError(Exception):
    """Error generico del verificador de pairing."""


class PairingVerifierKeyMisconfiguredError(PairingCryptoError):
    """`PAIRING_VERIFIER_KEY` falta o tiene un formato invalido -- el pairing
    debe fallar cerrado, nunca operar sin clave ni con un fallback inseguro."""


def _load_verifier_key() -> bytes:
    raw = get_settings().pairing_verifier_key
    if not raw:
        raise PairingVerifierKeyMisconfiguredError("PAIRING_VERIFIER_KEY no esta configurada")
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as exc:  # noqa: BLE001 -- cualquier fallo de decodificacion es config invalida
        raise PairingVerifierKeyMisconfiguredError("PAIRING_VERIFIER_KEY no es base64 valido") from exc
    if len(key) != _KEY_LENGTH_BYTES:
        raise PairingVerifierKeyMisconfiguredError(
            f"PAIRING_VERIFIER_KEY debe decodificar a exactamente {_KEY_LENGTH_BYTES} bytes"
        )
    return key


def is_pairing_configured() -> bool:
    """True solo si `PAIRING_VERIFIER_KEY` es valida -- usado para fallar
    cerrado ANTES de generar un codigo/verificador, no despues."""
    try:
        _load_verifier_key()
    except PairingVerifierKeyMisconfiguredError:
        return False
    return True


def generate_pairing_code() -> str:
    """6 digitos decimales ASCII, ceros a la izquierda permitidos.
    `secrets.randbelow` -- generador criptograficamente seguro de la
    biblioteca estandar, nunca `random`, contadores, timestamps, ni
    truncamiento de UUID."""
    return f"{secrets.randbelow(_PAIRING_CODE_MODULUS):0{_PAIRING_CODE_DIGITS}d}"


def _canonical_message(*, tenant_id: uuid.UUID, pairing_id: uuid.UUID, code: str) -> bytes:
    """Mensaje canonico congelado (Domain A.3, seccion 5): dominio + UUID de
    tenant + UUID de pairing + codigo, separados por 0x1F. Representacion de
    UUID: minusculas, formato estandar con guiones (`str(uuid.UUID)`).
    NUNCA se persiste este mensaje -- solo el HMAC resultante."""
    if len(code) != _PAIRING_CODE_DIGITS or not code.isdigit():
        raise PairingCryptoError("el codigo de pairing debe ser exactamente 6 digitos decimales")
    return _SEPARATOR.join(
        [
            _DOMAIN_LABEL,
            str(tenant_id).lower().encode("utf-8"),
            str(pairing_id).lower().encode("utf-8"),
            code.encode("utf-8"),
        ]
    )


def compute_pairing_verifier(*, tenant_id: uuid.UUID, pairing_id: uuid.UUID, code: str) -> str:
    """`hex(HMAC-SHA-256(PAIRING_VERIFIER_KEY, mensaje_canonico))`. Devuelve
    el digest hexadecimal -- el unico valor que se persiste en
    `pairing_code_verifier`."""
    key = _load_verifier_key()
    message = _canonical_message(tenant_id=tenant_id, pairing_id=pairing_id, code=code)
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def verify_pairing_code(
    *, tenant_id: uuid.UUID, pairing_id: uuid.UUID, code: str, stored_verifier: str
) -> bool:
    """Recalcula el HMAC sobre el mismo mensaje canonico y compara en tiempo
    constante contra `stored_verifier` (`hmac.compare_digest`, nunca `==`).
    Un codigo con formato invalido (longitud/no-digitos) se rechaza sin
    lanzar excepcion -- mismo resultado que un codigo incorrecto, para no
    distinguir "formato invalido" de "codigo incorrecto" ante un llamador
    que ya paso la validacion de forma en la capa de entrada."""
    try:
        candidate = compute_pairing_verifier(tenant_id=tenant_id, pairing_id=pairing_id, code=code)
    except PairingCryptoError:
        return False
    return hmac.compare_digest(candidate, stored_verifier)
