# ADR-0068: Bóveda SOL — jerarquía criptográfica, alcance de Fase 5/Bloque 5.1 y arquitectura general

## Estado
Aceptado

## Contexto
Fase 5, Bloque 5.1 introduce el almacenamiento cifrado de credenciales SOL (usuario/contraseña del portal
SUNAT) por cliente, protegidas detrás de una contraseña personal por usuario del despacho ("bóveda"). La
arquitectura fue validada experimentalmente en el spike `spike/phase5-sol-vault-crypto` (commit `2096a9b`,
tag `spike-phase5-sol-vault-crypto-v1`) antes de escribir una sola línea de código productivo. **El spike es
evidencia técnica histórica, no una dependencia en tiempo de ejecución**: ningún módulo productivo importa
nada de esa rama; los hallazgos del spike (fe de erratas de orden de rotación, backoff de fuerza bruta,
semántica del registro en memoria) se reimplementaron aquí desde cero según las convenciones reales del
repositorio.

Alcance explícitamente **excluido** de este bloque (reservado para fases posteriores, no diseñado aquí):
Playwright, automatización de acceso a SUNAT, Buzón SOL, sesiones de acceso SOL reales, IPC con un agente de
Windows. Ningún código de este bloque abre un navegador, llama a un endpoint de SUNAT, ni usa una credencial
SOL real — las credenciales solo se cifran, se guardan y se devuelven como metadatos no sensibles.

## Decisión

### Jerarquía criptográfica de tres niveles
```
contraseña personal del usuario  --Argon2id-->  KEK del slot
KEK del slot                     --AES-256-GCM-->  vault_root_key (envuelta, una copia por slot)
vault_root_key                   --AES-256-GCM-->  credential_dek (envuelta, versionada)
credential_dek                   --AES-256-GCM-->  sol_username / sol_password (por credencial, por campo)
```
`vault_root_key` (32 bytes aleatorios) nunca cifra directamente una credencial — solo envuelve
`credential_dek`. Cada slot (`USER` o `RECOVERY`) envuelve su **propia copia** de la misma `vault_root_key`
bajo un KEK derivado de su propio secreto (contraseña personal o código de recuperación) y su propia sal —
así, revocar o rotar un slot nunca afecta a los demás, y ninguna clave se comparte cifrada bajo el secreto de
otro actor. `credential_dek` es la única clave que cifra contenido real de credenciales, y es la única que
rota en operación ordinaria (ver ADR-0070).

### AES-256-GCM con sobre versionado y AAD obligatorio en cada capa
Mismo patrón que el ya productivo `app/core/integration_crypto.py`: `format_version` (1 byte) + nonce de 12
bytes (nuevo en cada cifrado, nunca reusado) + ciphertext + tag de autenticación GCM. El AAD se construye
mediante funciones deterministas y centralizadas en `app/core/sol_vault_crypto.py`
(`aad_wrapped_vault_root_key`, `aad_wrapped_data_key`, `aad_credential_field`), incluyendo siempre
`tenant_id` y el identificador de fila — mover un ciphertext a otra fila/tabla/tenant/usuario falla la
autenticación GCM de inmediato, sin necesidad de un chequeo aplicativo adicional. No se implementa ninguna
primitiva criptográfica propia: Argon2id vía `argon2.low_level.hash_secret_raw` (nunca `PasswordHasher`),
AES-256-GCM vía `cryptography`.

### Parámetros Argon2id productivos (v1, sin reducción automática)
```
memory_cost_kib = 393216   (384 MiB)
time_cost       = 2
parallelism     = 1
hash_len        = 32
kdf_version     = 1
```
Encapsulados en `production_argon2_params()`; cada slot persiste sus propios `kdf_time_cost`,
`kdf_memory_cost_kib`, `kdf_parallelism`, `kdf_hash_len`, `kdf_version` — un cambio futuro de parámetros no
invalida slots existentes, cada uno se sigue derivando con los parámetros con los que fue creado. **Nunca se
usa `m512` como valor por defecto y nunca se disminuyen los parámetros automáticamente por presión de
memoria** — si el proceso no tiene 384 MiB libres, la derivación falla explícitamente en vez de degradarse
silenciosamente a un parámetro más débil.

### Una sola derivación Argon2id simultánea por proceso (`Argon2ConcurrencyGate`)
`app/core/sol_vault_argon2_gate.py` expone un singleton `argon2_gate` construido sobre
`threading.Semaphore(max_concurrent=1)`. Toda derivación de KEK (`initialize`, `unlock`, `recover`,
`confirm_recovery`, `change_password`, `accept_invitation`, rotación de recovery) pasa por
`argon2_gate.derive_kek(...)`, con espera acotada (`timeout_seconds`, por defecto 5s) y liberación garantizada
en `finally`. Esto evita que dos derivaciones de 384 MiB corran a la vez y agoten la memoria del proceso — el
costo es una cola corta de espera bajo uso concurrente real, aceptable porque Argon2id con estos parámetros
tarda cientos de milisegundos, no segundos.

### Backend de un único worker, obligatorio en v1
`enforce_single_worker_gate(settings.backend_workers)` se llama a **nivel de módulo** en `app/main.py`, antes
de construir `FastAPI()` — un valor de `BACKEND_WORKERS` distinto de 1 hace fallar el arranque completo del
proceso, antes de aceptar ninguna petición, con un mensaje claro sin secretos. La razón estructural: el
`VaultUnlockRegistry` (ADR-0071) es memoria de un único proceso; con más de un worker, cada proceso tendría su
propio registro desincronizado, y un usuario podría parecer desbloqueado en un worker y bloqueado en otro. No
se introduce Redis ni ningún broker de claves compartido — la restricción de un solo proceso es la decisión de
diseño en sí, no un límite temporal a resolver con más infraestructura. El valor por defecto (`1`) coincide
con el `Dockerfile` real (`uvicorn app.main:app ... ` sin `--workers`).

### Estados derivados, nunca persistidos como valor propio
- `NOT_INITIALIZED` no es un valor de columna: se deriva de la **ausencia** de una fila en `sol_vaults` para
  ese `tenant_id` (`get_vault_status` devuelve `None`, el router traduce eso a `NOT_INITIALIZED` en la
  respuesta pública).
- `LOCKED` tampoco se persiste: se deriva de la ausencia de una entrada viva en `VaultUnlockRegistry` para
  `(tenant_id, vault_id, user_id, app_session_id)` (ver ADR-0071).

`sol_vaults.status` solo persiste `ACTIVE`, `RECOVERY_REQUIRED`, `DISABLED` — los tres estados que sí son
hechos de la base de datos, no inferencias de la capa de aplicación.

### Credenciales SOL: siempre `NEEDS_VALIDATION`, tipos con justificación explícita
Toda alta o actualización de `client_sol_credentials` aterriza en `status = 'NEEDS_VALIDATION'` — el cambio a
`ACTIVE` requeriría una validación real contra SUNAT (acceso supervisado) que este bloque **no** implementa;
sin esa validación, ninguna credencial puede auto-declararse funcional. `credential_kind` tiene 3 valores
reales (`SECONDARY`, `PRIMARY_OWN_RUC`, `PRIMARY_EXCEPTIONAL`) — deliberadamente **no existe** un valor
`PRIMARY` genérico. `SECONDARY` (un usuario SOL secundario, propio del despacho, con permisos delegados) es la
opción preferente para operar sobre clientes: nunca requiere manejar la clave SOL principal del contribuyente.
`PRIMARY_OWN_RUC` cubre el caso de una persona natural que opera con su propio RUC. `PRIMARY_EXCEPTIONAL` es
la única vía para usar la clave SOL principal de un tercero, y por eso todo alta/actualización exige
`authorization_basis` (no vacío) y admite `authorization_reference`/`authorized_scope_description` — la
autorización es un dato de negocio explícito, nunca implícito por el tipo de credencial.

### Permisos: el asistente no obtiene ningún permiso de este bloque
Los 9 permisos nuevos (`sol_vault:initialize`, `sol_vault:manage_users`, `sol_vault:recover`,
`sol_vault:rotate`, `sol_vault:unlock`, `sol_credential:read`, `sol_credential:create`,
`sol_credential:update`, `sol_credential:disable`) se reparten así: los 4 primeros son exclusivos de `admin`;
los 5 restantes los tienen `admin` y `contador`. `asistente` no recibe ninguno — ni siquiera lectura de
estado de credencial. Totales reales tras este bloque: `admin=51`, `contador=44`, `asistente=22`.

## Alternativas consideradas
- **Cifrar credenciales directamente bajo `vault_root_key`**: rechazada — impediría rotar la clave de cifrado
  de credenciales sin también re-envolver cada slot; la capa `credential_dek` intermedia (ADR-0070) desacopla
  ambas operaciones.
- **Múltiples workers con un registro de desbloqueo compartido (Redis)**: rechazada para v1 — introduce una
  dependencia de infraestructura nueva y un vector de persistencia de claves fuera de este proceso, contrario
  al principio de que ninguna clave desenvuelta sobrevive fuera de la memoria de un único proceso (ADR-0071).
- **Reducir automáticamente los parámetros de Argon2id bajo presión de memoria**: rechazada — degradaría la
  seguridad de forma silenciosa e impredecible; se prefiere una falla explícita.

## Consecuencias
- Cualquier bloque futuro que implemente validación real contra SUNAT (fuera de alcance aquí) debe respetar
  que `NEEDS_VALIDATION` es el único estado alcanzable por este bloque — la transición a `ACTIVE` queda
  reservada a ese trabajo futuro.
- Reinstalar el backend con más de un worker rompe el arranque de inmediato — documentado en
  `docs/WINDOWS_INSTALLATION.md` y `docs/USER_MANUAL.md`.
- ADR-0069, ADR-0070 y ADR-0071 detallan recuperación/invitaciones, rotación de `credential_dek` y el
  `VaultUnlockRegistry` respectivamente, todos construidos sobre la jerarquía y los parámetros fijados aquí.
