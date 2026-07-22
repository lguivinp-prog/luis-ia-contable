# Arquitectura — Luis IA Contable

## Visión general

Arquitectura modular, local-first, orientada a dominios. Tres componentes principales orquestados por Docker Compose:

```
┌─────────────────┐      HTTP (same-origin vía rewrites)      ┌──────────────────┐
│   frontend       │ ───────────────────────────────────────▶ │    backend       │
│   Next.js (PWA)  │ ◀─────────────────────────────────────── │    FastAPI       │
│   puerto 3000    │              cookie de sesión             │    puerto 8000   │
└─────────────────┘                                            └────────┬─────────┘
                                                                          │ SQLAlchemy
                                                                          ▼
                                                                 ┌──────────────────┐
                                                                 │   db              │
                                                                 │   PostgreSQL 16    │
                                                                 │   puerto 5432      │
                                                                 └──────────────────┘
```

El frontend nunca accede directamente a la base de datos. Todo pasa por el backend, que aplica autenticación, autorización por `tenant_id` y auditoría.

## Tecnologías y versiones

| Componente | Tecnología | Versión objetivo |
|---|---|---|
| Frontend | Next.js | 16.x (App Router, Turbopack) |
| Frontend | React | 19.x |
| Frontend | TypeScript | 5.x, modo `strict` |
| Frontend | PWA | Service worker manual (ver ADR-0006; Serwist es incompatible con Turbopack, compilador por defecto en Next.js 16) |
| Backend | Python | 3.12 |
| Backend | FastAPI | 0.11x |
| Backend | Pydantic | 2.x |
| Backend | SQLAlchemy | 2.0.x (síncrono) |
| Backend | Alembic | 1.13.x |
| Backend | Driver Postgres | psycopg 3 |
| Backend | Hashing de contraseñas | argon2-cffi (Argon2id) |
| Base de datos | PostgreSQL | 16-alpine |
| Infraestructura | Docker Compose | v2 (Compose Spec) |
| Scripts | PowerShell | 5.1+ / 7+ |

Las versiones exactas instaladas quedan fijadas en `backend/requirements.txt`, `frontend/package.json` y las imágenes base de los `Dockerfile`.

## Patrón backend: routers → services → repositories

- **`routers/`**: solo HTTP — parseo de request/response, códigos de estado, invoca `services`. No contiene lógica de negocio ni queries SQL.
- **`services/`**: lógica de negocio y orquestación (valida reglas, coordina repositorios, dispara auditoría).
- **`repositories/`**: acceso a datos puro mediante SQLAlchemy, sin lógica de negocio.
- **`models/`**: definiciones de tablas (SQLAlchemy ORM).
- **`schemas/`**: contratos de entrada/salida de la API (Pydantic).
- **`domain/`**: abstracciones de integraciones externas desacopladas del resto del sistema (p. ej. `domain/ruc_lookup/`), con un puerto (interfaz) y uno o más adaptadores.
- **`core/`**: configuración, seguridad transversal (hashing, tokens), dependencias de inyección, logging.
- **`seeds/`**: datos ficticios de desarrollo, siempre idempotentes.

Este patrón se repite para cada integración futura (Google, Gmail, WhatsApp, RUC lookup, IA): siempre puerto + adaptador, nunca acoplado directamente a un proveedor concreto.

## Patrón frontend: App Router con grupo protegido

- `src/app/login/` — página pública de inicio de sesión.
- `src/app/(app)/` — grupo de rutas protegidas; `proxy.ts` (el archivo `middleware.ts` fue renombrado a `proxy.ts` en Next.js 16) verifica la presencia de la cookie de sesión y redirige a `/login` si falta (la validez real de la sesión la resuelve siempre el backend en cada petición).
- `src/lib/apiClient.ts` — cliente HTTP centralizado (`fetch` con `credentials: 'include'`), maneja 401 redirigiendo a login.
- `src/components/ui/` — componentes de UI reutilizables y accesibles (botones, tablas, badges, semáforo de estado con texto + color).
- `next.config.js` define `rewrites()` hacia el backend (`/api/*` → `http://backend:8000/api/*`), de modo que todas las llamadas son same-origin: evita configurar CORS y simplifica el uso de cookies `SameSite=Strict`.

## Autenticación: cookie de sesión server-side (no JWT)

Ver [DECISIONS/ADR-0002-auth-session-strategy.md](DECISIONS/ADR-0002-auth-session-strategy.md) para la justificación completa. Resumen: un solo usuario administrador, prioridad en poder revocar/auditar sesiones de forma trivial, y el proxy same-origin de Next.js elimina la ventaja principal de JWT (evitar cookies cross-origin).

## Multi-tenant preparado, no implementado

Aunque hoy existe un único `tenant`, toda tabla de negocio incluye `tenant_id` y toda query de lectura/escritura lo filtra explícitamente. Esto permite evolucionar a multidespacho (Fase 6) sin reescribir el modelo de datos. Ver [DECISIONS/ADR-0003-tenant-model.md](DECISIONS/ADR-0003-tenant-model.md).

## Infraestructura local (Docker Compose)

Tres servicios (`db`, `backend`, `frontend`) en una red interna. Sin acceso directo desde fuera del host a la base de datos. Los healthchecks garantizan que el backend no arranque antes de que Postgres esté listo, y que el frontend dependa del backend. Detalle completo en [INTEGRATIONS.md](INTEGRATIONS.md) y `docker-compose.yml`.

## Por qué SQLAlchemy síncrono (no async)

Ver [DECISIONS/ADR-0005-sync-sqlalchemy-vs-async.md](DECISIONS/ADR-0005-sync-sqlalchemy-vs-async.md). Con un solo usuario y baja concurrencia, el código síncrono es más simple de mantener y depurar; se reevalúa si la automatización supervisada de Fase 4 (Playwright) exige I/O concurrente real.
