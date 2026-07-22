import hashlib
import secrets
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_password_hasher = PasswordHasher()

# Contrasena fija usada unicamente para generar el hash ficticio de
# mitigacion de enumeracion por tiempo (ver ADR-0009). No es un secreto: no
# protege ninguna cuenta real, solo sirve para que verify_password() tenga un
# costo Argon2 equivalente cuando el usuario no existe.
_DUMMY_PASSWORD_PLACEHOLDER = "not-a-real-account-9f3a7c2e1b6d4f58"


def hash_password(plain_password: str) -> str:
    return _password_hasher.hash(plain_password)


@lru_cache(maxsize=1)
def get_dummy_password_hash() -> str:
    """Hash Argon2id calculado una sola vez (no en cada solicitud) y reutilizado
    para verificar contra el cuando no existe una cuenta real, igualando el
    costo computacional frente a una contrasena incorrecta de una cuenta real."""
    return hash_password(_DUMMY_PASSWORD_PLACEHOLDER)


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, plain_password)
    except VerifyMismatchError:
        return False


def generate_session_token() -> str:
    """Token opaco de sesion. El valor crudo solo vive en la cookie del navegador."""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """Hash SHA-256 del token de sesion, el unico valor que se persiste en la base de datos."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
