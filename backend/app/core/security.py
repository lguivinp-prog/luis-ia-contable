import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_password_hasher = PasswordHasher()


def hash_password(plain_password: str) -> str:
    return _password_hasher.hash(plain_password)


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
