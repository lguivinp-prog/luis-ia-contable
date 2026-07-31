import logging
import re
import sys

_SENSITIVE_FIELD_NAMES = {
    "password",
    "token",
    "sol_username",
    "sol_password",
    "secret",
    # ---- Fase 3, Bloque 3.1: integracion Google OAuth (Seccion 15 del plan v3) ----
    "access_token",
    "refresh_token",
    "client_secret",
    "id_token",
    "code_verifier",
    # ---- Fase 3, Bloque 3.2: comunicaciones asistidas por WhatsApp ----
    "message_body",
    "recipient_phone",
    "recipient_phone_e164",
    "wa_url",
}


class RedactSensitiveFieldsFilter(logging.Filter):
    """Evita que nombres de campos sensibles aparezcan en logs, incluso por error humano."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage().lower()
        return not any(field in message for field in _SENSITIVE_FIELD_NAMES)


# El log de acceso de uvicorn incluye la query string completa de cada
# peticion. El callback OAuth (`code`/`state`) transporta secretos de un solo
# uso en la query string -- se redacta especificamente esa ruta, sin
# descartar la linea completa (a diferencia de RedactSensitiveFieldsFilter),
# para conservar visibilidad de que el callback ocurrio (Seccion 15 del plan v3).
_CALLBACK_QUERY_PATTERN = re.compile(r"(/integrations/google/callback)\?[^\s\"]*")


class RedactGoogleCallbackQueryFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if _CALLBACK_QUERY_PATTERN.search(message):
            record.msg = _CALLBACK_QUERY_PATTERN.sub(r"\1?<redacted>", message)
            record.args = ()
        return True


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    handler.addFilter(RedactSensitiveFieldsFilter())
    root.handlers = [handler]

    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.addFilter(RedactGoogleCallbackQueryFilter())
