# Plan de pruebas — Luis IA Contable

## Reglas generales

- Solo contribuyentes y usuarios ficticios. Nunca RUC, contraseñas o datos reales.
- Ninguna prueba automatizada ejecuta acciones contra portales oficiales (SUNAT, SUNAFIL, etc.).
- Toda integración externa se simula (proveedor Fake, mocks) en pruebas automatizadas.

## Pruebas unitarias (`backend/tests/unit/`)

| Archivo | Cubre |
|---|---|
| `test_ruc_validator.py` | Algoritmo de dígito verificador (módulo 11) para prefijos 10/15/17/20; rechazo de longitud incorrecta, caracteres no numéricos y dígito verificador inválido |
| `test_password_security.py` | Hash Argon2id: salts distintos en cada llamada, verificación correcta/incorrecta, formato del hash |
| `test_session_token.py` | Generación de token con entropía adecuada, no reversibilidad del hash almacenado |
| `test_ruc_lookup_fake_provider.py` | Contrato de `RucLookupProvider`; `FakeRucLookupProvider` determinista y marcado `is_simulated=True`; `SunatRucLookupProvider` lanza `NotImplementedError` |
| `test_identifier_normalization.py` | `normalize_login_identifier()`: NFC, minúsculas, `strip`, rechazo de caracteres de control, longitud máxima |

## Pruebas de integración (`backend/tests/integration/`)

Ejecutadas contra una base de datos PostgreSQL real de pruebas (`TEST_DATABASE_URL`), con cada test aislado mediante una transacción con SAVEPOINT que se revierte al finalizar.

| Archivo | Cubre |
|---|---|
| `test_auth_api.py` | Login correcto crea sesión, cookie de sesión y cookie CSRF; `/me` sin cookie devuelve 401; logout revoca la sesión y borra la cookie CSRF |
| `test_clients_api.py` | CRUD completo, unicidad `(tenant_id, ruc)`, rechazo de RUC inválido (422), cambio de `service_status` auditado, filtros de listado |
| `test_obligations_api.py` | Catálogo se lista desde el seed, asignación de obligación a cliente, rechazo de asignación duplicada, desactivación |
| `test_audit_logs.py` | Cada operación anterior genera la entrada esperada en `audit_logs`; ningún fallo de login lo hace (ver ADR-0009) |
| `test_login_lockout.py` | Bloqueo tras N intentos fallidos, backoff progresivo, desbloqueo tras expirar la duración, login exitoso tras desbloqueo, 100 solicitudes durante bloqueo activo no cambian `locked_until` ni insertan filas nuevas, variaciones de mayúsculas/espacios cuentan como el mismo identificador |
| `test_login_timing_mitigation.py` | Cuenta inexistente y contraseña incorrecta de cuenta real generan exactamente el mismo código HTTP, mensaje, forma de respuesta y conjunto de escrituras persistentes (`login_attempts` + `security_events`, cero `audit_logs`); login exitoso sí genera `audit_logs.LOGIN_SUCCESS` |
| `test_session_security.py` | Expiración por inactividad y absoluta, rotación de sesión en login, revocación en logout y al cambiar contraseña, límite de sesiones concurrentes, listado de sesiones activas, cierre de otras sesiones, reutilización de una sesión revocada |
| `test_csrf.py` | Mutación sin header `X-CSRF-Token` rechazada, header incorrecto rechazado, rotación en login, invalidación al revocar sesión, `keep-alive` protegido |
| `test_migrations.py` | `alembic upgrade head` desde `0001` con una sesión preexistente (creada antes de `csrf_token_hash`) |
| `test_migration_pgcrypto_bootstrap.py` | `alembic upgrade head` sobre una base de datos completamente nueva, sin `pgcrypto` preinstalada |
| `test_client_tax_category_validity.py` | Asignación de categoría, rechazo de categoría desconocida (404), solapamiento de vigencia rechazado (409, `EXCLUDE` constraint), renovación no solapada permitida, remoción de asignación |
| `test_good_taxpayer_dynamic.py` | `is_good_taxpayer` falso por defecto, verdadero con categoría activa, falso tras expirar la vigencia, rechazo 422 al enviarlo en creación/edición, cálculo batched correcto en listados |
| `test_tax_schedule_transitions.py` | Transiciones válidas/inválidas de la máquina de estados, comentario/motivo obligatorio en aprobar/rechazar/cancelar, inmutabilidad de una versión `APPROVED`, reemplazo automático de la versión previa del mismo alcance exacto, años fiscales distintos no se reemplazan entre sí |
| `test_tax_schedule_resolver.py` | Fecha de referencia (mensual/anual/formato inválido), especificidad determinista por tupla (categoría > general, dígitos específicos > `NULL`, excepción > entrada), ambigüedad explícita ante empate, error cuando no hay regla aplicable o no hay versión aprobada |
| `test_tax_schedule_import.py` | CSV válido con y sin cronograma previo, encabezados faltantes, fecha/dígitos/categoría inválidos, filas duplicadas, extensión no `.csv`, archivo sobredimensionado, confirmación crea versión+entradas, doble confirmación rechazada, rechazo de importación, diff contra versión aprobada previa |
| `test_tax_schedule_concurrency.py` | El índice único parcial impide dos versiones `APPROVED` del mismo alcance a nivel de base de datos; alcances distintos sí pueden coexistir aprobados; una carrera de aprobación simulada se traduce a `ConcurrentApprovalConflictError` sin corromper el estado |
| `test_tax_schedule_misc_endpoints.py` | Fuente/versión no encontradas (404), exportación de entradas a CSV, comparación de versiones, historial de aprobación vacío en una versión nueva, vista previa de vencimiento (cliente inexistente, sin versión aprobada, éxito con `demo_disclaimer`) |
| `test_tax_periods_canonicity.py` | Cada rama del `CHECK` de canonicidad (`MONTHLY`/`ANNUAL`/`SPECIAL`), apertura/cierre de periodo |
| `test_declaration_state_machine.py` | Historial de nacimiento (5 tablas, `audit_correlation_id` compartido), regresiones de flujo auditadas, transición inválida rechazada, conflicto de `row_version`, corrección requiere fecha de presentación, cierre normal bloqueado en 3.1 (antes de existir Dominios A-C), override deshabilitado por defecto, estado terminal `NOT_APPLICABLE`, cancelación con motivo |
| `test_declaration_generation.py` | Vista previa, detección de `STALE` por checksum, confirmación idempotente, clasificación por ítem, `SAVEPOINT` por ítem no revierte el lote completo |
| `test_declaration_risk.py` | `compute_risk_status` vs. `risk_status_sql_expression`: paridad para los 11 valores (incluidos `CLOSED_WITH_PENDING`/`PENDING_TAX`/`PENDING_DOCUMENTS` de Bloque 3.2) |
| `test_declarations_multitenant_integrity.py` | FKs compuestas rechazan cruces de tenant en cada tabla nueva de 3.1 |
| `test_declaration_documents.py` / `test_declaration_documents_api.py` | Catálogo sistema/personalizado con rechazo cross-tenant, sincronización idempotente, cruce entre clientes rechazado por SQL directo, ítems históricos conservados tras desactivar una asignación, sincronización administrativa por periodo, cambio de estado + recálculo de `document_status`, flujo HTTP completo con auditoría y CSRF |
| `test_tax_assessments.py` / `test_tax_assessments_payments_api.py` | Determinación inicial (4 filas en una transacción), corrección que aumenta/reduce/genera exceso, historial condicional (ADR-0039), `UNIQUE(sequence_number)`, flujo HTTP completo |
| `test_tax_payments.py` | Pago parcial/total, idempotencia (reenvío igual/payload distinto), sobrepago (rechazo/motivo faltante/éxito), anulación con historial, rollback atómico forzado tras fallo de historial/auditoría |
| `test_tax_payment_concurrency.py` | Concurrencia real con **dos conexiones/hilos independientes** sincronizados con `threading.Event` (nunca solo `sleep`): el segundo pago espera el bloqueo real del primero, suma final correcta sin pérdida |
| `test_professional_fees.py` / `test_professional_fees_api.py` | Acuerdo sin/con solapamiento, `supersede`/`cancel` cierran vigencia real y permiten un acuerdo posterior, cargo idempotente, emisión con/sin `due_date`, pago con historial de nacimiento/anulación, idempotencia y sobrepago de honorarios, recibo con límite agregado y rechazo de número reutilizado, independencia impuesto/honorario (2 pruebas), flujo HTTP completo |
| `test_professional_fee_risk_sql_parity.py` | Paridad Python/SQL de `compute_fee_collection_risk` (5 valores) y cambio de riesgo con el paso del tiempo sin alterar el `status` persistido (`frozen_clock`) |
| `test_declaration_close_full.py` | Cierre normal exitoso con las tres precondiciones cumplidas, cierre normal rechazado con documentos pendientes, cierre excepcional exitoso (flag + admin + motivo), rechazo 403 a un usuario no-admin |
| `test_dashboard.py` | Riesgo extendido visible en el tablero; un cliente con múltiples documentos/pagos/cargos en el mismo periodo produce **una sola fila por declaración** con importes agregados correctos |
| `test_reports_api.py` | Los 10 tipos de reporte en CSV/Excel, límite de filas configurable con truncamiento, auditoría por exportación, CSRF, tipo de reporte desconocido (422) |

## Pruebas unitarias adicionales (Bloque 3.2)

| Archivo | Cubre |
|---|---|
| `test_declaration_document_status.py` | `compute_document_status`: 7 combinaciones de estado de ítems |
| `test_tax_payment_status.py` | `compute_tax_payment_status`: 6 combinaciones de monto/saldo; fórmula de determinación con `Decimal` |
| `test_professional_fee_charge_status.py` | `compute_charge_payment_status`: 4 combinaciones |
| `test_professional_fee_risk.py` | `compute_fee_collection_risk`: 7 combinaciones de vencimiento/estado/saldo |
| `test_report_service.py` | Sanitización CSV contra fórmula, Excel con hojas "Datos"/"Metadatos" |

## Pruebas de RBAC y gestión de usuarios (Bloque 4)

| Archivo | Cubre |
|---|---|
| `test_permissions.py` | 31 permisos exactos, pertenencia por rol (`admin`=31, `contador`=28, `asistente`=13), fallo cerrado ante rol/permiso desconocido |
| `test_user_admin_invariant.py` | `removes_last_active_admin`: los 6 casos (desactivar/degradar único admin activo, ≥2 admins, objetivo inactivo, promoción, reactivación, mismo rol) |
| `test_migration_0012_users_rbac.py` | Upgrade/downgrade/re-upgrade con datos válidos; rol desconocido bloquea preflight; usuario inactivo heredado sin metadatos bloquea preflight; 4 casos de tenant sin admin activo (`NOT EXISTS` desde `tenants`); ausencia de DDL parcial tras cualquier fallo de preflight |
| `test_users_api.py` | CRUD completo de usuarios, cambio de rol, desactivación/reactivación, restablecimiento de contraseña, aislamiento multitenant, auto-operación rechazada, rechazo de `role == target.role` |
| `test_users_last_admin_concurrency.py` | Concurrencia real (dos conexiones, `threading.Event`): dos admins desactivándose/degradándose mutuamente siempre dejan exactamente 1 admin activo |
| `test_route_permission_coverage.py` | Todo endpoint bajo `/api/v1/` declara `require_permission` o está en la allowlist cerrada `EXEMPT_ROUTES`; toda ruta de `/api/v1/users` exige `user:read`/`user:manage` |
| `test_permission_matrix_api.py` | Matriz 200/403 real para cada uno de los 31 permisos, aprovechando que FastAPI resuelve `Depends()` antes de validar el cuerpo |

Frontend: `permissions.test.ts` (vitest) prueba `hasPermission` como función pura sin reconstruir el mapa de
roles. El gateo de los 16 componentes/páginas existentes se verifica manualmente con los 3 roles reales (ver
informe de cierre del Bloque 4).

## Pruebas de almacenamiento real de documentos (Bloque 5)

| Archivo | Cubre |
|---|---|
| `test_file_storage_backend.py` | `LocalFileStorageBackend`: hash/tamaño en streaming, límite aplicado durante la lectura (no solo `Content-Length`), rechazo de archivo vacío, `promote` atómico, `delete` idempotente, contención de ruta, `iter_keys`/`stat` |
| `test_document_file_content_validation.py` | Firma real PDF/JPEG/PNG; estructura OOXML válida DOCX/XLSX; ZIP genérico renombrado rechazado; ambigüedad word+xl rechazada; macro-habilitado rechazado (`vbaProject.bin`, y vía `[Content_Types].xml`); límites de ZIP-bomb (entradas, tamaño total/por-entrada, ratio de compresión); traversal de nombre de entrada |
| `test_document_file_naming.py` | Saneamiento de ruta (unix/windows/traversal), rechazo de control/CRLF, truncado a 255, `Content-Disposition` ASCII/no-ASCII/RFC 6266 |
| `test_document_file_compensation.py` | `compensate_physical_write` nunca lanza; compensa el blob ante fallo de `flush()` en la carga |
| `test_declaration_document_files_api.py` | Flujo HTTP completo (subir/listar/descargar/reemplazar/eliminar), permisos (asistente sube, no reemplaza/elimina), 409 sobre versión no `ACTIVE`, aislamiento multitenant, auditoría, regresión de `file_metadata` legado |
| `test_document_file_chain_integrity.py` | 4 pruebas SQL directas de integridad de cadena (versión sin predecesor, predecesor sin versión, reemplazo cruzado de ítem, reemplazo cruzado de cadena), índice único parcial rechaza dos `ACTIVE` en la misma cadena |
| `test_document_file_replace_concurrency.py` | Concurrencia real (dos conexiones, `threading.Event`): dos reemplazos simultáneos del mismo `file_id` (uno gana, 409 al otro); reemplazo vs. eliminación simultáneos; fallo forzado del segundo `flush()` revierte a `ACTIVE` y compensa el blob |
| `test_migration_0013_document_file_storage.py` | Upgrade/downgrade/re-upgrade de la tabla y todos sus constraints/índices nombrados |
| `test_document_integrity_report_api.py` | Las 5 categorías exactas del reporte de integridad; filtro por categoría; aislamiento por tenant; permiso `document:manage`; ninguna entrada expone `storage_key` |
| `test_document_storage_archive_cli.py` | `archive`/`extract` del volumen (round-trip, `--confirm` requerido, entrada faltante rechazada); regresión de que `extract` nunca elimina el punto de montaje de la raíz, solo su contenido |

Frontend: `documentFileValidation.test.ts` (vitest) prueba la validación de extensión/tamaño en el cliente
como función pura. El flujo completo (subir/descargar/reemplazar/eliminar, rechazo de contenido/tamaño
inválido, gateo por rol) se verificó manualmente en navegador con los 3 roles reales (ver informe de cierre
del Bloque 5). Backup/restauración (`backup.ps1`/`restore.ps1`) se verificó manualmente de extremo a extremo
contra el entorno de desarrollo, incluida la reversión automática ante fallo — no tienen suite `pytest`, mismo
criterio que el resto de los scripts operativos de este repositorio.

## Pruebas de gestión operativa de tareas (Fase 2, Bloque 1)

| Archivo | Cubre |
|---|---|
| `test_migration_0014_operational_tasks.py` | Upgrade/downgrade/re-upgrade; las 11 FK + 7 `CHECK` de `operational_tasks`, 4 FK + 8 `CHECK` de `task_change_history`, 2 FK + 1 `CHECK` de `task_comments`, las 4 constraints auxiliares en tablas preexistentes |
| `test_operational_task_constraints.py` | Intentos SQL/ORM directos contra las 5 formas estructurales, la cadena `charge→agreement→client`, coherencia de generación/resolución/vencimiento/`starts_before_due`, coherencia por `event_type` de `task_change_history`, límite de `task_comments.body` |
| `test_operational_tasks_manual.py` | Ciclo de vida manual vía API real: creación (con/sin `client_id`, rechazo cross-tenant), `PATCH` sin campos → 422, sin cambio real → 409 `TASK_NO_CHANGES`, `expected_version` obsoleto → 409 `TASK_VERSION_CONFLICT`, start/complete/cancel, reasignación (mismo valor → 409, usuario inactivo → 422), autorización por fila del asistente, comentarios (terminal → 409, vacío → 422), historial, filtro por `client_id` |
| `test_task_generation.py` | Las 3 reglas con plantillas exactas comparadas completas, idempotencia, `OBSERVED` incluido en `DOCUMENT_COLLECTION`, resolución automática (`FILED`→`COMPLETED`), realineación de vencimiento, conservación de `due_at` tras `due_is_overridden` |
| `test_sync_operational_tasks_cli.py` | El CLI persiste con un único `commit()`; tenant inexistente no crea ninguna fila y sale con código distinto de cero; una excepción forzada revierte todo; idempotencia en una segunda ejecución |
| `test_task_agenda_and_filters.py` | Fronteras exactas de `time_bucket` con reloj congelado (tarea sin hora a las 23:59:59 Lima vs. 1s después; tarea con hora 10:00 a las 09:59 vs. 10:01), ausencia de solape entre baldes, exclusión de tareas terminales, `assigned_to=me/unassigned` y valor inválido → 422, resúmenes de agenda/alertas, `task_summary` en el dashboard |
| `test_operational_task_concurrency.py` | Dos conexiones reales (12 casos): completar-vs-completar, completar-vs-cancelar, reasignar-vs-completar/reprogramar, dos reprogramaciones concurrentes (uno gana, 409 al otro), comentario-vs-reasignación en ambos órdenes, sincronización realineando/resolviendo vs. acción humana concurrente, dos sincronizaciones mutando las mismas tareas existentes bloqueadas por `id ASC`, y dos sincronizaciones insertando las MISMAS `generation_key` descubiertas en orden opuesto -- normalizadas al mismo orden antes de insertar en el índice único parcial, sin interbloqueo |
| `test_reports_api.py` (extendido) | 11º tipo `TASKS` incluido en la iteración genérica de los 11 tipos; cliente resuelto correctamente en el reporte |
| `test_permissions.py`/`test_permission_matrix_api.py` (extendidos) | `Permission` con 39 valores; `admin`=39/`contador`=36/`asistente`=19; matriz 200/403 de los 8 permisos nuevos |

Frontend: `/tasks`, `/tasks/[id]`, `/tasks/new`, `/agenda` y la extensión del dashboard se verificaron
manualmente en navegador con roles `admin` y `asistente` reales (creación sin/con cliente, ciclo de vida
completo con historial, comentarios, botones deshabilitados —no ocultos— del asistente sobre una tarea sin
responsable, agenda y tablero con las tarjetas canónicas).

## Pruebas de integración Google OAuth (Fase 3, Bloque 3.1)

| Archivo | Cubre |
|---|---|
| `test_migration_0015_google_oauth.py` | Upgrade/downgrade/re-upgrade `0014→0015→0014→0015`; las 4 `UNIQUE` + 7 `CHECK` de `google_oauth_connections` y la 1 `UNIQUE` + 7 `CHECK` de `google_oauth_authorization_attempts` |
| `test_google_oauth_constraints.py` | Intentos SQL/ORM directos: coherencia de estado (`ACTIVE`/`NEEDS_REAUTH`/`DISCONNECTED`), allowlist de `reauth_reason` (sin `INSUFFICIENT_SCOPE`), coherencia de la máquina de estados del intento (`PENDING`/`PROCESSING`/`SUCCEEDED`/`FAILED`), formato de `state_hash`, `scopes_granted`/`requested_scopes` como array JSON, FK multitenant |
| `test_integration_crypto.py` | Sobre AES-256-GCM: AAD incorrecto (fila/tabla/tenant/usuario/tipo de token distinto) falla la autenticación; clave inválida/ausente → `IntegrationKeyMissingError`; fingerprint estable para la misma clave y distinto para claves distintas |
| `test_google_oauth_service.py` | Flujo de 12 pasos del callback con `FakeOAuthProvider`: `state` inexistente/ya consumido → `INVALID_STATE` sin escritura; `state` expirado → purga + `FAILED`/`STATE_EXPIRED`; `error` de Google → `FAILED`/`AUTHORIZATION_DENIED`; `code_verifier` irrecuperable tras consumir el `state`; `email_verified=false` → rechazo; `sub` vacío → rechazo defensivo (`USERINFO_FAILED`); primera conexión sin `refresh_token` → `REFRESH_TOKEN_REQUIRED`, ninguna conexión `ACTIVE` creada; reconexión desde `NEEDS_REAUTH` sin `refresh_token` → mismo error, conexión sin cambios y sin incrementar `version` (`test_reconnect_from_needs_reauth_without_refresh_token_fails`, distinta de la incremental sobre `ACTIVE` que sí conserva su refresh_token); autorización incremental sin `refresh_token` → se conserva el cifrado existente; `sub` distinto sobre `ACTIVE`/`NEEDS_REAUTH` → 409 `GOOGLE_ACCOUNT_MISMATCH`; reemplazo de cuenta tras `DISCONNECTED` → `first_connected_at` no cambia, `version` incrementa, auditoría `INTEGRATION_GOOGLE_ACCOUNT_REPLACED`; desconexión con revocación exitosa y con revocación fallida (best-effort); desactivación de usuario limpia la conexión sin llamar a Google |
| `test_google_oauth_api.py` | Matriz de permisos 200/403 (`integration:connect`/`integration:disconnect`, totales 41/38/21); ownership (nunca la conexión de otro usuario); `capability=CALENDAR`/`DRIVE` → 422 en este bloque; Google no configurado → `409 INTEGRATION_MISCONFIGURED` en `authorize`; `GET /status` con `configured: bool` (nunca `500`, incluso sin ninguna configuración de integración presente); callback con `state` desconocido/reutilizado → redirección con error, nunca `500` sin manejar; ausencia de tokens en cualquier respuesta JSON |
| `test_oauth_provider_selection.py` (unit) | `get_oauth_provider()` nunca selecciona el adaptador real sin `client_id`+`client_secret` presentes, aunque `GOOGLE_OAUTH_PROVIDER="google"`; `is_google_oauth_configured()` refleja exactamente la presencia de ambas credenciales |
| `test_google_oauth_concurrency.py` | Dos conexiones PostgreSQL reales: doble callback con el mismo `state` (una gana, la otra `INVALID_STATE`); dos usuarios intentando conectar la misma cuenta Google (`UNIQUE(tenant_id, google_account_id)`) |
| `test_logging_redaction_google.py` | Captura de logging real de un callback completo (éxito y fallo): ausencia de `code`/`state` crudo/tokens en cualquier registro |

Frontend: `settings-integrations-google.test.tsx` (Vitest) — estados conectado/desconectado/`NEEDS_REAUTH`,
gating por permiso, ausencia de cualquier token en el DOM/estado de React, manejo de `google_status`/
`google_error` en la URL de retorno. **Nota:** este archivo no existe todavía — el proyecto no tiene una
convención de pruebas de componente (`.tsx`, React Testing Library) establecida (los 57 tests actuales son
todos de lógica pura en `src/lib/*.test.ts`); introducirla para una sola pantalla no se justificó en esta
ronda, así que el mensaje "Google no configurado" solo queda verificado por inspección directa del código y
manualmente en navegador, no por una prueba automática de componente.

**PowerShell (backup/restore, Bloque 3.1)**: `scripts/tests/Test-GoogleIntegrationKeyReconciliation.ps1`
(invoca la función real usada por `restore.ps1`; manifiesto sin `fingerprint` nunca se considera compatible
aunque el `key_id` coincida) y `scripts/tests/Test-NativeStepErrorHandling.ps1` (stderr con exit code 0 nunca
aborta bajo `$ErrorActionPreference="Stop"`; exit code distinto de cero sigue siendo detectable).

**Validación manual no incluida en la suite automática (`GOOGLE_LIVE_VALIDATION_DEFERRED`, ver ADR-0063)**: el
spike de aceptación real de PKCE por Google y el smoke test end-to-end contra una cuenta Google real requieren
un cliente OAuth real y una cuenta Google, que el usuario todavía no dispone — no se ejecutan en CI ni en esta
suite, y son obligatorios antes de habilitar los Bloques 3.2/3.3.

## Pruebas de arranque (smoke)

- `backend/tests/smoke/test_app_boot.py`: la aplicación FastAPI arranca sin excepciones; `/api/v1/health/live` y `/api/v1/health/ready` responden 200.
- `tests/smoke/test_stack_smoke.ps1`: tras `docker compose up`, valida con peticiones HTTP reales que backend y frontend respondan correctamente.

## Cómo ejecutar

```powershell
.\scripts\test.ps1
```

Ejecuta `ruff check` + `pytest --cov` dentro del contenedor `backend`, y `eslint` + `tsc --noEmit` dentro del contenedor `frontend`. Actúa como el "CI local" de esta fase.

## Fuera de alcance en esta entrega

Pruebas end-to-end de navegador (Playwright) para el flujo completo de UI quedan referenciadas para una fase posterior, una vez el frontend tenga más pantallas estables.
