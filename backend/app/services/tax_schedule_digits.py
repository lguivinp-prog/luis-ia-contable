"""Normalizacion de 'dígitos aplicables' de una regla de cronograma (ver
ADR-0014). Sustituye cualquier texto libre: la unica representacion valida es
una lista de enteros unicos entre 0 y 9 (o None = aplica a todos)."""


class InvalidDigitsError(ValueError):
    pass


def normalize_applicable_last_digits(raw: list[int] | None) -> list[int] | None:
    if raw is None:
        return None

    if len(raw) == 0:
        raise InvalidDigitsError(
            "La lista de dígitos aplicables no puede estar vacía (usa null para 'todos')"
        )

    seen: set[int] = set()
    for digit in raw:
        if not isinstance(digit, int) or isinstance(digit, bool):
            raise InvalidDigitsError(f"Dígito inválido (no es un entero): {digit!r}")
        if digit < 0 or digit > 9:
            raise InvalidDigitsError(f"Dígito fuera de rango 0-9: {digit}")
        if digit in seen:
            raise InvalidDigitsError(f"Dígito repetido: {digit}")
        seen.add(digit)

    return sorted(seen)


def specificity_of(digits: list[int] | None) -> int:
    """Menor valor = mas especifico. None (aplica a todos) es el menos
    especifico posible."""
    if digits is None:
        return 100
    return len(digits)
