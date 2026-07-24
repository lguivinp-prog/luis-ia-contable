# ADR-0051: Ciclo de vida de usuarios con contraseña temporal y protección concurrente del último admin

## Estado
Aceptado

## Contexto
El Bloque 4 introduce gestión real de usuarios (`admin`/`contador`/`asistente`): alta, cambio de rol,
desactivación, reactivación y restablecimiento de contraseña. Dos riesgos concretos debían resolverse antes
de exponer estas operaciones: (1) que un tenant quede sin ningún usuario `admin` activo — un estado del que
nadie con acceso a la aplicación podría recuperarse sin intervención directa en la base de datos — incluso
bajo dos solicitudes concurrentes; y (2) que las contraseñas iniciales asignadas por un administrador nunca
queden como secreto permanente compartido entre el admin y el usuario nuevo.

## Decisión

### Contraseña temporal
Migración 0012 añade `must_change_password BOOLEAN NOT NULL DEFAULT false` (usuarios existentes migran a
`false` por el propio `DEFAULT`). `user_service.create_user` la fija en `true`; `PATCH
/users/{id}/reset-password` (permiso `user:manage`) también la fija en `true` y revoca todas las sesiones
del usuario objetivo. La dependencia `require_password_ready` (envuelve `get_current_user`) devuelve 403
`{"code": "PASSWORD_CHANGE_REQUIRED"}` mientras el flag esté activo; `require_permission` depende de ella, así
que todo endpoint gateado por permiso hereda el bloqueo sin cablear nada por router. Los endpoints de
autoservicio de `auth.py` (incluido `POST /auth/change-password`, que limpia el flag) permanecen accesibles
para que el usuario pueda completar el cambio obligatorio. El frontend redirige a
`/change-password-required` antes de mostrar cualquier otra pantalla, con excepción explícita de su propia
ruta para evitar un bucle de navegación.

### Protección concurrente del último admin activo
Invariante: cada tenant conserva siempre al menos un usuario activo con `role='admin'`. Protegida en
`deactivate_user` y en `change_role` (cuando el objetivo es hoy un admin activo y la operación lo desactiva o
le quita el rol). Diseño anti-deadlock: `user_repository.lock_target_and_active_admins` ejecuta una única
consulta `SELECT ... FOR UPDATE ... ORDER BY id` que bloquea, en el mismo viaje a la base de datos y en el
mismo orden determinista, tanto la fila objetivo como todas las filas admin activas del tenant — evita el
deadlock que aparecería si dos transacciones bloquearan el mismo conjunto de filas en sentencias separadas
con orden distinto. El predicado puro `removes_last_active_admin(target, admins, *, new_role, deactivating)`
(sin acceso a base de datos, unitariamente testeable) decide si la operación dejaría al tenant sin ningún
admin activo, cubriendo los 6 casos: desactivar/degradar al único admin activo → `True`; ≥2 admins activos →
`False`; objetivo ya inactivo → `False` (no contaba); promover a admin → `False` (nunca reduce); reactivar →
el predicado ni se invoca (nunca reduce); cambiar un rol por el mismo rol → rechazado antes, como 422, sin
llegar al predicado.

Operaciones sobre uno mismo: un usuario no puede desactivarse ni cambiar su propio rol
(`SelfOperationNotAllowedError`, 409) — evita que el único admin activo pierda acceso por accidente sin pasar
siquiera por el chequeo de concurrencia.

### Desactivación y reactivación
Desactivar registra `deactivated_at`/`deactivated_by`/`deactivation_reason` y revoca todas las sesiones del
usuario objetivo (`auth_service.revoke_all_sessions`); reactivar limpia esas tres columnas. El historial de
auditoría (`USER_DEACTIVATE`/`USER_REACTIVATE`) permanece intacto aunque las columnas vigentes se limpien,
igual que el resto del sistema al anular pagos/recibos. Un `CHECK` (`ck_users_deactivation_coherence`)
garantiza que las tres columnas están todas `NULL` cuando `is_active=true` y todas `NOT NULL` cuando
`is_active=false`.

### Migración 0012: preflight antes de cualquier DDL
Antes del primer `ALTER TABLE`, tres verificaciones sobre el esquema `0001-0011` tal cual: (1) ningún `role`
fuera de `{'admin','contador','asistente'}`; (2) ningún usuario `is_active=false` preexistente sin las
columnas de desactivación que la migración va a crear (evita que la migración tenga que inventar un motivo
histórico); (3) todo tenant tiene al menos un admin activo, verificado con `NOT EXISTS` partiendo de
`tenants` (no de `DISTINCT tenant_id` de `users`, que omitiría tenants sin ningún usuario). Cualquier fallo
detiene la migración con un mensaje explícito, sin haber ejecutado DDL alguno.

## Alternativas consideradas
- **Enviar la contraseña inicial por correo**: rechazado (fuera de alcance de este bloque — no hay
  integración de correo saliente); el admin la define directamente y el usuario la cambia en su primer
  acceso.
- **Bloquear con `SELECT` separados para el objetivo y los admins**: rechazado — dos bloqueos independientes
  sobre conjuntos de filas solapados en órdenes potencialmente distintos es exactamente el patrón que produce
  deadlocks bajo dos operaciones concurrentes simétricas.

## Consecuencias
- `backend/tests/unit/test_user_admin_invariant.py` (8 pruebas) cubre los 6 casos del predicado más el caso
  de control "degradar un admin inactivo con ≥1 admin activo no se rechaza indebidamente".
- `backend/tests/integration/test_users_last_admin_concurrency.py` (2 pruebas) usa dos conexiones reales y
  `threading.Event` para demostrar que, bajo dos admins desactivándose/degradándose mutuamente en paralelo,
  siempre queda exactamente 1 admin activo.
- `backend/tests/integration/test_migration_0012_users_rbac.py` (7 pruebas) cubre upgrade/downgrade/re-upgrade
  válido y los 3 fallos de preflight, incluyendo los 4 casos de tenant-sin-admin-activo.
- El frontend redirige a la pantalla de cambio de contraseña obligatorio; ver `USER_MANUAL.md` para el flujo
  paso a paso.
