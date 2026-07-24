"""Matriz 200/403 por permiso (Bloque 4, Seccion 7/Dominio C del plan): al
menos 1 caso por cada uno de los 30 permisos, contra rutas y payloads reales.

Estrategia: FastAPI resuelve las sub-dependencias (`Depends`, incluido
`require_permission`) ANTES de validar el cuerpo del request -- por lo que un
cuerpo vacio/invalido no cambia el resultado de la verificacion de permiso:
si el rol carece del permiso, la respuesta es SIEMPRE 403, sin importar el
cuerpo ni si el recurso referenciado (un UUID aleatorio) existe. Si el rol
tiene el permiso, la respuesta NUNCA es 403 -- sera 404 (recurso inexistente),
422 (validacion del cuerpo) u otro codigo de negocio, pero nunca 403. Esto
permite verificar la matriz completa sin construir datos reales de cada
dominio (declaraciones, periodos, honorarios, etc.) para las 30 rutas."""

import uuid

import pytest

DUMMY_ID = "00000000-0000-0000-0000-000000000001"
DUMMY_ID_2 = "00000000-0000-0000-0000-000000000002"


def _assert_not_403(response) -> None:
    assert response.status_code != 403, (
        f"se esperaba acceso permitido, se recibio 403: {response.text}"
    )


# (permiso, metodo, ruta, body, fixture con el permiso, fixture sin el permiso o None)
CASES = [
    ("user:read", "GET", "/api/v1/users", None, "auth_client", "contador_client"),
    ("user:manage", "POST", "/api/v1/users", {}, "auth_client", "contador_client"),
    ("client:read", "GET", "/api/v1/clients", None, "asistente_client", None),
    (
        "client:write",
        "PUT",
        f"/api/v1/clients/{DUMMY_ID}",
        {},
        "contador_client",
        "asistente_client",
    ),
    ("obligation:read", "GET", "/api/v1/obligations/catalog", None, "asistente_client", None),
    (
        "obligation:write",
        "POST",
        f"/api/v1/clients/{DUMMY_ID}/obligations",
        {},
        "contador_client",
        "asistente_client",
    ),
    ("tax_category:read", "GET", "/api/v1/tax-categories/catalog", None, "asistente_client", None),
    (
        "tax_category:write",
        "POST",
        "/api/v1/tax-categories/catalog",
        {},
        "contador_client",
        "asistente_client",
    ),
    ("tax_period:read", "GET", "/api/v1/tax-periods", None, "asistente_client", None),
    (
        "tax_period:manage",
        "PATCH",
        f"/api/v1/tax-periods/{DUMMY_ID}/close",
        {},
        "contador_client",
        "asistente_client",
    ),
    ("tax_schedule:read", "GET", "/api/v1/tax-schedules/sources", None, "asistente_client", None),
    (
        "tax_schedule:manage",
        "POST",
        "/api/v1/tax-schedules/sources",
        {},
        "contador_client",
        "asistente_client",
    ),
    ("declaration:read", "GET", "/api/v1/declarations", None, "asistente_client", None),
    ("declaration:write", "POST", "/api/v1/declarations", {}, "contador_client", "asistente_client"),
    (
        "declaration:file",
        "PATCH",
        f"/api/v1/declarations/{DUMMY_ID}/filing",
        {},
        "contador_client",
        "asistente_client",
    ),
    (
        "declaration:close",
        "POST",
        f"/api/v1/declarations/{DUMMY_ID}/close",
        {"force_close_with_pending": False},
        "contador_client",
        "asistente_client",
    ),
    (
        "declaration:close_override",
        "POST",
        f"/api/v1/declarations/{DUMMY_ID}/close",
        {"force_close_with_pending": True, "close_override_reason": "prueba"},
        "auth_client",
        "contador_client",
    ),
    (
        "document:read",
        "GET",
        "/api/v1/document-requirement-catalog",
        None,
        "asistente_client",
        None,
    ),
    (
        "document:item_write",
        "PATCH",
        f"/api/v1/declarations/{DUMMY_ID}/documents/{DUMMY_ID_2}",
        {},
        "asistente_client",
        None,
    ),
    (
        "document:manage",
        "POST",
        "/api/v1/document-requirement-catalog",
        {},
        "contador_client",
        "asistente_client",
    ),
    (
        "tax_assessment:read",
        "GET",
        f"/api/v1/declarations/{DUMMY_ID}/tax-assessments",
        None,
        "asistente_client",
        None,
    ),
    (
        "tax_assessment:write",
        "POST",
        f"/api/v1/declarations/{DUMMY_ID}/tax-assessments",
        {},
        "contador_client",
        "asistente_client",
    ),
    (
        "tax_payment:read",
        "GET",
        f"/api/v1/declarations/{DUMMY_ID}/tax-payments",
        None,
        "asistente_client",
        None,
    ),
    (
        "tax_payment:write",
        "POST",
        f"/api/v1/declarations/{DUMMY_ID}/tax-payments",
        {},
        "contador_client",
        "asistente_client",
    ),
    (
        "tax_payment:void",
        "POST",
        f"/api/v1/declarations/{DUMMY_ID}/tax-payments/{DUMMY_ID_2}/void",
        {},
        "contador_client",
        "asistente_client",
    ),
    (
        "professional_fee:read",
        "GET",
        f"/api/v1/clients/{DUMMY_ID}/fee-agreements",
        None,
        "asistente_client",
        None,
    ),
    (
        "professional_fee:write",
        "POST",
        f"/api/v1/clients/{DUMMY_ID}/fee-agreements",
        {},
        "contador_client",
        "asistente_client",
    ),
    (
        "professional_fee:void",
        "POST",
        f"/api/v1/fee-charges/{DUMMY_ID}/payments/{DUMMY_ID_2}/void",
        {},
        "contador_client",
        "asistente_client",
    ),
    ("report:export", "POST", "/api/v1/reports/export", {}, "asistente_client", None),
    ("dashboard:read", "GET", "/api/v1/dashboard/monthly", None, "asistente_client", None),
    (
        "document:file_manage",
        "POST",
        f"/api/v1/declarations/{DUMMY_ID}/documents/{DUMMY_ID_2}/files/{DUMMY_ID}/delete",
        {"reason": "prueba"},
        "contador_client",
        "asistente_client",
    ),
]


@pytest.mark.parametrize("permission,method,path,body,granted_fixture,denied_fixture", CASES)
def test_permission_matrix(
    request, permission, method, path, body, granted_fixture, denied_fixture
) -> None:
    granted_client = request.getfixturevalue(granted_fixture)
    response = granted_client.request(method, path, json=body if body is not None else None)
    _assert_not_403(response)

    if denied_fixture is not None:
        denied_client = request.getfixturevalue(denied_fixture)
        response = denied_client.request(method, path, json=body if body is not None else None)
        assert response.status_code == 403, (
            f"{permission}: se esperaba 403 para {denied_fixture}, se obtuvo "
            f"{response.status_code}: {response.text}"
        )


def test_matrix_covers_all_31_permissions() -> None:
    from app.core.permissions import Permission

    covered = {case[0] for case in CASES}
    all_permissions = {p.value for p in Permission}
    missing = all_permissions - covered
    assert not missing, f"permisos sin caso de prueba en la matriz: {sorted(missing)}"
    assert len(CASES) >= 31


def test_asistente_role_is_real_and_distinct(asistente_user) -> None:
    assert asistente_user.role == "asistente"
    assert asistente_user.id != uuid.UUID(int=0)
