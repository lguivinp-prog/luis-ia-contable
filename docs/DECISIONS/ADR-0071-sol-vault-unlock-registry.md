# ADR-0071: Bóveda SOL — `VaultUnlockRegistry` en memoria, límites honestos de zeroization en Python

## Estado
Aceptado

## Contexto
Una vez desenvuelta, `vault_root_key` debe quedar disponible para operaciones subsecuentes (crear/actualizar
una credencial, rotar `credential_dek`, aceptar una invitación) sin que el usuario reingrese su contraseña en
cada llamada, pero **sin que esa clave sobreviva jamás fuera de la memoria de un único proceso**. Este ADR
documenta `VaultUnlockRegistry` (`app/core/sol_vault_registry.py`) y los límites reales de "borrar" una clave
en un lenguaje con recolector de basura.

## Decisión

### Registro puramente en memoria de proceso, indexado por sesión real
`VaultUnlockRegistry` nunca persiste en PostgreSQL, Redis ni ningún archivo. Índice:
`(tenant_id, vault_id, user_id, app_session_id) -> handle`, donde `app_session_id` es siempre el `id` real de
una fila de `sessions` (`app.models.session.Session`) — nunca un identificador inventado por este módulo. Esto
permite que dos sesiones de navegador distintas del mismo usuario coexistan con autorizaciones independientes
(por ejemplo, la sesión del escritorio queda desbloqueada mientras la del portátil sigue bloqueada).

### `resolve()` verifica en vivo, en cada uso — nunca confía en el estado emitido originalmente
Cada operación que necesita `vault_root_key` llama a `resolve()`, que revalida, contra la base de datos real,
en cada invocación: que la fila de `sessions` con ese `id` exista, que `revoked_at IS NULL`, que no haya
vencido, que `tenant_id`/`user_id` coincidan con los esperados, que la propia autorización de desbloqueo no
haya vencido ni sido revocada, que la operación solicitada esté en `allowed_operations`, y que — cuando la
autorización tiene alcance limitado a clientes específicos — el `client_id` de la operación esté autorizado.
Cualquier fallo de sesión o expiración purga la entrada inmediatamente, incluso si el fallo fue por otra
razón (por ejemplo, la sesión subyacente fue revocada por un logout en otra pestaña).

### `LOCKED` es un estado derivado, nunca persistido
No existe una columna "bloqueada" en ninguna tabla. Que una bóveda esté bloqueada para un usuario/sesión dado
es simplemente la ausencia de una entrada en el índice del registro para esa combinación exacta — lo mismo
que documenta ADR-0068 para `NOT_INITIALIZED`.

### Nunca se persiste el handle, nunca se devuelve al frontend
El `handle` (`os.urandom(32).hex()`) es una clave de memoria interna del proceso backend — nunca aparece en
ninguna respuesta HTTP, log, ni fila de auditoría. `GET /sol-vault/status` deriva `unlocked: bool` internamente
comparando si existe una entrada, pero nunca expone el handle en sí.

### Reiniciar el backend bloquea automáticamente todas las bóvedas
No existe ningún mecanismo de persistencia que sobreviva un reinicio del proceso — el registro nace vacío
(`reset()` limpia todo, y de hecho el propio arranque del proceso ya nace vacío por construcción del
singleton). No se trata de una limitación a resolver: es la garantía deliberada de que un reinicio equivale a
un bloqueo global, sin ninguna ventana en la que una clave "sobreviva" el reinicio en algún caché externo.

### Límites honestos de zeroization en Python (documentados, no maquillados)
`vault_root_key` se guarda dentro de `UnlockEntry` como `bytearray` (mutable), no como `bytes` (inmutable) —
esto permite sobrescribir su contenido en el lugar (`zeroize()`, que rellena el buffer con ceros) al purgar
una entrada, ya sea por `lock()`, por expiración de TTL detectada en `resolve()`, o por `reset()`. **Esto no
es una garantía criptográfica de borrado físico de memoria**: CPython puede haber copiado los bytes
originales (por ejemplo, al construir el `bytearray` desde el resultado de `unwrap_vault_root_key`, o durante
cualquier operación intermedia de `cryptography`/`argon2`) a otras páginas de memoria antes de que
`zeroize()` sobrescriba el buffer conocido; el recolector de basura no garantiza cuándo, ni si, esas copias
transitorias se sobrescriben. La mitigación real no es "borrado perfecto", sino minimizar la superficie: la
clave vive el menor tiempo posible, en el menor número de lugares posible (un único `UnlockEntry` por
sesión, nunca duplicada en una estructura paralela), y se purga proactivamente en cuanto deja de ser
necesaria — no se afirma en ningún endpoint, log ni documento que la memoria quede "borrada de forma segura".

### Registro compartido, no reinstanciado fuera de pruebas
`vault_unlock_registry` es la única instancia de proceso que debe usar todo el servicio de la bóveda SOL en
producción — instanciar un `VaultUnlockRegistry()` nuevo (como sí hacen las pruebas, deliberadamente, para
aislar cada caso) fuera de pruebas fragmentaría las autorizaciones entre instancias que no se ven entre sí.

## Alternativas consideradas
- **Persistir el registro en Redis para sobrevivir reinicios**: rechazada — reintroduciría exactamente el
  riesgo que este diseño evita (una clave desenvuelta fuera de la memoria de un único proceso controlado); y
  el single-worker gate (ADR-0068) ya asume que no hay necesidad de compartir estado entre procesos.
- **Usar `bytes` en vez de `bytearray` para `vault_root_key`**: rechazada — `bytes` es inmutable en Python, lo
  que haría imposible siquiera el intento de sobrescritura que ofrece `zeroize()`.
- **Afirmar que `zeroize()` borra la clave de forma segura**: rechazada explícitamente — sería una afirmación
  falsa dado el modelo de memoria de CPython; se documenta el límite real en su lugar.

## Consecuencias
- Cualquier endpoint nuevo que necesite `vault_root_key` desenvuelta debe pasar por `registry.resolve()` con
  el `operation` correcto — nunca debe cachear la clave por su cuenta ni construir un segundo registro.
- El manual de usuario (`docs/USER_MANUAL.md`) debe advertir explícitamente que reiniciar la aplicación
  bloquea todas las bóvedas de todos los usuarios, sin excepción, y que esto es intencional.
- Pruebas de concurrencia (`test_sol_vault_registry.py`, `test_sol_vault_rotation_concurrency.py`) verifican
  el comportamiento de `resolve()` contra sesiones reales, no contra mocks del modelo `Session`.
