"""Parsing de la respuesta de token de Google (Fase 3 / Bloque 3.1, ver
ADR-0063): un `id_token` presente en la respuesta se ignora explicitamente
-- nunca se interpreta ni se persiste, `OAuthTokenResult` no tiene ningun
campo para transportarlo."""

from app.domain.google_oauth.google_provider import GoogleOAuthProvider
from app.domain.google_oauth.provider import OAuthTokenResult


def test_id_token_in_response_is_ignored():
    body = {
        "access_token": "fake-access-token",
        "refresh_token": "fake-refresh-token",
        "expires_in": 3600,
        "scope": "openid email",
        "id_token": "eyJhbGciOiJSUzI1NiIsImtpZCI6ImZha2Uta2lkIn0.fake.payload",
    }
    result = GoogleOAuthProvider._parse_token_response(body)
    assert isinstance(result, OAuthTokenResult)
    assert not hasattr(result, "id_token")
    assert result.access_token == "fake-access-token"
    assert result.refresh_token == "fake-refresh-token"
    assert result.scopes_granted == ("openid", "email")


def test_oauth_token_result_has_no_id_token_field():
    field_names = {f for f in OAuthTokenResult.__dataclass_fields__}
    assert "id_token" not in field_names
    assert field_names == {"access_token", "refresh_token", "access_token_expires_at", "scopes_granted"}
