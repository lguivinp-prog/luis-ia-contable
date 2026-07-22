# Changelog — Luis IA Contable

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/).

## [Unreleased]

Próximo bloque de Fase 1: control de declaraciones por contribuyente/obligación/periodo, control documental mensual, honorarios profesionales.

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
