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

## Pruebas de integración (`backend/tests/integration/`)

Ejecutadas contra una base de datos PostgreSQL real de pruebas (`TEST_DATABASE_URL`), con cada test aislado mediante una transacción con SAVEPOINT que se revierte al finalizar.

| Archivo | Cubre |
|---|---|
| `test_auth_api.py` | Login correcto crea sesión y cookie; login fallido no crea sesión y audita `LOGIN_FAILURE`; `/me` sin cookie devuelve 401; logout revoca la sesión |
| `test_clients_api.py` | CRUD completo, unicidad `(tenant_id, ruc)`, rechazo de RUC inválido (422), cambio de `service_status` auditado, filtros de listado |
| `test_obligations_api.py` | Catálogo se lista desde el seed, asignación de obligación a cliente, rechazo de asignación duplicada, desactivación |
| `test_audit_logs.py` | Cada operación anterior genera la entrada esperada en `audit_logs` |

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
