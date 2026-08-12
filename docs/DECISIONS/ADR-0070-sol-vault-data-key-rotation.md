# ADR-0070: Bóveda SOL — rotación ordinaria de `credential_dek`, sin rotación de `vault_root_key`

## Estado
Aceptado

## Contexto
Con el tiempo puede ser deseable recifrar todas las credenciales SOL de una bóveda bajo una clave nueva (por
ejemplo, tras sospecha de exposición de la `credential_dek` activa, o como higiene periódica), sin tener que
tocar ningún slot de usuario ni pedir a nadie que reingrese su contraseña personal. Esta decisión documenta el
procedimiento de rotación y, deliberadamente, lo que **no** rota en esta versión.

## Decisión

### Solo `credential_dek` rota; `vault_root_key` nunca rota en operación ordinaria
`rotate_data_key` genera una `credential_dek` nueva, envuelta bajo la **misma** `vault_root_key` de siempre —
ningún slot cambia. Esto es intencional: rotar `vault_root_key` obligaría a re-envolverla bajo el KEK de cada
slot activo simultáneamente (una operación atómica sobre N secretos distintos, cada uno solo conocido por su
dueño), lo que no es realizable sin pedir la contraseña de todos a la vez. Si `vault_root_key` se sospecha
comprometida, el único procedimiento en v1 es manual: deshabilitar la bóveda (`status = 'DISABLED'`, fuera del
alcance de autoservicio de este bloque) y reconstruir el acceso desde cero — deliberadamente **diferido**,
no implementado como endpoint, para evitar ofrecer una operación de alto riesgo sin el diseño y las pruebas
que requeriría.

### Procedimiento atómico de 14 pasos, con advisory lock
`rotate_data_key` (y toda creación/actualización de credencial, vía el mismo `_advisory_lock`) adquiere
`pg_advisory_xact_lock(hashtext(vault_id))` **antes** de leer la data key `ACTIVE` — esto serializa cualquier
rotación contra cualquier alta/actualización de credencial concurrente de la misma bóveda, evitando que una
credencial se cifre bajo una versión de `credential_dek` que la rotación está a punto de retirar.

Orden exacto (fe de erratas del spike, ya corregida desde el diseño inicial de este bloque):
1. Adquirir el advisory lock de la bóveda.
2. Bloquear la fila de `sol_vaults` (`SELECT ... FOR UPDATE`).
3. Bloquear la fila de `sol_vault_data_keys` `ACTIVE` actual (`FOR UPDATE`).
4. Verificar `expected_version` contra `sol_vaults.version` (concurrencia optimista).
5. Desenvolver la `credential_dek` **antigua** usando la `vault_root_key` ya desenvuelta del actor.
6. Generar la `credential_dek` **nueva** (32 bytes aleatorios).
7. Listar todas las credenciales de la bóveda a recifrar.
8–9. (diferidos hasta el paso 14): descifrar cada campo con la clave antigua, listo para recifrar.
10. Marcar la fila de data key antigua como `RETIRED` y hacer `flush()` — libera el índice único parcial
    `ACTIVE`, **antes** de insertar la fila nueva.
11. (mismo flush que el paso 10.)
12. Insertar la fila de data key nueva (`ACTIVE`, versión `N+1`) y hacer `flush()` — la fila debe existir en
    la base de datos **antes** de reasignar cualquier credencial a esa versión, porque la FK compuesta
    `(tenant_id, vault_id, data_key_version) -> sol_vault_data_keys(tenant_id, vault_id, data_key_version)`
    la exige.
13. Recorrer las credenciales: descifrar cada campo con la clave antigua, cifrarlo de nuevo con la clave
    nueva (AAD reconstruido con la versión nueva), y reasignar `data_key_version`.
14. Incrementar `sol_vaults.version`. El router hace `commit()` — este servicio nunca comitea (ADR-0037).

El orden **paso 10 antes del paso 12** es deliberado: insertar la fila nueva antes de retirar la antigua
violaría el índice único parcial `ACTIVE` (dos filas `ACTIVE` a la vez); retirar la antigua después de
reasignar credenciales dejaría una ventana donde una credencial referencia una versión que la FK aún no
respalda como `ACTIVE`. El orden real evita ambos.

### Verificado con concurrencia real de dos conexiones
Una prueba dedicada (`test_sol_vault_rotation_concurrency.py`) fuerza una excepción a mitad de la rotación
(tras recifrar un campo de la primera credencial) dentro de una transacción **deliberadamente no comiteada**,
y usa una segunda conexión PostgreSQL real para verificar: (a) ninguna fila a medio recifrar es visible fuera
de esa transacción; (b) el advisory lock bloquea genuinamente a la segunda conexión; (c) tras el `rollback()`
de la primera, el estado queda completamente intacto — `version` sin incrementar, una única data key `ACTIVE`
en la versión original, las 3 credenciales de prueba descifrando correctamente con la clave original.

## Alternativas consideradas
- **Rotar `vault_root_key` junto con `credential_dek`**: rechazada para v1 — requeriría coordinar la
  contraseña de cada slot activo simultáneamente; queda fuera de alcance, con el procedimiento manual de
  bóveda deshabilitada como única vía para el caso de compromiso.
- **Insertar la data key nueva antes de retirar la antigua**: rechazada — viola el índice único parcial
  `ACTIVE` de `sol_vault_data_keys`.
- **Reasignar credenciales antes de insertar la fila de data key nueva**: rechazada — violaría la FK
  compuesta `(tenant_id, vault_id, data_key_version)`.

## Consecuencias
- Cualquier operación futura de alta/actualización de credencial debe seguir adquiriendo el mismo advisory
  lock antes de leer la data key activa — documentado también en ADR-0068 y aplicado en
  `client_sol_credential_service.py`.
- Un procedimiento de rotación de `vault_root_key` (para el caso de compromiso real) queda como trabajo futuro
  explícitamente no diseñado aquí — no debe inferirse de este ADR ningún compromiso de que exista un endpoint
  para ello en esta versión.
