# Changelog — Luis IA Contable

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/).

## [Unreleased]

### Fase 1, Bloque 3.1 — Ciclo operativo (periodos y declaraciones)

> Documentado retroactivamente: implementado y verificado en el commit `565a3f7` (etiqueta `v0.3.0-alpha.1`), no se había registrado en este archivo hasta ahora.

- **Periodos tributarios canónicos** (`tax_periods`): mensual/anual/especial, apertura/cierre.
- **Declaraciones** (`declarations`) con snapshot versionado del cronograma resuelto y 5 tablas de historial (estado, vencimiento, asignación, presentación, reapertura), máquina de estados de flujo/presentación con regresiones auditadas, bloqueo optimista (`row_version`).
- **Generación masiva idempotente** con detección de vista previa obsoleta (`STALE` vía checksum) y clasificación por ítem, cada uno protegido por su propio `SAVEPOINT`.
- **Tablero mensual básico** (workflow, presentación, riesgo calculado en vivo vs. fecha de vencimiento).
- Cierre de declaraciones deliberadamente restringido en esta subentrega: `tax_assessments`/`declaration_document_items` todavía no existían.

### Fase 1, Bloque 3.2 — Control documental, impuestos, honorarios, cierre completo, tablero extendido, reportes

- **Control documental**: catálogo híbrido sistema/tenant, asignación por cliente con vigencia real (general o vinculada a una obligación concreta), sincronización idempotente de ítems por declaración (individual y masiva por periodo, sin backfill destructivo), integridad cruzada cliente/declaración/requisito vía FKs compuestas (ADR-0033 a ADR-0036).
- **Determinación y pagos tributarios**: determinación versionada con correcciones e historial condicional, saldo siempre contra la determinación vigente, idempotencia de pagos (`idempotency_key`), concurrencia real con `SELECT ... FOR UPDATE`, sobrepago con confirmación explícita y motivo, anulación con motivo (ADR-0038 a ADR-0043).
- **Honorarios profesionales**: acuerdos con vigencia real (`supersede`/`cancel` cierran la vigencia de verdad), cargos con estado persistido separado del riesgo de cobranza calculado en vivo, recibos con regla de importe agregado — dominio completamente independiente del tributario (ADR-0044 a ADR-0046).
- **Cierre operativo completo**: cierre normal (tres precondiciones) y cierre excepcional con pendientes, gateado por un interruptor global + rol de administrador + motivo explícito (ADR-0047).
- **Tablero y riesgo extendidos**: 3 valores nuevos de riesgo (`CLOSED_WITH_PENDING`/`PENDING_TAX`/`PENDING_DOCUMENTS`), agregados de impuestos y honorarios sin duplicar filas del tablero (ADR-0048).
- **Reportes**: exportación CSV/Excel de 10 tipos de datos, sanitizada contra *CSV formula injection*, límite de filas configurable, `openpyxl` declarado en `requirements.txt` (ADR-0049).
- **Migraciones** `0008_document_control`, `0009_tax_assessments_payments`, `0010_professional_fees` — cadena lineal desde `0007`, verificadas upgrade/downgrade/re-upgrade con datos preexistentes de 3.1.
- **Frontend**: pestañas de declaración (Documentos, Impuestos, Cierre), honorarios y requisitos documentales por cliente, tablero con nuevas columnas/filtros, página de reportes.
- ADRs 0016–0032.
- Backend: cobertura de pruebas mantenida ≥ 84 % (311+ pruebas), incluida una prueba de concurrencia con dos conexiones reales y una prueba de rollback atómico forzado.

### Fase 1, Bloque 4 — RBAC real y gestión de usuarios

- **Catálogo de permisos**: `Permission` (30 valores) + `ROLE_PERMISSIONS` declarativo (`admin`=30,
  `contador`=27, `asistente`=13), fallo cerrado ante rol/permiso desconocido; separación
  `document:item_write` (operar ítems, incluido asistente) vs. `document:manage` (configuración documental,
  solo admin/contador) (ADR-0050).
- **Gestión de usuarios**: alta con contraseña temporal obligatoria, cambio de rol, desactivación/reactivación
  con motivo y revocación de sesiones, restablecimiento administrativo de contraseña — 6 endpoints
  (`/api/v1/users/*`), protección concurrente real del último admin activo por tenant vía
  `SELECT ... FOR UPDATE` sin deadlock, migración `0012_users_role_management` con preflight de 3
  verificaciones antes de cualquier DDL (ADR-0051).
- **Aplicación de permisos**: `require_permission` sustituye a `ensure_admin_role` (eliminada) en los 15
  routers existentes; cobertura estructural verificada por prueba dedicada (allowlist cerrada, nunca por
  prefijo) y matriz funcional 200/403 de los 30 permisos (ADR-0052).
- **Frontend**: `CurrentUserProvider`/`useCurrentUser` como fuente única de permisos (`GET /auth/me`),
  pantalla `/users` de administración, pantalla obligatoria de cambio de contraseña, gateo completo de los 16
  componentes/páginas mutantes existentes (ocultar o deshabilitar, nunca solo por color).
- ADRs 0050–0052.
- Backend: 397 pruebas (87 % cobertura), incluidas 2 pruebas de concurrencia real del último admin y una
  matriz 200/403 de 30 casos.

## [0.2.0] - 2026-07-22 - Fase 1 bloque 2

### Añadido
- **Endurecimiento de autenticación y sesiones**: bloqueo temporal por intentos fallidos con backoff progresivo y desbloqueo administrativo local (ADR-0009), mitigación de enumeración por tiempo mediante hash ficticio precalentado, protección CSRF double-submit cookie (ADR-0010), expiración de sesión por inactividad y absoluta, rotación de sesión en login, límite de sesiones concurrentes, vista de sesiones activas y cierre de otras sesiones, revocación al cambiar contraseña, UI de advertencia de inactividad con cuenta regresiva.
- **Motor de cronogramas tributarios versionado**: fuentes, versiones con máquina de estados (`DRAFT`→`IMPORTED`→`PENDING_REVIEW`→`APPROVED`→`SUPERSEDED`, más `REJECTED`/`CANCELLED`), entradas y excepciones con últimos dígitos normalizados (ADR-0014), historial de aprobación, importación de CSV con validación/diff/vista previa/confirmación (ADR-0012), reemplazo automático de versiones del mismo alcance exacto protegido por un índice único parcial (ADR-0011), resolución de vencimientos con especificidad determinista por tupla (ADR-0015), vista previa de vencimiento por cliente/obligación/periodo con aviso de datos ficticios.
- **Categorías tributarias del cliente**: catálogo abierto y asignación con vigencia histórica (restricción `EXCLUDE USING gist`), desacoplado de `clients.tax_regime`; `is_good_taxpayer` ahora se calcula dinámicamente (ADR-0013).
- Reloj inyectable (`app/core/clock.py`, ADR-0008) para pruebas de expiración/bloqueo sin `sleep`.
- Migraciones `0002_auth_hardening`, `0003_tax_categories`, `0004_tax_schedule_engine`, todas verificadas: upgrade desde `0001` con datos preexistentes, creación desde una base nueva sin `pgcrypto` preinstalada, downgrade × 3 y re-upgrade.
- Frontend: `/tax-categories` (catálogo y asignación por contribuyente) y `/tax-schedules` (fuentes, importación de CSV, revisión/aprobación, comparación de versiones, vista previa de vencimiento).
- ADRs 0008–0015.
- 55 pruebas automatizadas nuevas de backend (131 en total), 89 % de cobertura; `ruff`, `tsc --noEmit`, `eslint` y `next build` sin errores.

### Verificado manualmente en navegador
- Login → bloqueo tras intentos fallidos → creación de categoría → asignación a contribuyente → creación de fuente de cronograma → importación de CSV ficticio → confirmación → envío a revisión → aprobación → vista previa de vencimiento con aviso de datos de demostración.

### Notas de implementación
- `clients.is_good_taxpayer` (columna del Bloque 1) queda congelada: ya no se lee ni se escribe desde código nuevo (ver ADR-0013).
- Se eliminó `tenant_repository.get_default()` (y el archivo completo) al retirarse el fallback de "tenant por defecto" del login.

## [0.1.0] - 2026-07-22 - Fase 0 + Fase 1 bloque 1

### Añadido
- Estructura de repositorio, documentación base (docs/) y ADRs 0001–0007.
- Backend FastAPI (Python 3.12, SQLAlchemy 2.0 síncrono, Alembic) con autenticación por cookie de sesión, CRUD de contribuyentes, validación de RUC (dígito verificador), catálogo de obligaciones y asignación por cliente, auditoría básica.
- Proveedor de consulta de RUC simulado (`FakeRucLookupProvider`) y stub del proveedor real (`SunatRucLookupProvider`, lanza `NotImplementedError`).
- Modelos SQLAlchemy y migración inicial de Alembic (`tenants`, `users`, `sessions`, `audit_logs`, `clients`, `obligation_catalog`, `client_obligations`).
- Frontend Next.js 16 (App Router, Turbopack) con login, listado/alta/edición de contribuyentes, cambio de estado de servicio y gestión de obligaciones asignadas. PWA con manifest y service worker manual (ver ADR-0006).
- Docker Compose (`db`, `backend`, `frontend`, los tres con healthcheck) y scripts PowerShell: `install`, `start`, `stop`, `restart`, `migrate`, `seed`, `backup`, `restore`, `test`, `reset-dev`, `logs`.
- 44 pruebas automatizadas de backend (unitarias, integración y arranque) en verde; `ruff`, `tsc --noEmit` y `eslint` sin errores.

### Verificado manualmente en navegador
- Login → listado de contribuyentes → alta con consulta de RUC simulada → cambio de estado de servicio → asignación/desactivación/remoción de obligaciones → logout → redirección a `/login` en rutas protegidas.
- `backup.ps1` → `restore.ps1` → `reset-dev.ps1` contra datos ficticios.

### Notas de implementación
- `create-next-app` instaló Next.js 16.2.11, más reciente que lo previsto en el ADR original; Turbopack es el compilador por defecto desde esta versión. Se actualizó ADR-0006 (PWA): Serwist es incompatible con Turbopack, se usa un service worker escrito a mano.
- `middleware.ts` fue renombrado a `proxy.ts` en Next.js 16 (ver `docs/ARCHITECTURE.md`).
