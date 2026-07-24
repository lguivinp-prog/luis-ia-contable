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
| `test_permissions.py` | 30 permisos exactos, pertenencia por rol (`admin`=30, `contador`=27, `asistente`=13), fallo cerrado ante rol/permiso desconocido |
| `test_user_admin_invariant.py` | `removes_last_active_admin`: los 6 casos (desactivar/degradar único admin activo, ≥2 admins, objetivo inactivo, promoción, reactivación, mismo rol) |
| `test_migration_0012_users_rbac.py` | Upgrade/downgrade/re-upgrade con datos válidos; rol desconocido bloquea preflight; usuario inactivo heredado sin metadatos bloquea preflight; 4 casos de tenant sin admin activo (`NOT EXISTS` desde `tenants`); ausencia de DDL parcial tras cualquier fallo de preflight |
| `test_users_api.py` | CRUD completo de usuarios, cambio de rol, desactivación/reactivación, restablecimiento de contraseña, aislamiento multitenant, auto-operación rechazada, rechazo de `role == target.role` |
| `test_users_last_admin_concurrency.py` | Concurrencia real (dos conexiones, `threading.Event`): dos admins desactivándose/degradándose mutuamente siempre dejan exactamente 1 admin activo |
| `test_route_permission_coverage.py` | Todo endpoint bajo `/api/v1/` declara `require_permission` o está en la allowlist cerrada `EXEMPT_ROUTES`; toda ruta de `/api/v1/users` exige `user:read`/`user:manage` |
| `test_permission_matrix_api.py` | Matriz 200/403 real para cada uno de los 30 permisos, aprovechando que FastAPI resuelve `Depends()` antes de validar el cuerpo |

Frontend: `permissions.test.ts` (vitest) prueba `hasPermission` como función pura sin reconstruir el mapa de
roles. El gateo de los 16 componentes/páginas existentes se verifica manualmente con los 3 roles reales (ver
informe de cierre del Bloque 4).

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
