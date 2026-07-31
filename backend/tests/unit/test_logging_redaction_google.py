"""Redaccion de logs del callback OAuth Google (Fase 3 / Bloque 3.1,
Seccion 15 del plan v3): el log de acceso nunca debe contener `code`/
`state` crudos ni ningun token, aunque la linea completa se conserve para
trazabilidad de que el callback ocurrio."""

import logging

from app.core.logging import RedactGoogleCallbackQueryFilter, RedactSensitiveFieldsFilter


def _make_record(message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_callback_query_string_is_redacted():
    record = _make_record(
        'GET /api/v1/integrations/google/callback?state=SECRETSTATE123&code=SECRETCODE456 HTTP/1.1" 302'
    )
    result = RedactGoogleCallbackQueryFilter().filter(record)
    assert result is True  # nunca descarta la linea completa
    redacted_message = record.getMessage()
    assert "SECRETSTATE123" not in redacted_message
    assert "SECRETCODE456" not in redacted_message
    assert "/api/v1/integrations/google/callback?<redacted>" in redacted_message
    assert "302" in redacted_message  # el resto de la linea se conserva


def test_non_callback_lines_are_untouched():
    original = 'GET /api/v1/health/live HTTP/1.1" 200'
    record = _make_record(original)
    RedactGoogleCallbackQueryFilter().filter(record)
    assert record.getMessage() == original


def test_sensitive_field_names_still_drop_the_whole_line():
    record = _make_record("access_token=abc123 issued for user")
    result = RedactSensitiveFieldsFilter().filter(record)
    assert result is False  # el filtro generico SI descarta la linea completa


def test_sensitive_field_names_includes_google_oauth_fields():
    from app.core.logging import _SENSITIVE_FIELD_NAMES

    for field in ("access_token", "refresh_token", "client_secret", "id_token", "code_verifier"):
        assert field in _SENSITIVE_FIELD_NAMES
