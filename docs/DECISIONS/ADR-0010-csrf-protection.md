# ADR-0010: Protección CSRF con double-submit cookie

## Estado
Aceptado

## Contexto
La sesión se identifica por cookie (ver ADR-0002), lo que expone al backend a CSRF si se confiara solo en middleware del frontend. El plan exige explícitamente que la API **no dependa únicamente del middleware del frontend** para protegerse.

## Decisión
Patrón *double-submit cookie*: al iniciar sesión se genera un `csrf_token` aleatorio, se entrega en una cookie **no** `HttpOnly` (`csrf_token`) y se guarda su hash en `sessions.csrf_token_hash`. Toda mutación (`POST`/`PUT`/`PATCH`/`DELETE`) debe incluir el mismo valor en el header `X-CSRF-Token`; el backend lo compara contra el hash almacenado con `hmac.compare_digest` (tiempo constante). El token:
- Rota en cada login.
- Se borra al hacer logout.
- Se invalida automáticamente al revocarse la sesión (vive en la misma fila).
- "Continuar sesión" es `POST /auth/keep-alive` (mutación protegida por CSRF), nunca un `GET`.

## Migración segura de `sessions.csrf_token_hash`
Añadir una columna `NOT NULL UNIQUE` a una tabla con filas preexistentes (sesiones del Bloque 1, creadas antes de que existiera esta columna) requiere un procedimiento en 6 pasos dentro de `0002_auth_hardening.py`:
1. `CREATE EXTENSION IF NOT EXISTS pgcrypto` (defensivo: nunca se asume que `0001` ya la creó).
2. Añadir la columna como `NULLABLE`.
3. Revocar (`revoked_at = now()`) las sesiones preexistentes que no tengan hash.
4. Rellenar esas filas revocadas con un valor placeholder (`encode(gen_random_bytes(32), 'hex')`) — nunca `NULL`, para poder aplicar `NOT NULL` después.
5. Aserción SQL de que ninguna fila **activa** carece de hash.
6. `ALTER COLUMN ... SET NOT NULL` + `UNIQUE`.

El `downgrade()` de esta migración **nunca** ejecuta `DROP EXTENSION pgcrypto`: otros componentes (generación de UUIDs, `gen_random_bytes` en otras tablas) dependen de ella y podría no haber sido esta migración quien la creó originalmente.

## Consecuencias
- Ninguna sesión preexistente queda con acceso mutante sin protección CSRF: se revoca en vez de dejarse "sin hash".
- `test_csrf.py` cubre: mutación sin header rechazada, header incorrecto rechazado, rotación en login, invalidación al revocar, `keep-alive` protegido.
- `test_migration_pgcrypto_bootstrap.py` verifica que una base de datos completamente nueva (sin `pgcrypto` preinstalada) también puede correr `alembic upgrade head` sin fallar.
