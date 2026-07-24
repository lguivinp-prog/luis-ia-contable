# ADR-0050: Catálogo cerrado de permisos (`Permission`) y mapa declarativo por rol

## Estado
Aceptado

## Contexto
Hasta el Bloque 4, la única distinción de autorización real era `ensure_admin_role` (un único punto de
control admin-vs-resto, usado solo por el cierre excepcional de declaraciones, ADR-0047). No existía un
concepto de permiso granular: cualquier usuario autenticado con rol `contador`/`asistente` podía invocar
cualquier endpoint mutante. El bloque requiere RBAC real para los tres roles fijos ya existentes en el
`CHECK` de `users.role` (`admin`, `contador`, `asistente`), sin introducir tablas relacionales `roles`/
`permissions`/`user_roles` — el catálogo de permisos es estático y conocido en tiempo de desarrollo, no un
dato de negocio que el tenant configure.

## Decisión
`Permission(str, enum.Enum)` en `backend/app/core/permissions.py` declara 30 valores explícitos (sin
comodines `*`), agrupados por dominio: `user:*` (2), `client:*` (2), `obligation:*` (2), `tax_category:*` (2),
`tax_period:*` (2), `tax_schedule:*` (2), `declaration:*` (5, incluyendo `declaration:close_override` como
permiso separado de `declaration:close`), `document:*` (3, ver más abajo), `tax_assessment:*` (2),
`tax_payment:*` (3), `professional_fee:*` (3), `report:export` (1), `dashboard:read` (1).

`ROLE_PERMISSIONS: dict[str, frozenset[Permission]]` es la única fuente de verdad: `admin` = los 30;
`contador` = 27 (todos salvo `user:read`, `user:manage`, `declaration:close_override`); `asistente` = 13
(todos los `*:read` más `document:item_write` y `report:export`). `has_permission(role, permission)` y
`check_permission(user, permission)` fallan cerrado: un rol o permiso no reconocido devuelve `False`/lanza
403, nunca `True` por defecto.

**Separación `document:item_write` vs. `document:manage`**: los endpoints reales de
`declaration_documents.py` (confirmado por lectura directa del router, no por suposición) son solo 3 de
lectura, 1 de cambio de estado de ítem (`PATCH .../documents/{item_id}`) y 4 de configuración (crear entrada
de catálogo, asignar requisito a cliente, sincronizar por declaración, sincronizar por periodo). Un único
`document:write` habría sido demasiado amplio para `asistente`: operar el estado de un ítem ya asignado
(recibido/observado/aceptado) es una tarea operativa diaria que un asistente sí debe poder hacer, mientras
que crear/asignar/sincronizar requisitos es una decisión de configuración reservada a `admin`/`contador`. No
existen endpoints de "modificar catálogo" o "desactivar requisito" — no se declaró ningún permiso para
capacidades que el código no implementa.

## Alternativas consideradas
- **Tablas relacionales `roles`/`permissions`/`user_roles`**: rechazado para este bloque — los 3 roles y sus
  30 permisos son fijos y conocidos en el código, no configurables por el tenant; una tabla añadiría una
  migración de datos y consultas extra sin beneficio funcional inmediato. Si en el futuro se requieren roles
  personalizados por tenant, esto se revisita explícitamente.
- **Un único `document:write`**: rechazado tras revisión — mezclaría dos niveles de privilegio distintos
  bajo un mismo permiso, forzando a elegir entre dar a `asistente` demasiado acceso o quitarle una tarea
  operativa legítima.

## Consecuencias
- `backend/tests/unit/test_permissions.py` (17 pruebas) verifica el conteo total, la pertenencia exacta por
  rol, y el fallo cerrado ante rol/permiso desconocido.
- El frontend nunca reconstruye este mapa (ver ADR-0052 y `frontend/src/lib/permissions.ts`): consume
  `permissions: list[str]` calculado por el backend en `GET /auth/me`.
- Ver ADR-0051 para el ciclo de vida de usuarios que asigna estos roles, y ADR-0052 para cómo se
  aplican en los routers.
