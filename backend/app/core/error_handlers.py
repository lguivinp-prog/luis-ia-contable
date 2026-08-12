"""Interceptor de errores 422 (Fase 5, Bloque 5.1, ver ADR-0068, Seccion
19): el manejador por defecto de FastAPI para `RequestValidationError`
incluye el valor literal recibido (`input`) por cada error -- si el campo
es sensible (contrasena de boveda, codigo de recuperacion, credenciales
SOL, etc.), ese valor terminaria reflejado sin cifrar en el cuerpo de la
respuesta 422. Este interceptor reemplaza `input` por un marcador cuando
el ultimo segmento de `loc` coincide con un nombre de campo sensible --
mismo catalogo que `app/core/logging.py`, para no duplicar la lista.

Un cuerpo de peticion que incluya cualquier campo sensible (por nombre)
puede disparar un error 422 sobre OTRO campo -- por ejemplo un campo
inesperado (`extra=forbid`) cuyo `loc` no coincide con ningun nombre
conocido, pero cuyo valor recibido es el mismo secreto. Por eso, cuando el
cuerpo recibido contiene algun nombre de campo sensible, se redactan TODOS
los `input` del detalle de error, no solo los que coinciden por nombre."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import _SENSITIVE_FIELD_NAMES

_REDACTED_PLACEHOLDER = "<redacted>"


def _redact_input(error: dict) -> dict:
    if "input" not in error:
        return error
    error = dict(error)
    error["input"] = _REDACTED_PLACEHOLDER
    return error


def _redact_sensitive_input(error: dict) -> dict:
    loc = error.get("loc") or ()
    field_name = loc[-1] if loc else None
    if isinstance(field_name, str) and field_name in _SENSITIVE_FIELD_NAMES:
        return _redact_input(error)
    return error


async def _body_has_sensitive_field_name(request: Request) -> bool:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 -- cuerpo no-JSON o ilegible: nada que inspeccionar
        return False
    if not isinstance(body, dict):
        return False
    return any(key in _SENSITIVE_FIELD_NAMES for key in body)


async def _handle_request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    if await _body_has_sensitive_field_name(request):
        redacted_errors = [_redact_input(error) for error in errors]
    else:
        redacted_errors = [_redact_sensitive_input(error) for error in errors]
    content = {"detail": jsonable_encoder(redacted_errors)}
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=content)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _handle_request_validation_error)
