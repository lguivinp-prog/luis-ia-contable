# Block 5.2 — Borrador de modelo de datos (SIN migración)

**Domain A — Architecture & Contract Freeze**, congelado en **Domain
A.1 — Persistence & Concurrency Contract Freeze**. Borrador conceptual
para orientar la futura migración `0018` — **no se crea SQL ni
modelos SQLAlchemy en esta ronda**. Sigue el patrón productivo ya
establecido: `UUIDPrimaryKeyMixin`, FKs compuestas tenant-safe (ADR-0003/
ADR-0024), enums `str` con `CheckConstraint` en columna `String` (no
`Enum` de SQLAlchemy — patrón `Declaration`/ADR-0018/ADR-0022),
`version` para bloqueo optimista (mismo patrón que `Declaration`),
`idempotency_key: uuid.UUID` como columna en la propia entidad
(mismo patrón que `TaxPayment.idempotency_key` — ver
`BLOCK5_2_API_CONTRACTS.md` sección 7).

**Domain A.1 congela definitivamente**: las 4 tablas de la migración
`0018` (sección 8), todos los enums persistidos (sección 7), todas las
columnas exactas de cada tabla (secciones 1-4), el plan de índices
(sección 9), y la política de borrado/retención (sección 10).

**Domain A.2 — Final DDL Security & Compatibility Gate** cierra 4
huecos que Domain A.1 había dejado como "verificar en Domain B" o
insuficientemente especificados, **todos verificados contra código
productivo real de este mismo repositorio, no asumidos**: (1) el
soporte real de la FK compuesta de propiedad de credencial (sección 6
— **confirmado existente**, sin cambio a `client_sol_credentials`);
(2)-(3) el mecanismo y la retención del verificador de pairing (sección
4 — renombrado `pairing_code_hash` → `pairing_code_verifier`, limpiado
en terminal); (4) la invariante transaccional de "como mucho una
entrega de credencial por sesión, nunca por job" (sección 3 — nuevo
índice único parcial). Ver sección 11 para el registro de constraints
definitivo.

**Domain A.3 — Pairing Verifier Cryptographic Freeze corrige el
mecanismo exacto del Gate 2 de Domain A.2.** Domain A.2 afirmó
`PAIRING_CODE_KEYED_VERIFIER=YES` asumiendo que `hash_password()`/
`verify_password()` (Argon2id vía `argon2.PasswordHasher()`)
constituían un *keyed verifier*. **Verificado en Domain A.3, contra el
código real de `security.py`: es falso** —
`PasswordHasher()` se instancia sin parámetro de secreto/pepper; es
Argon2id **salted** (salt aleatorio por hash, embebido en el propio
hash) pero **no keyed** — no incorpora ningún secreto servidor-side
ajeno a la propia base de datos. Para un código de 6 dígitos (10⁶
combinaciones), eso deja el verificador enumerable offline ante una
fuga de la fila (el costo Argon2id encarece, pero no impide, agotar
10⁶ combinaciones). Sección 4 reemplaza el mecanismo por un
**HMAC-SHA-256 con clave servidor-side fuera de PostgreSQL** —
verdadero *keyed verifier*, con separación de dominio explícita. Ver
sección "Mecanismo del verificador" para el contrato exacto. Domain B
debe poder implementar `0018` **sin tomar ninguna decisión
arquitectónica ni criptográfica nueva**.

## 1. `sol_access_sessions`

| Campo | Tipo | Nullable | Default | Notas |
|---|---|---|---|---|
| `id` | UUID (PK) | No | — | `UUIDPrimaryKeyMixin` |
| `tenant_id` | UUID | No | — | FK `tenants.id` |
| `client_id` | UUID | No | — | FK compuesta `(client_id, tenant_id) → (clients.id, clients.tenant_id)` |
| `credential_id` | UUID | No | — | FK compuesta de **3 columnas** `(credential_id, client_id, tenant_id) → (client_sol_credentials.id, client_sol_credentials.client_id, client_sol_credentials.tenant_id)` — ver sección 6 "Propiedad de la credencial" |
| `requested_by_user_id` | UUID | No | — | FK compuesta `(requested_by_user_id, tenant_id) → (users.id, users.tenant_id)` — única columna de "quién" en la tabla, `created_by` **descartado** por redundancia (Domain A.1, resuelve la decisión pendiente #3 de Domain A) |
| `assigned_agent_id` | UUID | **Sí** | NULL | FK compuesta `(assigned_agent_id, tenant_id) → (sol_agents.id, sol_agents.tenant_id)` — nulo hasta que un job es creado y reclamado (sección 6 "Agente asignado") |
| `scope` | `String` + `CheckConstraint` | No | — | `SolAccessScope` — hoy solo `'AUTHENTICATE_TO_LANDING_ONLY'` |
| `status` | `String` + `CheckConstraint` | No | `'CREATED'` | `SolAccessSessionStatus` — 9 valores, `BLOCK5_2_STATE_MACHINE.md` sección 1 |
| `operation_result` | `String` + `CheckConstraint` | **Sí** | NULL | `SolAccessOperationResult` — valor único hoy (`'LANDING_REACHED'`), ver `BLOCK5_2_STATE_MACHINE.md` sección 6 (renombrado) |
| `terminal_reason_code` | `String` + `CheckConstraint` | **Sí** | NULL | `SolAccessTerminalReasonCode` — **renombrado desde `failure_code`** (Domain A.1 sección 38); poblado en `FAILED`/`PARTIAL_FAILURE`/`CANCELLED`/`EXPIRED`, nulo solo en `SUCCEEDED` |
| `idempotency_key` | UUID | No | — | **Nueva columna, Domain A.1** — mismo patrón que `TaxPayment.idempotency_key`; ver `BLOCK5_2_API_CONTRACTS.md` sección 7 |
| `created_at` | `DateTime(timezone=True)` | No | `now()` | |
| `agent_assigned_at` | `DateTime(timezone=True)` | **Sí** | NULL | |
| `started_at` | `DateTime(timezone=True)` | **Sí** | NULL | Primer `IN_PROGRESS` |
| `waiting_for_user_at` | `DateTime(timezone=True)` | **Sí** | NULL | Última entrada a `WAITING_FOR_USER` |
| `completed_at` | `DateTime(timezone=True)` | **Sí** | NULL | Al llegar a cualquier estado terminal |
| `expires_at` | `DateTime(timezone=True)` | No | — | `session_ttl` — ver `BLOCK5_2_STATE_MACHINE.md` sección 7; **tiempo de BD** (`now()` del servidor), nunca reloj del agente (sección 5 abajo) |
| `cancelled_at` | `DateTime(timezone=True)` | **Sí** | NULL | |
| `version` | `Integer` | No | `1` | Bloqueo optimista |
| `updated_at` | `DateTime(timezone=True)` | No | `now()`, `onupdate=now()` | |

**Recuento final: 20 columnas** (resuelve la pregunta abierta de
Domain A — el número correcto no es 17/18/19 sino 20, tras retirar
`created_by` y añadir `idempotency_key`).

**Constraints**:
- `CheckConstraint` para `scope`, `status`, `operation_result`,
  `terminal_reason_code` (valores cerrados).
- `ForeignKeyConstraint` compuestas para `client_id`, `credential_id`
  (3 columnas), `requested_by_user_id`, `assigned_agent_id`.
- `UNIQUE(tenant_id, idempotency_key)`.
- **`UNIQUE(tenant_id, client_id, credential_id) WHERE status NOT IN
  (estados terminales)`** — como mucho una sesión no-terminal por
  credencial (sección 6 "Duplicados de creación").
- Índice `(tenant_id, status)`.
- Índice `(expires_at)` — para el barrido de expiración.

## 2. `sol_agents`

| Campo | Tipo | Nullable | Default | Notas |
|---|---|---|---|---|
| `id` | UUID (PK) | No | — | |
| `tenant_id` | UUID | No | — | FK `tenants.id` |
| `display_name` | `String` | No | — | Asignado en el pairing |
| `certificate_fingerprint` | `String` | No | — | **`UNIQUE` global** (no solo por tenant) — es una identidad criptográfica, dos agentes nunca comparten certificado (Domain A.1 sección 8/35, confirmado) |
| `status` | `String` + `CheckConstraint` | No | `'ACTIVE'` | `SolAgentStatus` — exactamente `'ACTIVE'` / `'REVOKED'`, sin `OFFLINE` persistido |
| `paired_at` | `DateTime(timezone=True)` | No | — | |
| `last_seen_at` | `DateTime(timezone=True)` | **Sí** | NULL | Actualizado por heartbeat (sección 5) |
| `revoked_at` | `DateTime(timezone=True)` | **Sí** | NULL | |
| `version` | `Integer` | No | `1` | |
| `created_at` | `DateTime(timezone=True)` | No | `now()` | |
| `updated_at` | `DateTime(timezone=True)` | No | `now()` | |

**Constraints**:
- `UNIQUE(certificate_fingerprint)` — global.
- `CheckConstraint("(status != 'REVOKED') OR (revoked_at IS NOT NULL)")`
  — `revoked_at` es **obligatorio** cuando `status='REVOKED'` (Domain
  A.1 sección 36).
- Índice `(tenant_id, status)`.
- Índice `(last_seen_at)` — soporta el cómputo de `ONLINE`/`OFFLINE`
  derivado (nunca persistido, ver sección 5).

**Identidad de certificado — un único certificado activo por agente**
(Domain A.1 sección 9): `certificate_fingerprint` vive directamente en
`sol_agents`, **no** se diseña una tabla de certificados/PKI completa
en 5.2. Limitación documentada: rotar el certificado de un agente ya
emparejado requiere revocar y re-parear (un nuevo `sol_agent_pairings`
completo) — no existe todavía un flujo de "rotación sin re-pairing".
Esto es una limitación aceptada para la primera versión, no un olvido.

## 3. `sol_agent_jobs`

| Campo | Tipo | Nullable | Default | Notas |
|---|---|---|---|---|
| `id` | UUID (PK) | No | — | |
| `tenant_id` | UUID | No | — | FK `tenants.id` |
| `session_id` | UUID | No | — | FK compuesta `(session_id, tenant_id) → (sol_access_sessions.id, sol_access_sessions.tenant_id)` |
| `agent_id` | UUID | No | — | FK compuesta `(agent_id, tenant_id) → (sol_agents.id, sol_agents.tenant_id)` — **ya NO nulo** (Domain A.1 sección 34): el job se crea siempre dirigido a un agente específico (el ya asignado a la sesión), nunca como cola competida por agentes distintos — "claim" es la confirmación de ESE agente, no una carrera multi-agente |
| `status` | `String` + `CheckConstraint` | No | `'PENDING'` | `SolAgentJobStatus` — **enum propio, no reutiliza `SolAccessSessionStatus`**: `'PENDING'` / `'CLAIMED'` / `'COMPLETED'` / `'FAILED'` / `'CANCELLED'` / `'EXPIRED'` (renombrado de `ELIGIBLE` a `PENDING`, alineado con el encargo) |
| `job_token_hash` | `String` | No | — | Hash del token de un solo uso — **`UNIQUE` global** (nunca el token en claro) |
| `credential_delivered_at` | `DateTime(timezone=True)` | **Sí** | NULL | **Reemplaza `credential_delivery_attempt_count`** (Domain A.1 sección 50) — gate atómico `IS NULL` antes de entregar; una vez `NOT NULL`, nunca se re-entrega a este job |
| `claimed_at` | `DateTime(timezone=True)` | **Sí** | NULL | |
| `expires_at` | `DateTime(timezone=True)` | No | — | `job_ttl` ≤ `session.expires_at`, forzado a nivel de aplicación al crear el job (Postgres no soporta `CHECK` entre tablas — sección 5) |
| `completed_at` | `DateTime(timezone=True)` | **Sí** | NULL | |
| `cancelled_at` | `DateTime(timezone=True)` | **Sí** | NULL | |
| `version` | `Integer` | No | `1` | Crítico para la carrera de `claim` (`BLOCK5_2_API_CONTRACTS.md` sección 6) |
| `created_at` | `DateTime(timezone=True)` | No | `now()` | |
| `updated_at` | `DateTime(timezone=True)` | No | `now()` | |

**Constraints**:
- `UNIQUE(job_token_hash)` — global; el hash **permanece** tras el uso
  (para detección de replay/auditoría), nunca se borra.
- `UNIQUE(session_id) WHERE status IN ('PENDING', 'CLAIMED')` — como
  mucho un job activo por sesión (Domain A.1 sección 21).
- `UNIQUE(agent_id) WHERE status IN ('PENDING', 'CLAIMED')` — como
  mucho un job activo por agente (Domain A.1 sección 34).
- **`UNIQUE(session_id) WHERE credential_delivered_at IS NOT NULL`**
  — **nueva en Domain A.2** (Gate 3/4 del encargo de Domain A.2, sección
  "No-redelivery por sesión" abajo): garantiza a nivel de base de datos
  que como mucho **un job de toda la sesión**, nunca dos, llega a tener
  `credential_delivered_at` poblado — sin importar cuántos jobs
  históricos tenga la sesión ni si dos intentan entregar
  simultáneamente.
- Índice `(session_id, status)`, `(agent_id, status)`, `(expires_at)`.

**Multiplicidad job/sesión** (Domain A.1 secciones 17 y 20 — congelado
junto): una sesión puede tener **múltiples jobs históricos**
(reintentos), pero:

1. Como mucho **un job activo** (`PENDING`/`CLAIMED`) a la vez —
   forzado por el índice único parcial de arriba.
2. **En cuanto CUALQUIER job de la sesión tiene
   `credential_delivered_at NOT NULL`, esa sesión no puede crear más
   jobs** — el secreto ya fue expuesto a un proceso de agente; si ese
   agente falla después de la entrega, la sesión termina (`FAILED` o
   `PARTIAL_FAILURE` según la matriz de `BLOCK5_2_STATE_MACHINE.md`
   sección 9) y un intento nuevo requiere una **sesión enteramente
   nueva**, no un job nuevo.
3. Un job nuevo (pre-entrega) **puede** apuntar a un
   `assigned_agent_id` distinto del anterior — reasignación de agente
   solo está permitida antes de la primera entrega de credencial
   (`BLOCK5_2_ARCHITECTURE.md`/Domain A.1 sección 17).

### No-redelivery por sesión — invariante transaccional exacta (Domain A.2, Gates 3-5)

**Problema**: el gate de Domain A.1 (`credential_delivered_at IS NULL`
en el propio job) impide re-entrega al **mismo** job, pero no impedía,
por sí solo, que dos jobs **distintos** de la misma sesión ganaran la
carrera de entrega simultáneamente (Job A y Job B, ambos con su propio
`credential_delivered_at IS NULL`, podrían entregar "a la vez" sin
violar ningún constraint de Domain A.1).

**Estrategia elegida — B, la más simple con invariante inequívoca**:
el nuevo índice único parcial `UNIQUE(session_id) WHERE
credential_delivered_at IS NOT NULL` sobre `sol_agent_jobs` (arriba).
Bajo concurrencia real: dos conexiones PostgreSQL ejecutando

```sql
UPDATE sol_agent_jobs
SET credential_delivered_at = now()
WHERE id = :job_id AND credential_delivered_at IS NULL
```

para `job_id` **distintos** de la **misma** `session_id` — la primera
transacción en hacer `COMMIT` tiene éxito; la segunda recibe una
violación de unicidad (`unique_violation`) al intentar confirmar, y su
transacción se revierte por completo. **Nunca ambas ganan.**
Estrategia A (columna `credential_delivered_at`/`secret_consumed_at`
a nivel de `sol_access_sessions`) se descartó por añadir una segunda
fuente de verdad redundante con la del job — la misma garantía se
logra con un índice sobre una columna que ya existía, sin columna
nueva.

```
MAX_CREDENTIAL_DELIVERIES_PER_SESSION=1   -- DB-enforced, probado bajo concurrencia real en Domain B/C
```

**Creación de un job nuevo tras la entrega — capa distinta, a
propósito**: el índice de arriba impide que un job **entregue**, pero
no impide, por sí solo, que se **inserte** una fila de job nueva tras
la entrega (insertar un `PENDING` no viola ningún único, solo fallaría
si intentara entregar). Esto **no es una carrera concurrente** —
crear un job es una acción orquestada por el propio backend, no
disputada por actores externos — por lo que se resuelve con una
verificación de **aplicación** en el servicio de creación de jobs
(comprobar que ningún job hermano de la sesión tiene
`credential_delivered_at NOT NULL` antes de insertar), no con un
segundo mecanismo de BD. Si Domain B quisiera una segunda barrera de
BD (ej. un trigger `BEFORE INSERT`), sería un refuerzo opcional, no un
hueco del contrato congelado aquí.

```
NEW_JOB_AFTER_CREDENTIAL_DELIVERY=FORBIDDEN   -- aplicación (no es carrera concurrente); DB-enforced solo para el intento de ENTREGA en sí
```

## 4. `sol_agent_pairings`

**`PAIRING_TABLE_DECISION=YES` — congelado definitivamente, no es ya
una pregunta abierta.** Justificación (sin cambios respecto a la
recomendación de Domain A, ahora definitiva): el pairing tiene un TTL
propio de 120s completamente independiente del ciclo de vida de un
agente ya emparejado, y un intento de pairing puede fallar
permanentemente (`LOCKED`) sin que llegue a existir un `sol_agents` —
mezclar ambos ciclos de vida en una sola tabla obligaría a columnas
mayoritariamente nulas en la fila "real" del agente.

| Campo | Tipo | Nullable | Default | Notas |
|---|---|---|---|---|
| `id` | UUID (PK) | No | — | |
| `tenant_id` | UUID | No | — | FK `tenants.id` |
| `requested_by_user_id` | UUID | No | — | FK compuesta `(requested_by_user_id, tenant_id) → (users.id, users.tenant_id)` |
| `agent_display_name_candidate` | `String` | No | — | Nombre propuesto, se copia a `sol_agents.display_name` al confirmar |
| `pairing_code_verifier` | `String` | **Sí** (ver "Retención" abajo) | — | **Renombrado desde `pairing_code_hash` — Domain A.2, Gate 2; mecanismo corregido en Domain A.3.** `hex(HMAC-SHA-256(PAIRING_VERIFIER_KEY, mensaje canónico con dominio/tenant/pairing/código))` — **no** Argon2id, **no** un hash simple — ver justificación abajo. `NOT NULL` mientras `status='PENDING'`; `NULL` en cualquier estado terminal (`CheckConstraint`, ver "Retención"). **Ninguna columna de `key_version` se añade** — ver "Key rotation" abajo |
| `csr_fingerprint` | `String` | **Sí** | NULL | Metadata del CSR sanitizada — nulo hasta que el agente lo somete; nunca la clave privada |
| `status` | `String` + `CheckConstraint` | No | `'PENDING'` | `SolAgentPairingStatus` — exactamente `'PENDING'` / `'COMPLETED'` / `'LOCKED'` / `'EXPIRED'` / `'CANCELLED'` (Domain A.1 sección 11 — nunca `'ACTIVE'`, ese valor pertenece a `sol_agents`) |
| `failed_attempt_count` | `Integer` | No | `0` | Máximo 5 (sección de abajo) |
| `created_at` | `DateTime(timezone=True)` | No | `now()` | |
| `expires_at` | `DateTime(timezone=True)` | No | — | `created_at + 120s` (default) |
| `paired_at` | `DateTime(timezone=True)` | **Sí** | NULL | Al llegar a `COMPLETED` |
| `locked_at` | `DateTime(timezone=True)` | **Sí** | NULL | Al llegar a `LOCKED` |
| `confirmed_agent_id` | UUID | **Sí** | NULL | FK compuesta `(confirmed_agent_id, tenant_id) → (sol_agents.id, sol_agents.tenant_id)` — poblado atómicamente junto con `status='COMPLETED'` (sección 43) |
| `version` | `Integer` | No | `1` | |

**Nunca se persiste**: código de pairing en claro, clave privada, ni
ningún secreto crudo.

### Mecanismo del verificador — HMAC-SHA-256 con clave servidor-side (Domain A.3, corrige Gate 2 de Domain A.2)

**El código de pairing tiene baja entropía** (6 dígitos = 10⁶
combinaciones, ~19.9 bits) — muy por debajo de la entropía de un job
token (alta entropía, generado por el backend). Un hash simple
(`SHA-256(código)`, el mismo patrón usado para `job_token_hash`)
sería **enumerable offline** en fracciones de segundo si el
verificador se filtrara: bare `SHA-256` no tiene costo computacional
significativo, por lo que 10⁶ intentos son triviales de probar todos.

**Corrección de Domain A.3 — por qué Argon2id (Domain A.2) no basta**:
Domain A.2 propuso `hash_password()`/`verify_password()`
(`argon2.PasswordHasher()`) razonando que su costo computacional lo
volvía un *keyed verifier*. **Falso, verificado leyendo
`backend/app/core/security.py` completo esta ronda**:
`_password_hasher = PasswordHasher()` se instancia **sin ningún
parámetro de secreto/pepper** — ni en esa línea ni en ningún otro
punto del árbol `backend/` (barrido de `pepper|secret_key|PasswordHasher\(`
sin resultados adicionales). El paquete `argon2-cffi` añade un **salt
aleatorio por hash** (protege contra rainbow tables reutilizables
entre filas) pero su clase `PasswordHasher` **no expone ningún
parámetro de secreto servidor-side** — es Argon2id *salted*, no
*keyed*. El costo computacional (~decenas de ms) encarece un ataque de
fuerza bruta offline contra 10⁶ combinaciones, pero no lo impide: ante
una fuga de la fila (snapshot de BD, backup, réplica comprometida), un
atacante que solo tiene la BD puede agotar el espacio de 6 dígitos sin
necesitar nada más que el propio verificador. Por tanto:

```
PASSWORD_HASH_ALGORITHM=Argon2id (argon2-cffi, PasswordHasher() con parametros por defecto)
PASSWORD_HASH_USES_RANDOM_SALT=YES
PASSWORD_HASH_USES_SERVER_SIDE_PEPPER_OR_SECRET_KEY=NO
PASSWORD_HASH_SERVER_KEY_STORED_OUTSIDE_DB=NO   -- N/A, no existe tal clave
ARGON2_PASSWORD_HASH_IS_KEYED=NO
```

**Contrato final (Domain A.3)** — HMAC-SHA-256 con clave servidor-side
ausente de PostgreSQL, usando exclusivamente la librería estándar
`hmac`/`hashlib` de Python (ninguna criptografía casera):

```
canonical_message =
    b"sol-agent-pairing:v1" || 0x1F || tenant_id (str UUID) || 0x1F
    || pairing_id (str UUID) || 0x1F || code (6 digitos ascii)

pairing_code_verifier = hmac.new(
    PAIRING_VERIFIER_KEY, canonical_message, hashlib.sha256
).hexdigest()
```

- **Separación de dominio**: la etiqueta literal `"sol-agent-pairing:v1"`
  impide que un verificador válido para este protocolo sea reutilizable
  en cualquier otro uso presente o futuro de HMAC en el repo (ej. si
  algún día existiera otro flujo con verificador HMAC). Incluir
  `tenant_id` + `pairing_id` en el mensaje impide que un verificador
  sea trasladable entre filas o entre tenants — un HMAC calculado para
  la fila `pairing_id=A` nunca valida al comparar contra la fila
  `pairing_id=B`, incluso si por coincidencia ambas tuvieran el mismo
  código de 6 dígitos.
- **Delimitador `0x1F`** (Unit Separator ASCII, fuera del rango de
  caracteres válidos de un UUID o de un código de 6 dígitos decimales):
  evita ambigüedad de concatenación entre campos de longitud variable.
- **El mensaje canónico completo nunca se persiste** (contiene el
  código en claro) — solo se persiste el HMAC resultante, en
  `pairing_code_verifier`.
- **Verificación** — `hmac.compare_digest(pairing_code_verifier,
  hmac.new(PAIRING_VERIFIER_KEY, canonical_message,
  hashlib.sha256).hexdigest())`: comparación de tiempo constante de la
  librería estándar (`hmac.compare_digest`, no `==`).

```
PAIRING_CODE_BARE_HASH_ALLOWED=NO
PAIRING_CODE_KEYED_VERIFIER=YES
PAIRING_VERIFIER_PRIMITIVE=HMAC-SHA-256 (stdlib hmac/hashlib)
PAIRING_VERIFIER_DOMAIN_SEPARATED=YES
PAIRING_VERIFIER_SERVER_KEY_OUTSIDE_DB=YES
PAIRING_VERIFIER_CONSTANT_TIME_COMPARE=YES
PAIRING_VERIFIER_COLUMN=pairing_code_verifier
```

**Key storage — `PAIRING_VERIFIER_KEY`**: vive fuera de PostgreSQL,
reutilizando el **patrón** ya productivo de gestión de secretos de
`backend/app/core/config.py` (`Settings`, `pydantic-settings`,
`env_file=".env"`) — el mismo idioma ya usado para `session_secret`
(secreto de firma de sesión, obligatorio, sin default) e
`integration_token_encryption_key` (clave de cifrado, falla cerrado
como `INTEGRATION_MISCONFIGURED` si falta o es inválida, ADR-0064). Se
añade un ajuste **nuevo y de propósito específico** — no se reutiliza
literalmente `session_secret` ni `integration_token_encryption_key`,
para evitar *domain confusion* entre un secreto de firma de sesión, una
clave de cifrado de credenciales de integración de terceros, y una
clave de verificación de pairing (tres amenazas y tres superficies de
exposición distintas). El valor vive en variables de entorno/`.env`
del proceso backend — **nunca** en `sol_agent_pairings`, `audit_logs`,
logs de aplicación, frontend, agente Windows, ni navegador.

**Key rotation — `PAIRING_VERIFIER_KEY_VERSION_COLUMN_REQUIRED=NO`**:
no se añade columna `pairing_verifier_key_version` a `sol_agent_pairings`.
Justificación (mismo criterio de "no sobrediseñar" del encargo):
`integration_token_key_id` sí necesita persistirse porque cifra
secretos de **vida larga** (credenciales de integración que deben
poder descifrarse meses o años después, mucho después de una posible
rotación de clave). El verificador de pairing es lo opuesto: vive como
máximo `PAIRING_CODE_TTL_SECONDS=120` antes de alcanzar un estado
terminal y limpiarse (`PAIRING_VERIFIER_TERMINAL_RETENTION=CLEAR`,
ver abajo) — nunca se necesita verificar un código más allá de esa
ventana. Política operacional suficiente, sin DDL: la rotación de
`PAIRING_VERIFIER_KEY` se ejecuta en una ventana de *drain* (dejar de
emitir pairings nuevos, esperar como máximo 120s a que los `PENDING`
en vuelo lleguen a un estado terminal por confirmación o expiración,
rotar la clave, reanudar). Si se prefiriera evitar incluso ese drain
operacional, una implementación de Domain B puede aceptar en memoria
la clave `current` **y** la `previous` durante una ventana acotada
(`>= 120s`) sin que eso requiera ninguna columna — es un detalle de
despliegue, no del contrato de datos.

**No se diseña criptografía casera** — HMAC-SHA-256 vía los módulos
estándar de Python `hmac`/`hashlib`, mismo criterio ya aplicado en
Domain A.2 al preferir `hashlib.sha256` para `hash_session_token()`.

**Estados** — terminal: `COMPLETED`, `LOCKED`, `EXPIRED`, `CANCELLED`.
No terminal: únicamente `PENDING`.

**Intentos fallidos** (evidencia Spike B, TTL=120s, máx=5 fallos,
Domain A.1 sección 12): `failed_attempt_count < 5` permite reintentar
el código dentro del mismo registro; el **quinto** fallo pone
`status='LOCKED'` + `locked_at=now()` — terminal, **sin resetear el
contador**. Un nuevo intento de pairing exige un `sol_agent_pairings`
completamente nuevo (nuevo código, nuevo TTL).

**Constraints**:
- `CheckConstraint("expires_at > created_at")`.
- `UNIQUE(tenant_id, requested_by_user_id) WHERE status = 'PENDING'`
  — como mucho un pairing pendiente simultáneo por usuario dentro de
  un tenant (evita ambigüedad de "a cuál pairing pendiente confirma
  este código"); distintos usuarios del mismo tenant sí pueden parear
  agentes distintos en paralelo.
- **`CheckConstraint("(status = 'PENDING' AND pairing_code_verifier IS
  NOT NULL) OR (status != 'PENDING' AND pairing_code_verifier IS
  NULL)")`** — **nueva en Domain A.2** (Gate 3), mismo idioma de
  coherencia ya productivo en
  `client_sol_credential.ck_client_sol_credentials_disabled_coherence`.
- Índice `(tenant_id, status)`, `(expires_at)`.

**Fuerza bruta más allá del límite por registro** (Domain A.1 sección
44): el límite de 5 fallos protege un **registro** de pairing — para
impedir que alguien simplemente cree registros nuevos sin límite y
reinicie su presupuesto de intentos, se añade una **segunda capa**,
reutilizando el patrón ya productivo de `login_attempt_service.py`
(ADR-0009: conteo de filas + backoff exponencial por identificador,
sin Redis) — aquí el "identificador" es `(tenant_id,
requested_by_user_id)` y lo que se cuenta son creaciones de
`sol_agent_pairings` recientes, no intentos de código. **No requiere
tabla nueva** — se computa sobre la propia tabla `sol_agent_pairings`
(igual que `login_attempts` ya sirve tanto para registrar como para
calcular el lockout). Exacto umbral/ventana: a definir en Domain B,
siguiendo la misma fórmula de `get_lockout_status()`.

## 5. Tiempo de base de datos como autoridad

**Congelado (Domain A.1 sección 52)**: toda comparación de expiración,
validez de claim, expiración de pairing, y resolución de carreras usa
el **tiempo de transacción de la base de datos** (`now()` de
PostgreSQL, evaluado en el servidor) — **nunca** un timestamp
enviado por el agente. Los timestamps que el agente reporta (si los
hubiera, en telemetría de eventos) son **solo informativos**, jamás
autoritativos para una decisión de seguridad.

**Restricción cruzada `job.expires_at ≤ session.expires_at`**:
PostgreSQL no soporta `CHECK` constraints que referencien otra tabla —
se **fuerza a nivel de aplicación** en el momento de crear el job
(`expires_at = min(now() + job_ttl_default, session.expires_at)`),
reforzable opcionalmente con un trigger `BEFORE INSERT` en Domain B si
se quiere una segunda barrera a nivel de BD.

## 6. Notas de propiedad y unicidad (Domain A.1, secciones 15-17, 33)

- **Aislamiento de tenant**: cada tabla de este documento lleva su
  propio `tenant_id` **y** FKs compuestas atadas a `tenant_id` en cada
  referencia — nunca una FK simple a otra tabla sin repetir
  `tenant_id`. Confirma el patrón ya productivo de
  `client_sol_credential.py`.
- **Propiedad de la credencial** (Domain A.1 sección 16, **confirmado
  con evidencia real en Domain A.2, Gate 1**): no basta
  `credential.tenant_id == session.tenant_id` — la FK compuesta de 3
  columnas de la sección 1 (`credential_id, client_id, tenant_id`)
  obliga también `credential.client_id == session.client_id`
  estructuralmente. Se inspeccionó directamente el modelo productivo
  (`backend/app/models/client_sol_credential.py`) y la migración que lo
  crea (`backend/migrations/versions/0017_sol_vault.py`, líneas
  299-300):

  ```python
  UniqueConstraint(
      "id", "tenant_id", "client_id", name="uq_client_sol_credentials_id_tenant_client"
  )
  ```

  ```
  CLIENT_SOL_CREDENTIAL_PK=id (UUID, UUIDPrimaryKeyMixin)
  CLIENT_SOL_CREDENTIAL_UNIQUES=uq_client_sol_credentials_id_tenant_client(id, tenant_id, client_id); ux_client_sol_credentials_active_per_client(tenant_id, client_id) WHERE status <> 'DISABLED'
  CREDENTIAL_COMPOSITE_FK_SUPPORTED=YES
  CREDENTIAL_SUPPORTING_UNIQUE_REQUIRED=NO
  ```

  **Esta restricción ya existe, productiva, desde Bloque 5.1** — cubre
  exactamente el conjunto de columnas `{id, tenant_id, client_id}` que
  la FK de `sol_access_sessions` necesita referenciar. **`0018` no
  necesita tocar `client_sol_credentials` en absoluto** — Estrategia A
  del encargo de Domain A.2 ("migration 0018 añadirá una UNIQUE
  supporting constraint") resulta innecesaria porque la constraint ya
  está ahí; no se trata de Estrategia B tampoco (no hace falta
  reformular nada) — es, literalmente, "ya resuelto por Bloque 5.1".
  `sol_access_sessions` declara su FK con el mismo idioma ya usado por
  `client_sol_credential.py` para sus propias FKs compuestas (ej.
  `fk_client_sol_credentials_data_key_version`):

  ```python
  ForeignKeyConstraint(
      ["credential_id", "client_id", "tenant_id"],
      ["client_sol_credentials.id", "client_sol_credentials.client_id", "client_sol_credentials.tenant_id"],
      name="fk_sol_access_sessions_credential_client_tenant",
  )
  ```

  ```
  CREDENTIAL_OWNERSHIP_DB_ENFORCED=YES
  ```
- **Duplicados de creación de sesión** (Domain A.1 sección 33): como
  mucho **una sesión no-terminal por `(tenant_id, client_id,
  credential_id)`**, sin importar qué usuario la solicitó — el
  navegador/agente subyacente y la propia sesión SUNAT son
  inherentemente de un solo vuelo por credencial. Forzado por el
  índice único parcial de la sección 1.

## 7. Enums persistidos — lista congelada y exhaustiva

Ningún otro enum se persiste — específicamente, **`ONLINE`/`OFFLINE`
nunca es un enum persistido** (se deriva, sección 2).

| Enum | Tabla.columna | Valores |
|---|---|---|
| `SolAccessScope` | `sol_access_sessions.scope` | `AUTHENTICATE_TO_LANDING_ONLY` (único valor hoy) |
| `SolAccessSessionStatus` | `sol_access_sessions.status` | `CREATED`, `WAITING_FOR_AGENT`, `WAITING_FOR_USER`, `IN_PROGRESS`, `SUCCEEDED`, `PARTIAL_FAILURE`, `FAILED`, `CANCELLED`, `EXPIRED` |
| `SolAccessOperationResult` | `sol_access_sessions.operation_result` | `LANDING_REACHED` (único valor hoy — `BLOCK5_2_STATE_MACHINE.md` sección 6) |
| `SolAccessTerminalReasonCode` | `sol_access_sessions.terminal_reason_code` | 20 valores — `BLOCK5_2_STATE_MACHINE.md` sección 9 (renombrado desde `SolAccessFailureCode`) |
| `SolAgentStatus` | `sol_agents.status` | `ACTIVE`, `REVOKED` |
| `SolAgentJobStatus` | `sol_agent_jobs.status` | `PENDING`, `CLAIMED`, `COMPLETED`, `FAILED`, `CANCELLED`, `EXPIRED` |
| `SolAgentPairingStatus` | `sol_agent_pairings.status` | `PENDING`, `COMPLETED`, `LOCKED`, `EXPIRED`, `CANCELLED` |

## 8. Tablas candidatas para la futura migración `0018` — FREEZE definitivo

```
MIGRATION_0018_TABLE_COUNT=4

sol_agents
sol_access_sessions
sol_agent_jobs
sol_agent_pairings
```

**`sol_access_audit_events` NO se crea** — extiende
`audit_logs`/`AuditAction` existente en su lugar
(`AUDIT_STORAGE_MODEL=EXISTING_AUDIT_INFRASTRUCTURE`, ver
`BLOCK5_2_SECURITY_MODEL.md` sección 7).

**Ningún SQL se escribe en esta ronda.** La migración `0018` real es
trabajo de Domain B/C, después de que este freeze sea revisado.

## 9. Plan de índices (inventario completo, sin SQL)

```
sol_access_sessions:
  ix (tenant_id, status)
  ix (expires_at)
  ux (tenant_id, idempotency_key)
  ux (tenant_id, client_id, credential_id) WHERE status NOT IN (terminales)

sol_agents:
  ux (certificate_fingerprint)                     -- global
  ix (tenant_id, status)
  ix (last_seen_at)

sol_agent_jobs:
  ux (job_token_hash)                              -- global
  ux (session_id) WHERE status IN ('PENDING','CLAIMED')
  ux (agent_id) WHERE status IN ('PENDING','CLAIMED')
  ux (session_id) WHERE credential_delivered_at IS NOT NULL   -- Domain A.2, Gate 3/4
  ix (session_id, status)
  ix (agent_id, status)
  ix (expires_at)

sol_agent_pairings:
  ux (tenant_id, requested_by_user_id) WHERE status = 'PENDING'
  ix (tenant_id, status)
  ix (expires_at)
```

`ux` = índice único (parcial donde se indica `WHERE`), `ix` = índice
no único.

## 10. Política de borrado y retención

- **Sin hard delete** en ninguna de las 4 tablas — no existe (ni
  existirá en el alcance inicial) un endpoint `DELETE` en la User API
  (`BLOCK5_2_API_CONTRACTS.md`).
- **`sol_agents`**: el ciclo de vida se gestiona vía `status=REVOKED`,
  nunca se borra una fila — preserva el historial de
  `certificate_fingerprint` para detectar reintentos de reuso.
- **`sol_access_sessions` / `sol_agent_jobs` / `sol_agent_pairings`**:
  se retienen — sin soft-delete explícito porque no existe concepto de
  "borrar" visible al usuario, solo antigüedad natural.
- **Plazo exacto de retención**: configurable, **a alinear en Domain B
  con la política de retención que ya gobierne `audit_logs`** (no
  confirmada en esta ronda — verificar antes de fijar un número).
- **`pairing_code_verifier` tras un estado terminal — decisión
  revertida en Domain A.2 (Gate 3)**: **SÍ se anula** (`NULL`) al
  entrar a cualquier estado terminal (`COMPLETED`/`LOCKED`/`EXPIRED`/
  `CANCELLED`), forzado por el `CheckConstraint` de coherencia de la
  sección de constraints arriba.

  ```
  PAIRING_VERIFIER_TERMINAL_RETENTION=CLEAR
  ```

  **Por qué se revierte la decisión de Domain A.1** ("no anular, no
  protege nada explotable"): esa justificación asumía un hash simple,
  donde el argumento era válido (comparación de un solo sentido, sin
  costo). Con el verificador HMAC keyed final de Domain A.3, el
  argumento de "no protege nada" es **más fuerte todavía**: sin
  `PAIRING_VERIFIER_KEY` (fuera de la BD) un atacante con solo la fila
  no puede validar ningún intento offline en absoluto, esté o no
  limpiado el campo — pero limpiar sigue siendo la postura correcta
  por minimización de datos (principio declarado explícitamente en el
  encargo de Domain A.2, "si ya no es necesario para verificar ningún
  código, minimizar retención") y por defensa en profundidad ante un
  eventual compromiso futuro de la clave: si `PAIRING_VERIFIER_KEY`
  se filtrara más adelante, las filas ya limpiadas siguen sin exponer
  nada verificable, mientras que filas que conservaran el verificador
  sí quedarían enumerables retroactivamente contra esa clave filtrada.
  El campo ya era nullable por diseño de estados, y el resto de la
  auditoría (`status`, `failed_attempt_count`, timestamps,
  `confirmed_agent_id`) se conserva íntegro. Sin costo funcional de
  implementar la postura conservadora, se adopta.

## 11. Recuento final de columnas (Domain A.2, secciones 8-9 del encargo)

**`sol_access_sessions` — se mantiene en 20** (sin cambio respecto a
Domain A.1): ninguno de los Gates de Domain A.2 requirió una columna
nueva aquí — la invariante de "una sola entrega por sesión" se resolvió
con un índice sobre una columna que ya vivía en `sol_agent_jobs`
(sección 3), no con una columna nueva en `sol_access_sessions`; y el
patrón real de idempotencia (`tax_payment_service.py`) confirmó que no
hace falta una columna de fingerprint de payload (sección "Constraint
Register Final" abajo, fila de idempotencia). La corrección del
contrato primó sobre el número previo — en este caso, coincide.

**`sol_agent_pairings` — 14 columnas**: `id`, `tenant_id`,
`requested_by_user_id`, `agent_display_name_candidate`,
`pairing_code_verifier`, `csr_fingerprint`, `status`,
`failed_attempt_count`, `created_at`, `expires_at`, `paired_at`,
`locked_at`, `confirmed_agent_id`, `version` — sin cambio de conteo
respecto a Domain A.1 (el cambio de esta tabla en Domain A.2/A.3 es el
nombre/mecanismo/nullability de `pairing_code_verifier`, no la
cantidad de columnas). **Domain A.3 confirma explícitamente que
tampoco se añade `pairing_verifier_key_version`** — justificado en la
sección "Key rotation" de la sección 4: el TTL de 120s hace innecesaria
la persistencia de versión de clave; una ventana de *drain* operacional
basta. `PAIRING_COLUMNS_FROZEN=YES` recién queda cerrado con esta
confirmación, no antes.

**`sol_agent_jobs` — 12 columnas**, sin cambio de conteo (el nuevo
constraint de Domain A.2 es un índice sobre `credential_delivered_at`,
una columna que ya existía desde Domain A.1 — no se añadió ninguna
columna).

## 12. Registro de constraints final (Domain A.2, sección 10 del encargo)

| Tabla | Constraint/índice | Columnas/predicado | Propósito | DB-enforced |
|---|---|---|---|---|
| `sol_access_sessions` | `fk_..._client_tenant` | `(client_id, tenant_id) → clients` | Tenant-safe | Sí |
| `sol_access_sessions` | `fk_..._credential_client_tenant` | `(credential_id, client_id, tenant_id) → client_sol_credentials` | Propiedad de credencial (Gate 1) | Sí |
| `sol_access_sessions` | `fk_..._requester_tenant` | `(requested_by_user_id, tenant_id) → users` | Tenant-safe | Sí |
| `sol_access_sessions` | `fk_..._agent_tenant` | `(assigned_agent_id, tenant_id) → sol_agents` | Tenant-safe | Sí |
| `sol_access_sessions` | `ck_..._scope`/`status`/`operation_result`/`terminal_reason_code` | Valores cerrados | Enums persistidos | Sí |
| `sol_access_sessions` | `uq_..._idempotency_key` | `(tenant_id, idempotency_key)` | Idempotencia (Gate 6, patrón `TaxPayment`) | Sí |
| `sol_access_sessions` | `ux_..._active_per_credential` | `(tenant_id, client_id, credential_id) WHERE status NOT IN (terminales)` | Máx. 1 sesión no-terminal por credencial | Sí |
| `sol_agents` | `uq_..._certificate_fingerprint` | `(certificate_fingerprint)` global | Identidad criptográfica única | Sí |
| `sol_agents` | `ck_..._revoked_coherence` | `status='REVOKED' ⇒ revoked_at NOT NULL` | Coherencia de revocación | Sí |
| `sol_agent_jobs` | `fk_..._session_tenant`, `fk_..._agent_tenant` | Compuestas a `tenant_id` | Tenant-safe | Sí |
| `sol_agent_jobs` | `uq_..._job_token_hash` | `(job_token_hash)` global | Un solo uso, anti-replay | Sí |
| `sol_agent_jobs` | `ux_..._active_per_session` | `(session_id) WHERE status IN ('PENDING','CLAIMED')` | Máx. 1 job activo/sesión | Sí |
| `sol_agent_jobs` | `ux_..._active_per_agent` | `(agent_id) WHERE status IN ('PENDING','CLAIMED')` | Máx. 1 job activo/agente | Sí |
| `sol_agent_jobs` | `ux_..._one_delivery_per_session` | `(session_id) WHERE credential_delivered_at IS NOT NULL` | Máx. 1 entrega de credencial/sesión, bajo concurrencia real (Gate 3/4) | Sí |
| `sol_agent_pairings` | `fk_..._requester_tenant`, `fk_..._confirmed_agent_tenant` | Compuestas a `tenant_id` | Tenant-safe | Sí |
| `sol_agent_pairings` | `ck_..._expires_after_created` | `expires_at > created_at` | Coherencia temporal | Sí |
| `sol_agent_pairings` | `ux_..._pending_per_requester` | `(tenant_id, requested_by_user_id) WHERE status='PENDING'` | Sin ambigüedad de qué pairing confirma un código | Sí |
| `sol_agent_pairings` | `ck_..._verifier_coherence` | `PENDING ⇒ verifier NOT NULL`, `terminal ⇒ verifier NULL` | Retención del verificador (Gate 3) | Sí |
| `client_sol_credentials` | `uq_client_sol_credentials_id_tenant_client` (ya existe, Bloque 5.1) | `(id, tenant_id, client_id)` | Soporte de la FK de propiedad de credencial (Gate 1) | Sí — **sin cambio necesario en `0018`** |
| — | `job.expires_at ≤ session.expires_at` | Cruza tablas | Coherencia de TTL | **No** — Postgres no soporta `CHECK` entre tablas; forzado a nivel de aplicación al crear el job (Domain A.1 sección 5), reforzable con trigger opcional |
| — | `NEW_JOB_AFTER_CREDENTIAL_DELIVERY=FORBIDDEN` | Cruza filas de la misma tabla | Prohibir job nuevo tras entrega | **No** — no es una carrera concurrente, se resuelve en el servicio de creación de jobs (Gate 5) |

**Todas las filas marcadas "Sí" son probables con dos conexiones
PostgreSQL reales en Domain B/C** (constraint/índice a nivel de motor,
no de aplicación) — las dos únicas filas "No" están justificadas
explícitamente (limitación real de PostgreSQL para la primera; ausencia
genuina de concurrencia para la segunda), no son huecos sin resolver.

```
CONSTRAINT_REGISTER_FROZEN=YES
```
