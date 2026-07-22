# ADR-0009: Bloqueo por intentos fallidos de login (versión definitiva)

## Estado
Aceptado (revisado 6 veces durante la ronda de aprobación del plan; esta es la versión final implementada)

## Contexto
Se requiere mitigar ataques de fuerza bruta/credential stuffing contra `/auth/login`, sin (a) revelar nunca si una cuenta existe, (b) bloquear permanentemente una cuenta, ni (c) introducir asimetrías de tiempo o de auditoría entre "cuenta inexistente" y "cuenta existente con contraseña incorrecta" que un atacante pudiera explotar para enumerar usuarios.

## Decisión

### Conteo de intentos
`login_attempts.tenant_id` es **NULLABLE** y solo se rellena en el intento **exitoso**. El conteo de fallos es "fallos consecutivos desde el último `success=true`" — las filas nunca se borran (historial completo conservado). `normalize_login_identifier()` (`app/core/identifier.py`) normaliza NFC + minúsculas + `strip` + rechazo de caracteres de control antes de calcular el conteo, evitando que variaciones de mayúsculas/espacios sirvan para eludir el bloqueo.

### Mitigación de enumeración por tiempo
`get_dummy_password_hash()` (`app/core/security.py`, `lru_cache`, precalentado en el arranque de `main.py`) se usa para ejecutar `verify_password` con el mismo costo computacional cuando el identificador no corresponde a ninguna cuenta o la cuenta está inactiva.

### Solicitudes durante bloqueo activo
Se responde 429 + `Retry-After`; se registra un `security_events` (`LOGIN_ATTEMPT_WHILE_LOCKED`, `tenant_id` NULL); **no** se inserta fila en `login_attempts` ni se modifica `locked_until` — cien solicitudes durante el bloqueo no lo extienden ni lo reinician.

### Regla de simetría definitiva (la corrección más importante del proceso de aprobación)
**Ningún fallo de login, exista o no la cuenta, escribe en `audit_logs`.** Ambos casos ejecutan exactamente la misma huella de persistencia: una fila en `login_attempts` (`tenant_id=NULL`) + una fila en `security_events` (`LOGIN_FAILURE_ATTEMPT`). La única diferencia es el **valor interno** de `security_events.tenant_id`/`user_id` (rellenado solo si se identificó una cuenta real) — nunca expuesto en la respuesta HTTP, que es idéntica en código, mensaje y forma en ambos casos. `audit_logs` queda reservado para acciones dentro de una sesión autenticada (login exitoso, logout, cambio de contraseña, revocación de sesiones, acciones de negocio). Si un fallo cruza el umbral de bloqueo se registra además `LOGIN_LOCKOUT_TRIGGERED`, que depende solo del historial del identificador y no reintroduce asimetría.

### Backoff progresivo
`duration = min(base_minutes * 2**cycle, max_minutes)`, con `cycle = (consecutive_failures - max_attempts) // max_attempts`. Configurable vía variables de entorno no secretas: `LOGIN_MAX_ATTEMPTS`, `LOGIN_ATTEMPT_WINDOW_MINUTES`, `LOGIN_LOCKOUT_DURATION_MINUTES`, `LOGIN_LOCKOUT_MAX_MINUTES`.

### Desbloqueo administrativo
`backend/app/cli/unlock_login.py` + `scripts/unlock-login.ps1`: normaliza el identificador igual que el login, nunca borra el historial de `login_attempts`, registra `LOGIN_UNLOCKED_MANUALLY` en `security_events`.

## Consecuencias
- `test_login_lockout.py` y `test_login_timing_mitigation.py` verifican explícitamente que ambos casos (cuenta existente/inexistente) producen el mismo código HTTP, mensaje, y el mismo conjunto de escrituras persistentes.
- Se eliminó `tenant_repository.get_default()` (y el archivo `tenant_repository.py` completo) al retirarse el fallback de "tenant por defecto" del flujo de login, tras confirmar que no quedaban otros usos.
