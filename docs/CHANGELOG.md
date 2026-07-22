# Changelog — Luis IA Contable

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/).

## [Unreleased]

Próximo bloque de Fase 1: bloqueo de cuenta por intentos fallidos, motor de cronogramas tributarios versionado, control de declaraciones y honorarios.

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
