"""Double-submit cookie CSRF (ver ADR-0010). Defensa en profundidad sobre
SameSite=Strict; la verificacion ocurre siempre en el backend (dependencia
FastAPI), nunca depende de codigo del lado del frontend."""

import hashlib
import hmac
import secrets


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def hash_csrf_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_csrf_token(provided_token: str | None, expected_hash: str) -> bool:
    if not provided_token:
        return False
    return hmac.compare_digest(hash_csrf_token(provided_token), expected_hash)
