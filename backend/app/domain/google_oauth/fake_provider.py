"""Proveedor OAuth Google simulado, deterministico, sin ninguna llamada de
red -- para desarrollo y para la suite de pruebas automatica (ver Seccion 19
del plan v3: ninguna prueba normal depende de una cuenta Google real)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode

from app.core import clock
from app.domain.google_oauth.provider import (
    GoogleIdentity,
    OAuthTokenExchangeError,
    OAuthTokenResult,
    OAuthUserInfoError,
)


@dataclass(frozen=True)
class FakeAuthorizationCode:
    """Configuracion de lo que debe ocurrir cuando este `code` se intercambie.

    `issue_refresh_token=False` simula una respuesta de Google sin
    `refresh_token` (usada para probar REFRESH_TOKEN_REQUIRED en la primera
    conexion). `email_verified=False` simula una cuenta sin verificar."""

    sub: str
    email: str
    email_verified: bool = True
    scopes: tuple[str, ...] = ()
    issue_refresh_token: bool = True
    access_token_ttl_seconds: int = 3600


@dataclass
class _IssuedTokenIdentity:
    sub: str
    email: str
    email_verified: bool


class FakeOAuthProvider:
    def __init__(self) -> None:
        self._pending_codes: dict[str, FakeAuthorizationCode] = {}
        self._refresh_tokens: dict[str, _IssuedTokenIdentity] = {}
        self._access_tokens: dict[str, _IssuedTokenIdentity] = {}
        self._revoked_tokens: set[str] = set()

    def register_authorization_code(self, code: str, config: FakeAuthorizationCode) -> None:
        self._pending_codes[code] = config

    def build_authorization_url(
        self,
        *,
        state: str,
        code_challenge: str,
        scopes: list[str],
        redirect_uri: str,
        prompt_consent: bool,
    ) -> str:
        params = {
            "client_id": "fake-client-id",
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
            "access_type": "offline",
            "include_granted_scopes": "true",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if prompt_consent:
            params["prompt"] = "consent"
        return f"https://fake-google-authorize.invalid/o/oauth2/v2/auth?{urlencode(params)}"

    def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> OAuthTokenResult:
        del redirect_uri  # el fake no valida coherencia de redirect_uri
        if not code_verifier:
            raise OAuthTokenExchangeError("code_verifier requerido")
        config = self._pending_codes.pop(code, None)
        if config is None:
            raise OAuthTokenExchangeError("invalid_grant: code desconocido o ya consumido")
        identity = _IssuedTokenIdentity(
            sub=config.sub, email=config.email, email_verified=config.email_verified
        )
        access_token = f"fake-access-{uuid.uuid4()}"
        self._access_tokens[access_token] = identity
        refresh_token = None
        if config.issue_refresh_token:
            refresh_token = f"fake-refresh-{uuid.uuid4()}"
            self._refresh_tokens[refresh_token] = identity
        return OAuthTokenResult(
            access_token=access_token,
            refresh_token=refresh_token,
            access_token_expires_at=clock.utcnow() + timedelta(seconds=config.access_token_ttl_seconds),
            scopes_granted=tuple(config.scopes),
        )

    def refresh_access_token(self, *, refresh_token: str) -> OAuthTokenResult:
        identity = self._refresh_tokens.get(refresh_token)
        if identity is None:
            raise OAuthTokenExchangeError("invalid_grant: refresh_token desconocido o revocado")
        access_token = f"fake-access-{uuid.uuid4()}"
        self._access_tokens[access_token] = identity
        return OAuthTokenResult(
            access_token=access_token,
            # Un refresco real de Google normalmente NO devuelve un nuevo
            # refresh_token ni una lista de scopes -- se simula ese
            # comportamiento por defecto (ver Seccion 9 del plan v3).
            refresh_token=None,
            access_token_expires_at=clock.utcnow() + timedelta(hours=1),
            scopes_granted=None,
        )

    def get_user_info(self, *, access_token: str) -> GoogleIdentity:
        identity = self._access_tokens.get(access_token)
        if identity is None or access_token in self._revoked_tokens:
            raise OAuthUserInfoError("access_token invalido o revocado")
        return GoogleIdentity(
            sub=identity.sub, email=identity.email, email_verified=identity.email_verified
        )

    def revoke_token(self, *, token: str) -> None:
        self._revoked_tokens.add(token)
        self._access_tokens.pop(token, None)
        self._refresh_tokens.pop(token, None)
