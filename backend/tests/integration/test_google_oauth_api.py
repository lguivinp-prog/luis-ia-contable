"""API HTTP de integracion Google OAuth (Fase 3 / Bloque 3.1): permisos,
ownership, restriccion de capability, Google no configurado, ausencia de
tokens en cualquier respuesta JSON."""

import base64
import os
from urllib.parse import parse_qs, urlparse

import pytest

from app.core import integration_crypto
from app.core.dependencies import get_fake_oauth_provider_singleton, reset_fake_oauth_provider_singleton
from app.domain.google_oauth.fake_provider import FakeAuthorizationCode

_STATUS_URL = "/api/v1/integrations/google/status"
_AUTHORIZE_URL = "/api/v1/integrations/google/authorize"
_CALLBACK_URL = "/api/v1/integrations/google/callback"
_DISCONNECT_URL = "/api/v1/integrations/google/disconnect"


@pytest.fixture(autouse=True)
def _isolated_fake_provider():
    reset_fake_oauth_provider_singleton()
    yield
    reset_fake_oauth_provider_singleton()


@pytest.fixture(autouse=True)
def _configured_encryption_key(monkeypatch):
    settings = integration_crypto.get_settings()
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setattr(settings, "integration_token_encryption_key", key, raising=False)
    monkeypatch.setattr(settings, "integration_token_key_id", "v1", raising=False)


def _authorize(auth_client):
    response = auth_client.post(
        _AUTHORIZE_URL, json={"capability": "IDENTITY", "return_path": "/settings/integrations/google"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    state = parse_qs(urlparse(body["authorization_url"]).query)["state"][0]
    return state


def _complete_via_http(auth_client, *, sub="sub-http-1", email="http@example.com"):
    state = _authorize(auth_client)
    code = f"code-{state[:8]}"
    get_fake_oauth_provider_singleton().register_authorization_code(
        code, FakeAuthorizationCode(sub=sub, email=email, scopes=("openid", "email"))
    )
    response = auth_client.get(_CALLBACK_URL, params={"state": state, "code": code}, follow_redirects=False)
    assert response.status_code == 302
    return response


def test_status_requires_authentication(client) -> None:
    response = client.get(_STATUS_URL)
    assert response.status_code == 401


def test_status_disconnected_by_default(auth_client) -> None:
    response = auth_client.get(_STATUS_URL)
    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is False
    assert body["identity_authorized"] is False


def test_status_configured_false_when_client_id_and_secret_empty(auth_client, monkeypatch) -> None:
    """GOOGLE_LIVE_VALIDATION_DEFERRED (ADR-0063): sin credenciales OAuth
    reales, el estado publico debe reportar configured=false -- nunca 500,
    nunca una llamada de red -- para que la UI pueda mostrar "Google no
    configurado" en vez de ofrecer un boton que termine en error."""
    settings = integration_crypto.get_settings()
    monkeypatch.setattr(settings, "google_oauth_client_id", "", raising=False)
    monkeypatch.setattr(settings, "google_oauth_client_secret", "", raising=False)
    response = auth_client.get(_STATUS_URL)
    assert response.status_code == 200
    assert response.json()["configured"] is False


def test_status_configured_true_when_client_id_and_secret_present(auth_client, monkeypatch) -> None:
    settings = integration_crypto.get_settings()
    monkeypatch.setattr(settings, "google_oauth_client_id", "real-client-id", raising=False)
    monkeypatch.setattr(settings, "google_oauth_client_secret", "real-client-secret", raising=False)
    response = auth_client.get(_STATUS_URL)
    assert response.status_code == 200
    assert response.json()["configured"] is True


def test_status_never_500_without_any_integration_configuration(auth_client, monkeypatch) -> None:
    """Peor caso: instalacion nueva sin credenciales Google NI clave de
    cifrado configuradas. GET /status nunca debe fallar -- solo authorize/
    callback/disconnect dependen de la clave de cifrado."""
    settings = integration_crypto.get_settings()
    monkeypatch.setattr(settings, "google_oauth_client_id", "", raising=False)
    monkeypatch.setattr(settings, "google_oauth_client_secret", "", raising=False)
    monkeypatch.setattr(settings, "integration_token_encryption_key", "", raising=False)
    response = auth_client.get(_STATUS_URL)
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["connected"] is False


def test_callback_with_unknown_state_redirects_with_error_never_500(auth_client) -> None:
    """Regresion: el router debe capturar InvalidStateError y redirigir con
    google_error=INVALID_STATE -- nunca dejar que se propague como un 500
    sin manejar (encontrado durante la verificacion manual en navegador)."""
    response = auth_client.get(
        _CALLBACK_URL,
        params={"state": "does-not-exist-anywhere", "code": "whatever"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "google_error=INVALID_STATE" in response.headers["location"]


def test_callback_with_reused_state_redirects_with_error_never_500(auth_client) -> None:
    """Un segundo callback con el mismo state (ya SUCCEEDED) tambien debe
    redirigir con error, nunca 500."""
    state = _authorize(auth_client)
    code = f"code-{state[:8]}"
    get_fake_oauth_provider_singleton().register_authorization_code(
        code, FakeAuthorizationCode(sub="sub-reuse", email="reuse@example.com", scopes=("openid", "email"))
    )
    first = auth_client.get(_CALLBACK_URL, params={"state": state, "code": code}, follow_redirects=False)
    assert first.status_code == 302
    assert "google_status=connected" in first.headers["location"]

    second = auth_client.get(_CALLBACK_URL, params={"state": state, "code": code}, follow_redirects=False)
    assert second.status_code == 302
    assert "google_error=INVALID_STATE" in second.headers["location"]


def test_authorize_rejects_calendar_capability_in_this_block(auth_client) -> None:
    response = auth_client.post(
        _AUTHORIZE_URL, json={"capability": "CALENDAR", "return_path": "/settings/integrations/google"}
    )
    assert response.status_code == 422


def test_authorize_rejects_invalid_return_path(auth_client) -> None:
    response = auth_client.post(
        _AUTHORIZE_URL, json={"capability": "IDENTITY", "return_path": "https://evil.example.com/"}
    )
    assert response.status_code == 422


def test_full_connect_flow_via_http_reaches_active_status(auth_client) -> None:
    _complete_via_http(auth_client, sub="sub-http-1", email="http@example.com")
    status_response = auth_client.get(_STATUS_URL)
    body = status_response.json()
    assert body["connected"] is True
    assert body["status"] == "ACTIVE"
    assert body["google_account_email"] == "http@example.com"
    assert body["identity_authorized"] is True


def test_disconnect_requires_existing_connection(auth_client) -> None:
    response = auth_client.post(_DISCONNECT_URL)
    assert response.status_code == 404


def test_disconnect_via_http_after_connecting(auth_client) -> None:
    _complete_via_http(auth_client)
    response = auth_client.post(_DISCONNECT_URL)
    assert response.status_code == 200
    body = response.json()
    assert body["disconnected"] is True

    status_response = auth_client.get(_STATUS_URL)
    assert status_response.json()["connected"] is False


def test_google_not_configured_returns_409(auth_client, monkeypatch) -> None:
    settings = integration_crypto.get_settings()
    monkeypatch.setattr(settings, "integration_token_encryption_key", "", raising=False)
    response = auth_client.post(
        _AUTHORIZE_URL, json={"capability": "IDENTITY", "return_path": "/settings/integrations/google"}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "INTEGRATION_MISCONFIGURED"


def test_ownership_connections_are_independent_per_user(auth_client, contador_client) -> None:
    _complete_via_http(contador_client, sub="sub-contador", email="contador@example.com")

    admin_status = auth_client.get(_STATUS_URL).json()
    assert admin_status["connected"] is False

    contador_status = contador_client.get(_STATUS_URL).json()
    assert contador_status["connected"] is True
    assert contador_status["google_account_email"] == "contador@example.com"


def test_no_token_fields_in_any_response(auth_client) -> None:
    _complete_via_http(auth_client)
    status_body = auth_client.get(_STATUS_URL).json()
    serialized = str(status_body).lower()
    assert "access_token" not in serialized
    assert "refresh_token" not in serialized
    assert "encrypted" not in serialized
