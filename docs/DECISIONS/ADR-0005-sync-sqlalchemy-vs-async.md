# ADR-0005: SQLAlchemy síncrono en lugar de async

## Estado
Aceptado

## Contexto
FastAPI soporta tanto vistas síncronas como asíncronas, y SQLAlchemy 2.0 soporta ambos modos de uso con drivers distintos (`psycopg` síncrono vs `asyncpg`/`psycopg` async).

## Decisión
Se usa SQLAlchemy 2.0 en modo síncrono con el driver `psycopg` (versión 3), tanto en la aplicación como en las migraciones de Alembic.

## Justificación
- Un solo usuario administrador, baja concurrencia esperada en esta fase.
- El código síncrono es más simple de escribir, depurar y testear (sin necesidad de `pytest-asyncio`, fixtures async, etc.).
- Evita la complejidad de Alembic en modo async, que requiere configuración adicional sin beneficio claro aquí.

## Revisión futura
Esta decisión se revisará en Fase 4, cuando la automatización supervisada con Playwright y posibles integraciones concurrentes (Google, Gmail) puedan beneficiarse de I/O asíncrono. Si se migra, se hará de forma incremental y documentada en un nuevo ADR.
