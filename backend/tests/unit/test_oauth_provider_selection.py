"""Seleccion de adaptador OAuth Google (GOOGLE_LIVE_VALIDATION_DEFERRED, ver
ADR-0063): el adaptador real nunca se construye sin credenciales reales,
incluso si GOOGLE_OAUTH_PROVIDER quedara mal configurado como "google"."""

from app.core import dependencies
from app.core.dependencies import get_oauth_provider, reset_fake_oauth_provider_singleton
from app.domain.google_oauth.fake_provider import FakeOAuthProvider
from app.domain.google_oauth.google_provider import GoogleOAuthProvider
from app.services.google_oauth_service import is_google_oauth_configured


def test_get_oauth_provider_returns_fake_when_credentials_empty(monkeypatch):
    monkeypatch.setattr(dependencies.settings, "google_oauth_provider", "google", raising=False)
    monkeypatch.setattr(dependencies.settings, "google_oauth_client_id", "", raising=False)
    monkeypatch.setattr(dependencies.settings, "google_oauth_client_secret", "", raising=False)
    reset_fake_oauth_provider_singleton()
    try:
        provider = get_oauth_provider()
        assert isinstance(provider, FakeOAuthProvider)
    finally:
        reset_fake_oauth_provider_singleton()


def test_get_oauth_provider_returns_real_only_with_full_credentials(monkeypatch):
    monkeypatch.setattr(dependencies.settings, "google_oauth_provider", "google", raising=False)
    monkeypatch.setattr(dependencies.settings, "google_oauth_client_id", "real-id", raising=False)
    monkeypatch.setattr(dependencies.settings, "google_oauth_client_secret", "real-secret", raising=False)
    provider = get_oauth_provider()
    assert isinstance(provider, GoogleOAuthProvider)


def test_get_oauth_provider_returns_fake_when_only_one_credential_present(monkeypatch):
    monkeypatch.setattr(dependencies.settings, "google_oauth_provider", "google", raising=False)
    monkeypatch.setattr(dependencies.settings, "google_oauth_client_id", "real-id", raising=False)
    monkeypatch.setattr(dependencies.settings, "google_oauth_client_secret", "", raising=False)
    reset_fake_oauth_provider_singleton()
    try:
        provider = get_oauth_provider()
        assert isinstance(provider, FakeOAuthProvider)
    finally:
        reset_fake_oauth_provider_singleton()


def test_is_google_oauth_configured_reflects_both_credentials(monkeypatch):
    from app.services import google_oauth_service as svc

    settings = svc.get_settings()
    monkeypatch.setattr(settings, "google_oauth_client_id", "", raising=False)
    monkeypatch.setattr(settings, "google_oauth_client_secret", "", raising=False)
    assert is_google_oauth_configured() is False

    monkeypatch.setattr(settings, "google_oauth_client_id", "real-id", raising=False)
    monkeypatch.setattr(settings, "google_oauth_client_secret", "real-secret", raising=False)
    assert is_google_oauth_configured() is True
