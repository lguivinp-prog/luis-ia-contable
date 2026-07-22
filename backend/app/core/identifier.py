"""Normalizacion del identificador de login (ver ADR-0009).

Se usa el mismo resultado en login_attempts, la busqueda de usuario y el
desbloqueo administrativo, para impedir evadir el bloqueo con variaciones de
mayusculas/espacios (p. ej. "ADMIN@X.COM" vs " admin@x.com "). La funcion es
pura: nunca consulta la base de datos ni depende de si el usuario existe.
"""

import unicodedata

MAX_IDENTIFIER_LENGTH = 255


class InvalidIdentifierError(ValueError):
    pass


def normalize_login_identifier(identifier: str) -> str:
    if not isinstance(identifier, str):
        raise InvalidIdentifierError("El identificador debe ser una cadena de texto")

    normalized = unicodedata.normalize("NFC", identifier).strip().lower()

    if not normalized:
        raise InvalidIdentifierError("El identificador no puede estar vacio")
    if len(normalized) > MAX_IDENTIFIER_LENGTH:
        raise InvalidIdentifierError("El identificador excede la longitud maxima permitida")
    if any(unicodedata.category(ch).startswith("C") for ch in normalized):
        raise InvalidIdentifierError("El identificador contiene caracteres de control no permitidos")

    return normalized
