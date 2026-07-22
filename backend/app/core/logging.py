import logging
import sys

_SENSITIVE_FIELD_NAMES = {"password", "token", "sol_username", "sol_password", "secret"}


class RedactSensitiveFieldsFilter(logging.Filter):
    """Evita que nombres de campos sensibles aparezcan en logs, incluso por error humano."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage().lower()
        return not any(field in message for field in _SENSITIVE_FIELD_NAMES)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    handler.addFilter(RedactSensitiveFieldsFilter())
    root.handlers = [handler]
