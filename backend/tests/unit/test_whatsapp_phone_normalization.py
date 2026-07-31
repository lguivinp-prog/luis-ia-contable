"""Normalizacion conservadora de numeros de WhatsApp (Fase 3 / Bloque 3.2,
ver ADR-0067): cubre los 6 pasos exactos del algoritmo y sus casos borde --
nunca adivina un pais fuera de Peru, siempre exige E.164 explicito en caso
de duda."""

import pytest

from app.services.whatsapp_phone_normalization import (
    InvalidWhatsAppNumberError,
    WhatsAppNumberRequiredError,
    digits_for_wa_url,
    normalize_whatsapp_number,
)


def test_none_raises_number_required() -> None:
    with pytest.raises(WhatsAppNumberRequiredError):
        normalize_whatsapp_number(None)


def test_empty_string_raises_number_required() -> None:
    with pytest.raises(WhatsAppNumberRequiredError):
        normalize_whatsapp_number("")


def test_whitespace_only_raises_number_required() -> None:
    with pytest.raises(WhatsAppNumberRequiredError):
        normalize_whatsapp_number("   ")


def test_explicit_e164_is_accepted_unchanged() -> None:
    display, e164 = normalize_whatsapp_number("+51987654321")
    assert display == "+51987654321"
    assert e164 == "+51987654321"


def test_peru_9_digit_mobile_gets_plus_51_prefix() -> None:
    _, e164 = normalize_whatsapp_number("987654321")
    assert e164 == "+51987654321"


def test_peru_11_digit_with_51_prefix_gets_plus_sign() -> None:
    _, e164 = normalize_whatsapp_number("51987654321")
    assert e164 == "+51987654321"


def test_00_prefix_is_converted_to_plus() -> None:
    _, e164 = normalize_whatsapp_number("0051987654321")
    assert e164 == "+51987654321"


def test_spaces_hyphens_and_parens_are_stripped_before_evaluation() -> None:
    display, e164 = normalize_whatsapp_number(" (51) 987-654-321 ")
    assert e164 == "+51987654321"
    # El display conserva el texto original ingresado (recortado), tal cual
    # lo escribio el usuario -- solo el E.164 se normaliza.
    assert display == "(51) 987-654-321"


def test_9_digit_not_starting_with_9_is_rejected() -> None:
    with pytest.raises(InvalidWhatsAppNumberError):
        normalize_whatsapp_number("123456789")


def test_11_digit_not_starting_with_51_is_rejected() -> None:
    with pytest.raises(InvalidWhatsAppNumberError):
        normalize_whatsapp_number("12345678901")


def test_too_short_explicit_e164_is_rejected() -> None:
    with pytest.raises(InvalidWhatsAppNumberError):
        normalize_whatsapp_number("+123")


def test_too_long_explicit_e164_is_rejected() -> None:
    with pytest.raises(InvalidWhatsAppNumberError):
        normalize_whatsapp_number("+1234567890123456")


def test_explicit_e164_starting_with_zero_after_plus_is_rejected() -> None:
    with pytest.raises(InvalidWhatsAppNumberError):
        normalize_whatsapp_number("+0123456789")


def test_non_numeric_input_is_rejected() -> None:
    with pytest.raises(InvalidWhatsAppNumberError):
        normalize_whatsapp_number("abc-defg-hij")


def test_arbitrary_short_local_number_without_country_code_is_rejected() -> None:
    """Numero corto ambiguo sin prefijo de pais explicito -- nunca se
    adivina, se exige formato internacional (Seccion 7 del pedido)."""
    with pytest.raises(InvalidWhatsAppNumberError):
        normalize_whatsapp_number("5551234")


def test_digits_for_wa_url_strips_plus_sign() -> None:
    assert digits_for_wa_url("+51987654321") == "51987654321"
