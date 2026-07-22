"""Validacion del RUC peruano: formato y digito verificador (algoritmo modulo 11 de SUNAT).

Referencia: algoritmo publico de calculo del digito verificador usado por SUNAT
para el Registro Unico de Contribuyentes. No involucra ninguna regla de
cronograma ni fecha de vencimiento (ver docs/TAX_RULES_MODEL.md).
"""

# Prefijos (dos primeros digitos) de tipo de contribuyente actualmente en uso por SUNAT.
VALID_RUC_PREFIXES = {"10", "15", "16", "17", "20"}

_CHECK_DIGIT_FACTORS = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]


class InvalidRucError(ValueError):
    pass


def compute_check_digit(first_ten_digits: str) -> int:
    if len(first_ten_digits) != 10 or not first_ten_digits.isdigit():
        raise InvalidRucError("Se requieren exactamente 10 digitos para calcular el verificador")

    total = sum(
        int(digit) * factor
        for digit, factor in zip(first_ten_digits, _CHECK_DIGIT_FACTORS, strict=True)
    )
    remainder = total % 11
    result = 11 - remainder
    if result == 10:
        return 0
    if result == 11:
        return 1
    return result


def is_valid_ruc(ruc: str) -> bool:
    if not isinstance(ruc, str) or len(ruc) != 11 or not ruc.isdigit():
        return False
    if ruc[:2] not in VALID_RUC_PREFIXES:
        return False
    expected_check_digit = compute_check_digit(ruc[:10])
    return expected_check_digit == int(ruc[10])


def validate_ruc(ruc: str) -> str:
    """Lanza InvalidRucError con un mensaje descriptivo si el RUC no es valido; retorna el RUC si lo es."""
    if not isinstance(ruc, str) or not ruc.isdigit() or len(ruc) != 11:
        raise InvalidRucError("El RUC debe tener exactamente 11 digitos numericos")
    if ruc[:2] not in VALID_RUC_PREFIXES:
        raise InvalidRucError(f"Prefijo de RUC no reconocido: {ruc[:2]}")
    if not is_valid_ruc(ruc):
        raise InvalidRucError("Digito verificador del RUC invalido")
    return ruc


def get_ruc_last_digit(ruc: str) -> int:
    return int(ruc[10])
