"""Adaptador real contra los endpoints OAuth/identidad de Google, via httpx
directo (sin SDK oficial de Google -- ver Seccion 26 del plan v3). Aplica
PKCE (`code_challenge`/`S256`) junto con `client_secret`, siguiendo la Opcion
A descrita en la Seccion 27 del plan.

IMPORTANTE (correccion de la ronda de implementacion, Seccion 2): la
aceptacion real de PKCE por un cliente OAuth de Google de tipo Web NO ha
sido verificada empiricamente en esta sesion -- no existe un cliente OAuth
real ni una cuenta Google disponible para ejecutar el spike de la Seccion 27
del plan. Este adaptador implementa el flujo documentado (Opcion A), pero su
funcionamiento real contra Google queda como validacion manual pendiente
antes de producción (ver ADR-0063 y el informe de implementacion)."""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlencode

import httpx

from app.core import clock
from app.core.config import get_settings
from app.domain.google_oauth.provider import (
    GoogleIdentity,
    OAuthProviderError,
    OAuthTokenExchangeError,
    OAuthTokenResult,
    OAuthUserInfoError,
)

_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
_REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"

_REQUEST_TIMEOUT_SECONDS = 10.0
_MAX_RETRIES = 2


class GoogleOAuthProvider:
    def build_authorization_url(
        self,
        *,
        state: str,
        code_challenge: str,
        scopes: list[str],
        redirect_uri: str,
        prompt_consent: bool,
    ) -> str:
        settings = get_settings()
        # Nunca incluir client_secret aqui -- solo se usa en el intercambio
        # de token, exclusivamente en el backend (ver correccion Seccion 2).
        params = {
            "client_id": settings.google_oauth_client_id,
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
        return f"{_AUTHORIZATION_ENDPOINT}?{urlencode(params)}"

    def _post_token_endpoint(self, data: dict[str, str]) -> dict:
        settings = get_settings()
        payload = {
            **data,
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
        }
        last_error: Exception | None = None
        for _ in range(_MAX_RETRIES + 1):
            try:
                with httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                    response = client.post(_TOKEN_ENDPOINT, data=payload)
                if response.status_code >= 500:
                    last_error = OAuthTokenExchangeError(
                        f"error del servidor de Google: {response.status_code}"
                    )
                    continue
                if response.status_code >= 400:
                    raise OAuthTokenExchangeError(
                        f"Google rechazo la solicitud de token: {response.status_code}"
                    )
                return response.json()
            except httpx.TimeoutException as exc:
                last_error = OAuthTokenExchangeError("timeout al contactar el endpoint de token")
                last_error.__cause__ = exc
            except httpx.HTTPError as exc:
                last_error = OAuthTokenExchangeError("error de red al contactar el endpoint de token")
                last_error.__cause__ = exc
        raise last_error or OAuthTokenExchangeError("fallo desconocido en el endpoint de token")

    def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> OAuthTokenResult:
        body = self._post_token_endpoint(
            {
                "code": code,
                "code_verifier": code_verifier,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            }
        )
        return self._parse_token_response(body)

    def refresh_access_token(self, *, refresh_token: str) -> OAuthTokenResult:
        body = self._post_token_endpoint(
            {"refresh_token": refresh_token, "grant_type": "refresh_token"}
        )
        return self._parse_token_response(body)

    @staticmethod
    def _parse_token_response(body: dict) -> OAuthTokenResult:
        access_token = body.get("access_token")
        if not access_token:
            raise OAuthTokenExchangeError("respuesta de Google sin access_token")
        expires_in = int(body.get("expires_in", 3600))
        raw_scope = body.get("scope")
        scopes_granted = tuple(raw_scope.split()) if raw_scope else None
        return OAuthTokenResult(
            access_token=access_token,
            refresh_token=body.get("refresh_token"),
            access_token_expires_at=clock.utcnow() + timedelta(seconds=expires_in),
            scopes_granted=scopes_granted,
        )

    def get_user_info(self, *, access_token: str) -> GoogleIdentity:
        try:
            with httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                response = client.get(
                    _USERINFO_ENDPOINT, headers={"Authorization": f"Bearer {access_token}"}
                )
        except httpx.HTTPError as exc:
            raise OAuthUserInfoError("error de red al consultar userinfo") from exc
        if response.status_code != 200:
            raise OAuthUserInfoError(f"userinfo devolvio {response.status_code}")
        body = response.json()
        sub = body.get("sub")
        email = body.get("email")
        if not sub or not email:
            raise OAuthUserInfoError("userinfo no devolvio sub/email")
        return GoogleIdentity(
            sub=sub, email=email, email_verified=bool(body.get("email_verified", False))
        )

    def revoke_token(self, *, token: str) -> None:
        try:
            with httpx.Client(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                client.post(_REVOKE_ENDPOINT, data={"token": token})
        except httpx.HTTPError:
            # Mejor esfuerzo: una revocacion remota fallida nunca bloquea la
            # desconexion local (ver Seccion 7 del plan v3).
            pass


def get_oauth_provider() -> GoogleOAuthProvider | object:
    """Selector segun `GOOGLE_OAUTH_PROVIDER` (mismo patron que
    `ruc_lookup_provider`, ver app/core/config.py)."""
    from app.domain.google_oauth.fake_provider import FakeOAuthProvider

    settings = get_settings()
    if settings.google_oauth_provider == "google":
        return GoogleOAuthProvider()
    return FakeOAuthProvider()


__all__ = ["GoogleOAuthProvider", "OAuthProviderError", "get_oauth_provider"]
