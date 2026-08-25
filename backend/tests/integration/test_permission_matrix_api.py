"""Matriz 200/403 por permiso (Bloque 4, Seccion 7/Dominio C del plan): al
menos 1 caso por cada uno de los 42 permisos, contra rutas y payloads reales.

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
    ("task:read", "GET", "/api/v1/tasks", None, "asistente_client", None),
    (
        "task:create",
        "POST",
        "/api/v1/tasks",
        {},
        "asistente_client",
        None,
    ),
    (
        "task:update",
        "PATCH",
        f"/api/v1/tasks/{DUMMY_ID}",
        {},
        "asistente_client",
        None,
    ),
    (
        "task:complete",
        "POST",
        f"/api/v1/tasks/{DUMMY_ID}/complete",
        {},
        "asistente_client",
        None,
    ),
    (
        "task:assign",
        "POST",
        f"/api/v1/tasks/{DUMMY_ID}/reassign",
        {},
        "contador_client",
        "asistente_client",
    ),
    (
        "task:cancel",
        "POST",
        f"/api/v1/tasks/{DUMMY_ID}/cancel",
        {},
        "contador_client",
        "asistente_client",
    ),
    (
        "task:sync",
        "POST",
        "/api/v1/tasks/sync",
        {"trigger_source": "MANUAL"},
        "asistente_client",
        None,
    ),
    ("alert:read", "GET", "/api/v1/tasks/alerts-summary", None, "asistente_client", None),
    (
        "integration:connect",
        "GET",
        "/api/v1/integrations/google/status",
        None,
        "asistente_client",
        None,
    ),
    (
        "integration:disconnect",
        "POST",
        "/api/v1/integrations/google/disconnect",
        None,
        "asistente_client",
        None,
    ),
    (
        "communication:draft",
        "GET",
        "/api/v1/communications/templates",
        None,
        "asistente_client",
        None,
    ),
    # ---- Fase 5, Bloque 5.1: boveda de credenciales SOL (ver ADR-0068) ----
    ("sol_vault:unlock", "GET", "/api/v1/sol-vault/status", None, "contador_client", "asistente_client"),
    ("sol_vault:initialize", "POST", "/api/v1/sol-vault/initialize", {}, "auth_client", "contador_client"),
    ("sol_vault:manage_users", "GET", "/api/v1/sol-vault/slots", None, "auth_client", "contador_client"),
    ("sol_vault:recover", "POST", "/api/v1/sol-vault/recover", {}, "auth_client", "contador_client"),
    (
        "sol_vault:rotate",
        "POST",
        "/api/v1/sol-vault/data-key/rotate",
        {},
        "auth_client",
        "contador_client",
    ),
    (
        "sol_credential:read",
        "GET",
        f"/api/v1/clients/{DUMMY_ID}/sol-credential/status",
        None,
        "contador_client",
        "asistente_client",
    ),
    (
        "sol_credential:create",
        "POST",
        f"/api/v1/clients/{DUMMY_ID}/sol-credential",
        {},
        "contador_client",
        "asistente_client",
    ),
    (
        "sol_credential:update",
        "PUT",
        f"/api/v1/clients/{DUMMY_ID}/sol-credential",
        {},
        "contador_client",
        "asistente_client",
    ),
    (
        "sol_credential:disable",
        "POST",
        f"/api/v1/clients/{DUMMY_ID}/sol-credential/disable",
        {},
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


def test_matrix_covers_all_56_permissions() -> None:
    """Fase 5, Bloque 5.1 (ver ADR-0068): agrega los 9 permisos sol_vault:*/
    sol_credential:* (42 -> 51). Fase 5, Bloque 5.2 (ver
    docs/phase5/block5_2/): agrega 5 permisos sol_session:*/sol_agent:*
    (51 -> 56) -- excluidos deliberadamente de esta matriz porque Domain B
    de Bloque 5.2 es exclusivamente fundacion de persistencia (migracion
    0018 + modelos + enum de permisos), sin ningun router/endpoint todavia
    (ver BLOCK5_2_IMPLEMENTATION_PLAN.md seccion 4 -- los endpoints reales
    llegan en Domain D). Cuando esos endpoints existan, deben añadirse a
    `CASES` y retirarse de esta exclusion."""
    from app.core.permissions import Permission

    _RESERVED_WITHOUT_ENDPOINTS_YET = {
        Permission.SOL_SESSION_CREATE.value,
        Permission.SOL_SESSION_READ.value,
        Permission.SOL_SESSION_CANCEL.value,
        Permission.SOL_AGENT_READ.value,
        Permission.SOL_AGENT_MANAGE.value,
    }

    covered = {case[0] for case in CASES}
    all_permissions = {p.value for p in Permission}
    missing = all_permissions - covered - _RESERVED_WITHOUT_ENDPOINTS_YET
    assert not missing, f"permisos sin caso de prueba en la matriz: {sorted(missing)}"
    assert len(CASES) >= 51


def test_asistente_role_is_real_and_distinct(asistente_user) -> None:
    assert asistente_user.role == "asistente"
    assert asistente_user.id != uuid.UUID(int=0)
