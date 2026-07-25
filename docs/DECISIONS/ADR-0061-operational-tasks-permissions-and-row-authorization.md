# ADR-0061: Permisos de tareas y autorización por fila del asistente (excepción acotada)

## Estado
Aceptado

## Contexto
El patrón establecido en todo el repositorio desde ADR-0050/ADR-0052 es "permiso de rol, nunca de fila":
`require_permission` decide 200/403 exclusivamente por el rol del usuario autenticado, sin inspeccionar la
fila concreta sobre la que opera. La gestión operativa de tareas introduce el primer caso real donde eso no
basta: un asistente debe poder operar las tareas que tiene asignadas, pero no las de sus compañeros, aunque
las pueda leer todas.

## Decisión

### 8 permisos nuevos, `task:sync` concedido a los 3 roles
`task:read`, `task:create`, `task:update`, `task:complete`, `task:assign`, `task:cancel`, `task:sync`,
`alert:read`. `task:assign`/`task:cancel` quedan reservados a admin/contador; el resto (incluido `task:sync`,
para que cualquier rol pueda disparar/consultar una sincronización idempotente) se conceden a los 3 roles.
Totales resultantes: `admin=39`, `contador=36`, `asistente=19`.

### Autorización por fila del asistente — única excepción documentada
`task:read` sigue siendo permiso de rol puro: lectura total del tenant para los 3 roles, sin filtrado por
fila. Pero `task:update`/`task:complete` para el rol asistente exigen además, tras bloquear la fila con
`populate_existing=True`, que `task.assigned_to == current_user.id` con el valor recién releído bajo el
bloqueo — nunca un valor potencialmente obsoleto leído antes de bloquear. Operar una tarea ajena, o sin
responsable, devuelve **403** (nunca 404: la tarea es legible vía `task:read`; ni 409: no es un conflicto de
estado). El asistente nunca tiene `task:assign` ni `task:cancel`, y no puede autoasignarse una tarea sin
responsable en este bloque — solo admin/contador pueden asignarla.

### La carrera comentario-vs-reasignación se resuelve con el mismo bloqueo de fila
Un comentario no incrementa `version` (no muta la tarea), pero adquiere el mismo `lock_exact_task` que
cualquier acción mutante, precisamente para resolver la carrera: si el comentario del asistente adquiere el
bloqueo antes que una reasignación concurrente, se acepta (la reasignación procede después sin conflicto); si
la reasignación adquiere el bloqueo primero, el asistente —al bloquear después— relee la nueva asignación (ya
no es él) y recibe 403. Verificado con una prueba de concurrencia real que fuerza ambos órdenes.

### Creación manual con `assigned_to` restringido para el asistente
Al crear una tarea manual, el asistente solo puede fijar `assigned_to` a sí mismo o `NULL` — intentar
asignarla a otro usuario sin tener `task:assign` es **403**, verificado antes de tocar la base de datos.

## Alternativas consideradas
- **Extender el patrón de permiso de rol puro también a `task:update`/`task:complete`**: rechazada — habría
  permitido a cualquier asistente operar la tarea de un compañero, contradiciendo el propósito operativo real
  del despacho (cada colaborador gestiona su propia carga de trabajo).
- **Autoasignación de tareas sin responsable por el asistente**: rechazada en este bloque — la asignación
  queda como decisión exclusiva de admin/contador; se documenta como candidata explícita para un bloque
  futuro si la operación real del despacho lo requiere.
- **Resolver la carrera comentario-vs-reasignación con una comprobación previa al bloqueo**: rechazada —
  cualquier lectura de `assigned_to` antes del `SELECT ... FOR UPDATE` puede quedar obsoleta exactamente por
  la razón que ADR-0058 ya documentó para Bloque 5 (`populate_existing`).

## Consecuencias
- `test_route_permission_coverage`/matriz 200/403 se extienden a los 39 permisos totales, incluidas 3 filas
  de asistente-denegado para `task:assign`/`task:cancel` (sin denegación para `task:sync`, concedido a los 3
  roles).
- Esta es la **única** excepción de todo el repositorio al patrón "permiso de rol, nunca de fila" — cualquier
  bloque futuro que necesite un patrón similar debe documentar su propio ADR, no asumir que este precedente
  se generaliza automáticamente.
