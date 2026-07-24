"""Tipos comunes reutilizados entre schemas (Bloque 4): politica unica de
contrasenas y validacion unica de motivos (ver ADR-0051). Ningun schema debe
declarar sus propias reglas de longitud/complejidad de contrasena o de
"motivo no vacio" por separado -- todos importan de aqui."""

from typing import Annotated

from pydantic import AfterValidator, Field

PASSWORD_MIN_LENGTH = 8

PasswordStr = Annotated[str, Field(min_length=PASSWORD_MIN_LENGTH)]


def _validate_reason(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("El motivo no puede estar vacío ni contener solo espacios.")
    return stripped


ReasonStr = Annotated[str, AfterValidator(_validate_reason)]
