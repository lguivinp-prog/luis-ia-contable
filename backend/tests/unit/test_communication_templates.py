"""Catalogo de plantillas de comunicacion asistida (Fase 3 / Bloque 3.2, ver
ADR-0067, Seccion 8 del pedido): 5 plantillas versionadas en codigo,
renderizado determinista, rechazo de variables no admitidas, tolerancia a
variables admitidas pero ausentes, y ausencia de cualquier dato sensible
hardcodeado en el texto."""

import pytest

from app.domain.communication_templates import (
    ALLOWED_VARIABLES,
    TemplateNotFoundError,
    UnknownTemplateVariableError,
    get_template,
    list_templates,
)

_EXPECTED_CODES = {
    "GENERAL_MESSAGE",
    "OBLIGATION_DUE_REMINDER",
    "DOCUMENT_REQUEST",
    "PAYMENT_PENDING_REMINDER",
    "TASK_FOLLOW_UP",
}

_FORBIDDEN_SUBSTRINGS = (
    "password",
    "contraseña",
    "clave sol",
    "sol_username",
    "token",
    "cuenta bancaria",
    "numero de cuenta",
    "monto",
    "deuda",
)


def test_list_templates_returns_exactly_5_codes_sorted() -> None:
    templates = list_templates()
    assert {t.code for t in templates} == _EXPECTED_CODES
    assert [t.code for t in templates] == sorted(t.code for t in templates)


def test_every_template_is_version_1() -> None:
    for template in list_templates():
        assert template.version == 1


def test_every_template_variable_is_in_the_allowlist() -> None:
    for template in list_templates():
        for variable in template.variables:
            assert variable in ALLOWED_VARIABLES, f"{template.code} declara {variable} fuera de la allowlist"


def test_every_template_body_contains_no_forbidden_sensitive_data() -> None:
    for template in list_templates():
        lowered = template.body_template.lower()
        for forbidden in _FORBIDDEN_SUBSTRINGS:
            assert forbidden not in lowered, f"{template.code} contiene '{forbidden}' hardcodeado"


def test_get_unknown_template_raises_template_not_found() -> None:
    with pytest.raises(TemplateNotFoundError):
        get_template("NO_EXISTE")


def test_render_general_message_substitutes_declared_variables() -> None:
    template = get_template("GENERAL_MESSAGE")
    rendered = template.render(
        {"client_business_name": "Comercial Los Andes SAC", "professional_name": "Luis"}
    )
    assert "Comercial Los Andes SAC" in rendered
    assert "Luis" in rendered


def test_render_is_deterministic_same_inputs_same_output() -> None:
    template = get_template("OBLIGATION_DUE_REMINDER")
    variables = {
        "client_business_name": "Cliente X",
        "period_label": "2026-06",
        "due_date": "2026-07-15",
        "professional_name": "Luis",
    }
    assert template.render(variables) == template.render(variables)


def test_render_rejects_unknown_variable() -> None:
    template = get_template("GENERAL_MESSAGE")
    with pytest.raises(UnknownTemplateVariableError) as exc_info:
        template.render({"client_business_name": "X", "sol_password": "hackme"})
    assert "sol_password" in exc_info.value.unknown


def test_render_tolerates_missing_but_allowed_variable_as_empty_string() -> None:
    template = get_template("TASK_FOLLOW_UP")
    rendered = template.render({"client_business_name": "Cliente Y"})
    # task_title y professional_name no se proporcionaron pero son admitidas
    # -- deben renderizar como cadena vacia, nunca lanzar excepcion.
    assert "Cliente Y" in rendered


def test_render_with_empty_variables_dict_never_raises_for_optional_fields() -> None:
    template = get_template("DOCUMENT_REQUEST")
    rendered = template.render({})
    assert isinstance(rendered, str)
