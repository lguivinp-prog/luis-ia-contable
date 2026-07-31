"""Puerto de dominio para el proveedor OAuth/identidad de Google (Fase 3 /
Bloque 3.1, ver ADR-0062/ADR-0063). Cualquier adaptador (simulado o real)
implementa este contrato -- el dominio y los routers nunca llaman
directamente a un SDK o cliente HTTP de Google."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class OAuthProviderError(Exception):
    """Error generico normalizado del proveedor OAuth."""


class OAuthAuthorizationDeniedError(OAuthProviderError):
    """El usuario denego el consentimiento, o Google devolvio un error en el callback."""


class OAuthTokenExchangeError(OAuthProviderError):
    """El intercambio de codigo o el refresco de token fallo (code/verifier/refresh_token invalido)."""


class OAuthUserInfoError(OAuthProviderError):
    """La consulta al endpoint userinfo fallo o devolvio datos incompletos."""


@dataclass(frozen=True)
class OAuthTokenResult:
    """Resultado normalizado de un intercambio de codigo o un refresco de
    token. Nunca contiene datos de identidad -- eso se obtiene por separado
    via `get_user_info` (ver Seccion 8 del plan v3)."""

    access_token: str
    refresh_token: str | None
    access_token_expires_at: datetime
    # None significa "la respuesta no incluyo una lista de scopes" (el
    # llamador debe conservar los scopes_granted previos) -- distinto de una
    # lista vacia, que significaria "Google concedio cero scopes".
    scopes_granted: tuple[str, ...] | None


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    email_verified: bool


class OAuthProvider(Protocol):
    def build_authorization_url(
        self,
        *,
        state: str,
        code_challenge: str,
        scopes: list[str],
        redirect_uri: str,
        prompt_consent: bool,
    ) -> str:
        """Construye la URL de autorizacion. NUNCA incluye client_secret."""
        ...

    def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> OAuthTokenResult: ...

    def refresh_access_token(self, *, refresh_token: str) -> OAuthTokenResult: ...

    def get_user_info(self, *, access_token: str) -> GoogleIdentity: ...

    def revoke_token(self, *, token: str) -> None:
        """Revocacion de mejor esfuerzo -- una falla aqui nunca debe impedir
        la desconexion local (ver Seccion 7 del plan v3)."""
        ...
