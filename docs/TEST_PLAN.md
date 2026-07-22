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
