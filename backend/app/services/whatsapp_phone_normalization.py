"""Normalizacion conservadora de numeros de WhatsApp (Fase 3 / Bloque 3.2,
ver ADR-0067, Seccion 7 del pedido). Nunca inventa ni completa un numero
ambiguo -- ante cualquier duda, exige formato internacional explicito
(E.164 con "+") en vez de adivinar un pais. El unico pais con reglas de
conveniencia propias es Peru (9 digitos moviles / 11 digitos con prefijo
51), porque es el unico mercado real de esta instalacion."""

from __future__ import annotations

import re

_STRIP_CHARS_RE = re.compile(r"[\s\-()]")
_E164_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")


class WhatsAppNumberRequiredError(Exception):
    """El cliente no tiene `whatsapp_number` y no se proporciono uno
    alternativo -- no hay ningun numero del cual partir."""

    code = "WHATSAPP_NUMBER_REQUIRED"


class InvalidWhatsAppNumberError(Exception):
    """El numero proporcionado no pudo normalizarse de forma segura -- se
    exige formato internacional explicito en vez de adivinar."""

    code = "INVALID_WHATSAPP_NUMBER"


def normalize_whatsapp_number(raw: str | None) -> tuple[str, str]:
    """Devuelve (recipient_phone_display, recipient_phone_e164).

    Reglas exactas (nunca se salta ni se reordena un paso):
    1. eliminar espacios, guiones y parentesis;
    2. si empieza por "+", aceptar entre 8 y 15 digitos;
    3. si empieza por "00", convertir a "+";
    4. si tiene exactamente 9 digitos y empieza por 9, asumir Peru y
       anteponer "+51";
    5. si tiene 11 digitos y empieza por "51", anteponer "+";
    6. en cualquier otro caso, exigir formato internacional explicito
       (rechazar con InvalidWhatsAppNumberError).
    """
    if raw is None or not raw.strip():
        raise WhatsAppNumberRequiredError()

    display = raw.strip()
    cleaned = _STRIP_CHARS_RE.sub("", raw)

    if not cleaned:
        raise WhatsAppNumberRequiredError()

    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]

    if cleaned.startswith("+"):
        digits = cleaned[1:]
        if not digits.isdigit() or not (8 <= len(digits) <= 15):
            raise InvalidWhatsAppNumberError()
        e164 = "+" + digits
    elif cleaned.isdigit() and len(cleaned) == 9 and cleaned.startswith("9"):
        e164 = "+51" + cleaned
    elif cleaned.isdigit() and len(cleaned) == 11 and cleaned.startswith("51"):
        e164 = "+" + cleaned
    else:
        raise InvalidWhatsAppNumberError()

    if not _E164_RE.match(e164):
        raise InvalidWhatsAppNumberError()

    return display, e164


def digits_for_wa_url(e164: str) -> str:
    """Digitos sin el signo "+", tal como los exige la URL wa.me."""
    return e164.lstrip("+")
