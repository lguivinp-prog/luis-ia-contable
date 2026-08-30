# Block 5.2 — Modelo de seguridad: Supervised SOL Access

**Domain A — Architecture & Contract Freeze**, congelado en **Domain
A.1 — Persistence & Concurrency Contract Freeze**, reforzado en
**Domain A.2 — Final DDL Security & Compatibility Gate** (sección 4 —
invariante de no-redelivery a nivel de sesión) y corregido en **Domain
A.3 — Pairing Verifier Cryptographic Freeze** (T21 — el verificador de
pairing es HMAC-SHA-256 *keyed*, no Argon2id *salted* como Domain A.2
había afirmado; el mecanismo exacto vive en
`BLOCK5_2_DATA_MODEL_DRAFT.md` sección 4, este documento solo trata su
amenaza). Complementa `BLOCK5_2_ARCHITECTURE.md` y
`BLOCK5_2_DATA_MODEL_DRAFT.md`. El campo antes llamado `failure_code`
se renombra a `terminal_reason_code` en todo este documento (ver
`BLOCK5_2_STATE_MACHINE.md` sección 10).

**Domain E1-A — Secret Delivery Architecture & Contract Freeze**: la
tabla de amenazas de la sección 12 se extiende con T23–T42 (entrega de
secreto SOL al agente). El contrato/orden/matrices de fallo completos
viven en `BLOCK5_2_SECRET_DELIVERY_ARCHITECTURE.md` — este documento
solo trata las amenazas, no reabre las secciones 1–11 existentes.

## 1. Frontera de credenciales — Alternative A (Spike B, sin rediseñar)

```
SOL Vault (Block 5.1, ya productivo)
    ↓
backend autorizado
    ↓ descifrado puntual (just-in-time)
job credential payload
    ↓ mTLS
Windows Agent
    ↓
Chromium
    ↓
SUNAT
```

El agente recibe **únicamente**:
- El `username` necesario para el job actual.
- El `password` necesario para el job actual.
- Identificadores de job/sesión no sensibles.

El agente **nunca** recibe:

```
vault_root_key
credential_dek
master password
recovery secret
recovery slot data
```

**No se persiste texto plano** en ningún punto: no en BD, no en
archivo, no en archivo temporal, no en argumentos de CLI, no en
variables de entorno, no en logs, no en auditoría, no en mensajes de
excepción. Este es el mismo principio estructural que
`observer/manual_auth.py` de Spike A ya demostró (barrido estático:
"este CLI JAMÁS rellena, lee, ni intercepta usuario/clave/RUC/OTP/
CAPTCHA") — aquí se extiende al agente productivo.

**Limitación documentada, no prometida como resuelta**: Python **no
garantiza** zeroización perfecta de memoria (el recolector de basura y
el modelo de objetos de CPython no ofrecen esa garantía como lo haría,
p. ej., `SecureString` en .NET o `mlock`/`explicit_bzero` en C). El
diseño minimiza la vida útil del secreto en memoria (descarte de
referencias "as soon as practical" tras entregarlo al navegador, ver
sección 3) pero **no se afirma** que el secreto quede criptográficamente
borrado de la memoria del proceso del agente en un tiempo determinado.

## 2. Integración con el Vault (Block 5.1)

Block 5.2 **consume** Block 5.1 — no duplica:

- Criptografía del vault (`backend/app/core/sol_vault_crypto.py`).
- Manejo de slots (`backend/app/core/sol_vault_registry.py`,
  `VaultUnlockRegistry`).
- Cifrado/rotación de `credential_dek`
  (`backend/app/services/sol_vault_service.py`,
  `backend/app/services/client_sol_credential_service.py`).
- Lógica de recuperación en dos pasos.

**Contrato de solicitud de credencial** (para diseñar en Domain B,
congelado conceptualmente aquí): la solicitud debe incluir
`tenant_id`, `client_id`, `credential_id`, el usuario que solicita, y
el `session_id`/`job_id` para el que se solicita — y debe existir
**autorización previa al descifrado**: la credencial solo se descifra
`just-in-time` cuando simultáneamente existan:

1. Una `sol_access_session` en estado válido (no terminal).
2. Un `sol_agent_job` reclamado por un agente `ACTIVE` del mismo
   tenant.
3. Autorización RBAC vigente del usuario solicitante
   (`sol_session:create`, ver `BLOCK5_2_RBAC_MATRIX.md`).

Si el vault está bloqueado (`VaultUnlockRegistry` sin slot
desbloqueado) o en recuperación: `terminal_reason_code =
VAULT_LOCKED`/`VAULT_RECOVERY_REQUIRED`, la sesión nunca llega a
`WAITING_FOR_AGENT`.

### Precondiciones exactas de entrega del secreto (Domain A.1, sección 49 — congelado)

El descifrado `just-in-time` solo ocurre cuando **todas** las
siguientes condiciones se cumplen simultáneamente:

```
session:
  status NOT IN (terminales)
  expires_at > now()

job:
  status = 'CLAIMED'
  agent_id = <agente que solicita>       -- binding
  session_id = <sesión que autoriza>     -- binding
  expires_at > now()
  job_token_hash coincide con el token presentado
  credential_delivered_at IS NULL        -- gate de un solo uso, sección 4

agent:
  status = 'ACTIVE'
  identidad mTLS coincide con certificate_fingerprint

autorización del usuario:
  ya validada al crear la sesión (sol_session:create) — no se
  re-valida en este paso, pero la sesión no debe haber sido alterada
  desde entonces

vault:
  desbloqueado según VaultUnlockRegistry (Block 5.1)

credential:
  pertenece a (tenant_id, client_id) de la sesión (FK compuesta de 3
  columnas, BLOCK5_2_DATA_MODEL_DRAFT.md sección 1)
  disponible (no en un estado que impida su uso, ej. DISABLED)
```

Solo entonces se ejecuta el descifrado. Cualquier condición no
satisfecha aborta antes de tocar el vault.

## 3. Envelope de entrega de secreto (conceptual)

Contenido mínimo indispensable:

```
job_id
session_id
credential_username
credential_password
scope
auth_policy_version
portal_policy_version
expires_at
```

**Nunca incluye**: secretos de tenant, claves del vault, datos de
recuperación.

- **Solo en memoria** — nunca serializado a disco por el agente ni por
  el backend más allá del tiempo de tránsito mTLS.
- Tras entregar el envelope al proceso que controla el navegador: se
  descartan las referencias **tan pronto como sea posible en la
  práctica** (ver limitación de zeroización, sección 1).

## 4. Re-entrega de credenciales

**Por defecto: sin re-entrega automática.**

Si el agente pierde la credencial o el job (crash, desconexión,
reinicio): la sesión debe fallar (`AGENT_CRASHED`/`BROKER_DISCONNECTED`)
o requerir un **camino de retry explícito** — nunca reenvío silencioso
de una contraseña al mismo o a otro job.

**Mecanismo exacto, congelado en Domain A.1 (sección 50)**: columna
`sol_agent_jobs.credential_delivered_at` (`DateTime`, nullable) — **no
un contador**. La entrega se autoriza mediante un predicado atómico
`credential_delivered_at IS NULL` (parte de las precondiciones de la
sección 2); al entregar, se hace `UPDATE ... SET
credential_delivered_at = now() WHERE id = :job_id AND
credential_delivered_at IS NULL` — si la fila ya tenía
`credential_delivered_at` puesto, el `UPDATE` no afecta ninguna fila y
la entrega se rechaza. Un simple contador no aportaría nada que este
gate binario no dé ya, y complicaría innecesariamente la condición de
autorización.

Máximo **1 entrega por job**, para siempre — nunca se resetea. Un
retry siempre implica:

1. Un **nuevo** job (nunca se reutiliza el job cuyo
   `credential_delivered_at` ya está poblado — ver
   `BLOCK5_2_DATA_MODEL_DRAFT.md` sección 3, "Multiplicidad
   job/sesión": si el job anterior ya entregó el secreto, ni siquiera
   se permite crear un job nuevo en la misma sesión — se requiere una
   sesión enteramente nueva).
2. Un **nuevo** job token (nunca se reutiliza el anterior).
3. Una **nueva** autorización de descifrado (vuelve a pasar por el
   contrato de la sección 2).
4. Un **nuevo** evento de auditoría — nunca silencioso.

**Refuerzo de Domain A.2 (Gate 3-4)**: el gate `credential_delivered_at
IS NULL` de arriba, por sí solo, protege contra la re-entrega al
**mismo** job — pero **no** bastaba, bajo concurrencia real, para
impedir que **dos jobs distintos de la misma sesión** entregaran
"simultáneamente" (una carrera Job A/Job B, cada uno con su propio
gate satisfecho de forma independiente). Se añadió un índice único
parcial en `sol_agent_jobs` — `UNIQUE(session_id) WHERE
credential_delivered_at IS NOT NULL` — que hace que **PostgreSQL
mismo**, no la aplicación, garantice que como mucho un job de toda la
sesión llega a entregar, incluso si dos `UPDATE` compiten en el mismo
instante. Ver `BLOCK5_2_DATA_MODEL_DRAFT.md` sección 3 ("No-redelivery
por sesión") para el mecanismo transaccional exacto y la matriz de
constraints.

```
MAX_CREDENTIAL_DELIVERIES_PER_SESSION=1
CREDENTIAL_DELIVERY_CONCURRENCY_DB_SAFE=YES
NEW_JOB_AFTER_CREDENTIAL_DELIVERY=FORBIDDEN
```

## 5. Identidad del agente y jobs — aspectos de seguridad

(Ver `BLOCK5_2_DATA_MODEL_DRAFT.md` para las tablas completas —
aquí solo los invariantes de seguridad.)

- **Job token**: alta entropía, generado por el backend, entregado al
  agente ya autenticado por mTLS junto con el job (nunca vía User
  API). **Se persiste únicamente su hash** (nunca el token plano) —
  mismo principio que cualquier secreto de un solo uso ya existente en
  el repo. El hash **permanece** tras el uso (detección de replay/
  auditoría), nunca se borra.
- **Job dirigido, no competido**: el job se crea ya con `agent_id`
  fijado al agente asignado a la sesión (`BLOCK5_2_DATA_MODEL_DRAFT.md`
  sección 3) — "claim" es la confirmación de ESE agente específico,
  nunca una carrera abierta entre agentes distintos por un job
  genérico.
- **Binding obligatorio**: `job` + `agent` + `session` — un job nunca
  es válido fuera de la tripleta exacta con la que fue emitido.
- **Un solo uso**: tras `CLAIMED`, ningún segundo `claim` sobre el
  mismo job puede tener éxito.
- **Replay rechazado**: un token ya usado (exitoso, fallido, cancelado,
  o expirado) nunca vuelve a ser aceptado — `terminal_reason_code =
  JOB_REPLAY_DETECTED`.
- **Agente revocado**: cualquier intento de claim/heartbeat de un
  agente en estado `REVOKED` se rechaza inmediatamente
  (`terminal_reason_code = AGENT_REVOKED`), sin importar si el
  certificado mTLS sigue siendo criptográficamente válido hasta su
  expiración natural — la revocación en `sol_agents.status` es la
  fuente de verdad de autorización, no solo el certificado.
  `sol_agents.revoked_at` es obligatorio (`CheckConstraint`) siempre
  que `status='REVOKED'`. Cualquier job activo (`PENDING`/`CLAIMED`)
  del agente recién revocado debe forzarse hacia `CANCELLED`/`FAILED`
  usando el mismo mecanismo de `UPDATE` condicional de la sección
  "Matriz de carreras" abajo.
- **Identidad de certificado — un solo certificado activo por agente**
  (Domain A.1 sección 9): `certificate_fingerprint` vive directamente
  en `sol_agents`, sin tabla de certificados/PKI separada en 5.2.
  Rotar el certificado de un agente ya emparejado requiere revocar +
  re-parear — no existe todavía un flujo de rotación in-place. Es una
  limitación aceptada para esta primera versión, documentada, no un
  olvido.

### Claim atomicity (Domain A.1, sección 23)

```sql
UPDATE sol_agent_jobs
SET status = 'CLAIMED', claimed_at = now(), version = version + 1
WHERE id = :job_id
  AND agent_id = :presented_agent_id      -- ya fijado en creación, ver arriba
  AND status = 'PENDING'
  AND expires_at > now()
  AND job_token_hash = :presented_token_hash
  AND session_id = :session_id
RETURNING id;
```

Si la actualización afecta 0 filas, el claim falla. El agente recibe
un rechazo **genérico y estable** (sin distinguir públicamente "ya
reclamado" vs. "expirado" vs. "token inválido") — evita dar
información de diagnóstico útil para un intento de adivinación de
token; el detalle real queda solo en logs internos sanitizados.

### Matriz de carreras (Domain A.1, secciones 24-26)

Todas resueltas por el mismo mecanismo: **la actualización que
efectivamente afecta una fila (`rowcount > 0`) gana; la que llega
después, contra una fila ya en el estado que perseguía cambiar, es un
no-op** — nunca dos actualizaciones "ganan" a la vez, nunca se necesita
un booleano ad-hoc.

| Carrera | Resolución determinista |
|---|---|
| Cancel vs. claim | Si el `UPDATE` de cancelación llega primero: `job.status → CANCELLED` (si estaba `PENDING`); el `UPDATE` de claim posterior no encuentra `status='PENDING'` → falla. Si el claim llega primero: `job.status → CLAIMED`; el cancel posterior ya no puede evitar el claim, pero SÍ puede cancelar la **sesión** — si `credential_delivered_at` seguía `NULL` en ese momento, el job también se fuerza a `CANCELLED`; si ya se había entregado la credencial, el job queda como quede (lo reporte el agente), pero la sesión pasa a `CANCELLED` de todas formas (terminal, ver `BLOCK5_2_STATE_MACHINE.md`) |
| Cancel vs. success | Si `SUCCEEDED` se persistió primero (terminal): un cancel posterior es idempotente, devuelve el estado terminal existente sin error. Si `CANCELLED` se persistió primero (terminal): un reporte de éxito posterior del agente encuentra `status NOT IN (no-terminales)` → el `UPDATE` no afecta filas → se descarta (se registra en auditoría como "reporte tardío tras cancelación", pero la sesión permanece `CANCELLED`) |
| Timeout vs. success | Mismo mecanismo — quien complete primero la transacción condicionada al estado no-terminal gana; el tiempo de comparación de TTL siempre es tiempo de BD (`BLOCK5_2_DATA_MODEL_DRAFT.md` sección 5), nunca el reloj del agente |

### Heartbeat y derivación online/offline (Domain A.1, secciones 27-28)

- El heartbeat **muta** `sol_agents.last_seen_at`, vía una llamada
  interna agente→backend autenticada por mTLS (nunca un `GET` de
  usuario — "ningún `GET` muta", `BLOCK5_2_API_CONTRACTS.md` sección
  7). Se recomienda **coalescer** las escrituras (solo persistir si
  `last_seen_at` está más desactualizado que un umbral corto) para
  evitar amplificación de escrituras por heartbeats muy frecuentes —
  el umbral exacto se deja a Domain B.
- Fórmula de derivación (nunca persistida):
  `ONLINE si (now() - last_seen_at) <= umbral_de_heartbeat, si no OFFLINE`.
  Umbral configurable, valor exacto no fijado en Domain A.1 (decisión
  pendiente #5 de Domain A, sin impacto de DDL — ver
  `BLOCK5_2_IMPLEMENTATION_PLAN.md`).
- **Regla dura**: el estado `ONLINE`/`OFFLINE` derivado **nunca** es
  , por sí solo, suficiente para autorizar la asignación de un job — el
  `claim` real vuelve a validar la identidad mTLS de la conexión viva
  en ese instante (sección "Claim atomicity" arriba), independiente de
  cuán reciente sea `last_seen_at`.

### Agent job capacity (Domain A.1, sección 34)

```
MAX_ACTIVE_JOB_PER_AGENT = 1
MAX_ACTIVE_JOB_PER_SESSION = 1
```

Forzado por índices únicos parciales
(`BLOCK5_2_DATA_MODEL_DRAFT.md` sección 3) — nunca solo por lógica de
aplicación. Simplifica aislamiento del navegador, vida de la
credencial, interacción humana y visibilidad de escritorio: un agente
físico solo controla una sesión SUNAT visible a la vez.

## 6. Aislamiento de tenant

Toda entidad nueva de 5.2 (`sol_access_sessions`, `sol_agents`,
`sol_agent_jobs`, y la tabla de auditoría/pairing si se separan) debe
ser tenant-safe **estructuralmente**, no solo por disciplina de
aplicación — mismo patrón ya productivo en
`backend/app/models/client_sol_credential.py` (ADR-0003/ADR-0024):
cada tabla hijo lleva su propio `tenant_id` **y** una FK compuesta
`(fk_id, tenant_id) → (padre.id, padre.tenant_id)`, de forma que una
fila **no puede** referenciar estructuralmente un padre de otro
tenant, incluso ante un bug de aplicación. Ver
`BLOCK5_2_DATA_MODEL_DRAFT.md` para el detalle exacto de FKs
compuestas propuestas.

## 7. Modelo de auditoría

Extiende el `AuditAction` enum ya productivo
(`backend/app/models/audit_log.py`) con un nuevo banner — **no crea
una tabla paralela**:

```python
class AuditAction(str, enum.Enum):
    ...
    # ---- Fase 5, Bloque 5.2 ----
    SOL_SESSION_CREATED = "SOL_SESSION_CREATED"
    AGENT_ASSIGNED = "AGENT_ASSIGNED"
    JOB_CLAIMED = "JOB_CLAIMED"
    AUTH_STARTED = "AUTH_STARTED"
    WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
    AUTH_SUCCEEDED = "AUTH_SUCCEEDED"
    TAXPAYER_CONFIRMED = "TAXPAYER_CONFIRMED"
    SESSION_CANCELLED = "SESSION_CANCELLED"
    SESSION_FAILED = "SESSION_FAILED"
    LOGOUT_OBSERVED = "LOGOUT_OBSERVED"
    SESSION_CLEANUP_COMPLETED = "SESSION_CLEANUP_COMPLETED"
```

**Ratificado en Domain C.1 (Gate 4)** — Domain C, al implementar los
servicios reales, encontró 4 transiciones de seguridad/forense sin
ningún valor de los 11 anteriores que las cubriera razonablemente.
Ampliación **aditiva**, sin migración (`AuditLog.action` es
`String(60)` sin `CheckConstraint` — no hay DDL que tocar):

```python
    # ---- Fase 5, Bloque 5.2, Domain C.1 (ratificado, aditivo, sin DDL) ----
    PAIRING_COMPLETED = "PAIRING_COMPLETED"
    PAIRING_LOCKED = "PAIRING_LOCKED"
    AGENT_REVOKED = "AGENT_REVOKED"
    CREDENTIAL_DELIVERY_GATE_CONSUMED = "CREDENTIAL_DELIVERY_GATE_CONSUMED"
```

**Semántica exacta de `CREDENTIAL_DELIVERY_GATE_CONSUMED`** (Domain
C.1, sección 9 del encargo) — significa **únicamente** que el gate
transaccional de "a lo sumo una entrega de credencial por sesión" fue
consumido (`credential_delivered_at` poblado). **Nunca** significa que
la credencial en claro llegó al agente por la red — el transporte real
(broker/mTLS/Windows Agent) pertenece a un dominio posterior fuera de
alcance de Domain C, y puede fallar incluso después de que este evento
se emita. Ningún lector de `audit_logs` debe interpretar este evento
como confirmación de entrega de red.

El resto del ciclo de vida (creación/cancelación/expiración de
pairing, creación/completado/fallo/expiración de job, expiración de
sesión) queda **sin** `AuditAction` dedicado — decisión deliberada:
esas filas ya llevan su propio `status`/`terminal_reason_code`/
timestamps trazables en su propia tabla.

Payload (`AuditLog.log_metadata`, columna `metadata` JSONB): solo IDs
internos (`session_id`, `job_id`, `agent_id`), `status`,
`terminal_reason_code`, timestamps. **Nunca**: RUC, username,
password, cookies, token, contenido de documento.

`AuditLog` (reservado hoy para "actuaciones dentro de una sesión
autenticada" según el comentario del modelo) es el destino correcto —
`security_event.py` (login/auth del **producto**, no de SUNAT) no
aplica aquí. **No se implementa en Domain A** — es diseño de enum, no
código.

### Transaccionalidad de la auditoría (Domain A.1, sección 37)

**Congelado**: los eventos críticos
(`SOL_SESSION_CREATED`/`JOB_CLAIMED`/`SESSION_CANCELLED`/
`SESSION_FAILED`/`SESSION_SUCCEEDED` y análogos) se escriben en la
**misma transacción de base de datos** que la transición de estado que
los origina — **no** un patrón outbox/best-effort. Justificación:
`audit_logs` es una tabla más de la misma base PostgreSQL que
`sol_access_sessions`/`sol_agent_jobs` — no hay frontera de sistema
distribuido que forzar a un patrón outbox (ese patrón resuelve el
problema de escribir de forma consistente *a través* de sistemas
distintos, que no es el caso aquí). No se encontró en el repo un
patrón de outbox existente que debiera reutilizarse; el propio
`audit_service.py` productivo ya escribe dentro de la sesión de
SQLAlchemy de la request, lo cual — a confirmar en Domain B — ya
implica la misma transacción por defecto.

## 8. Logging

**Redacción de secretos obligatoria.** Prohibido en cualquier log:

```
credential username (si se considera sensible)
password
job token (plano)
cookies
Authorization
request body con auth
OAuth state
valores de storage del navegador
```

**Allowlist, no denylist**: el logging estructurado de Block 5.2 debe
declarar explícitamente qué campos SÍ se registran (mismo principio
que `observer/network_logger.py` de Spike A — la firma de `record()`
estructuralmente no tiene parámetros para query/body/headers/cookies,
en vez de intentar filtrarlos después de capturarlos). Un campo nuevo
que no está en la allowlist nunca se registra por accidente.

## 9. Superficie de error de la API

Las respuestas de API **nunca** deben incluir:

```
excepción cruda de Playwright
URL cruda del navegador
query string
valores de credenciales
secreto del agente
datos privados de certificado
```

- Errores externos (hacia el cliente/frontend): **códigos de error
  estables** (`terminal_reason_code`, `BLOCK5_2_STATE_MACHINE.md`
  sección 10).
- Detalles internos: sanitizados antes de cualquier log/respuesta —
  mismo principio de sanitización ya validado exhaustivamente en Spike
  A (`observer/sanitizer.py`).

## 10. Retención de datos

**Se persiste**:
- Metadata de sesión (`sol_access_sessions` — IDs, timestamps, status,
  scope).
- `operation_result`/`terminal_reason_code`.
- Eventos de auditoría (sección 7).
- Identidad de agente (`sol_agents`).

**No se persiste**:
```
contenido del navegador
cookies de sesión SUNAT
credenciales
DOM
screenshots
HAR
trace
storageState
```

**No se almacenan snapshots de evidencia en producción inicialmente**
— a diferencia del spike (que sí capturaba metadata de red sanitizada
con fines de investigación), el producto de Domain A en adelante no
tiene ese requisito — si se necesitara en el futuro, sería una
decisión explícita y su propio ADR, con el mismo rigor de sanitización
que Spike A ya demostró ser alcanzable.

## 11. Reconexión, `expected_version` y atomicidad de pairing (Domain A.1)

### `expected_version` — alcance uniforme (sección 51)

Las **cuatro** entidades nuevas llevan `version` (`sol_access_sessions`,
`sol_agents`, `sol_agent_jobs`, `sol_agent_pairings`) — todas reciben
mutaciones concurrentes potenciales. Estrategia uniforme: cada `UPDATE`
mutante se condiciona **tanto** al estado/status esperado **como** al
`version` esperado cuando el llamador lo conoce (defensa en
profundidad) — para `sol_agent_jobs` el predicado de estado
(`status='PENDING'`, sección "Claim atomicity") ya es suficiente por sí
solo dado que las transiciones son naturalmente excluyentes, pero
`version` se mantiene de todas formas por uniformidad entre las 4
tablas, no porque cada una lo necesite con la misma urgencia.

### Broker disconnect — transitorio vs. muerte del agente (sección 42)

Se distinguen dos casos, nunca tratados igual:

- **Desconexión transitoria de transporte**: el agente sigue vivo,
  pierde la conexión mTLS brevemente y reconecta dentro de una
  **ventana de recuperación** acotada (segundos, valor exacto en
  Domain B). Al reconectar con su identidad mTLS existente, el agente
  puede **resumir** el reporte de su job `CLAIMED` sin que el backend
  lo haya marcado `FAILED`/`EXPIRED` prematuramente.
- **Muerte del agente**: no hay reconexión dentro de la ventana — el
  job sigue su curso normal de expiración/TTL (secciones anteriores),
  nunca ejecución duplicada, porque el estado en BD (no la memoria
  local del agente) es la única fuente de verdad que un agente
  reconectado debe reconciliar.

### Pairing → agente, atomicidad (sección 43)

Al confirmar un pairing (`status → COMPLETED`), debe **crearse o
activarse exactamente un `sol_agents`** — nunca queda un pairing
`COMPLETED` sin un agente correspondiente. Se ejecuta en la **misma
transacción**: el `INSERT` en `sol_agents` (o la reactivación de uno
existente si el flujo lo permitiera) y el `UPDATE` de
`sol_agent_pairings.status='COMPLETED'` +
`confirmed_agent_id=<nuevo_id>` ocurren juntos o ninguno ocurre.

## 12. Threat model mínimo

Para cada amenaza: prevención, detección, recuperación, riesgo
residual.

| # | Amenaza | Prevención | Detección | Recuperación | Riesgo residual |
|---|---|---|---|---|---|
| T1 | Robo de credencial en tránsito | mTLS obligatorio, sin HTTP plano | Fallo de handshake TLS detectado por el broker | Rechazar conexión, `BROKER_DISCONNECTED` | Bajo — depende de la corrección de la implementación mTLS (Domain B/C) |
| T2 | Persistencia de credencial en el agente | Nunca escribir a disco/env/logs; entrega puntual (sección 1) | Barrido estático de código (mismo patrón que Spike A) | N/A — es prevención estructural | Medio — Python no garantiza zeroización de memoria (sección 1) |
| T3 | Agente malicioso/revocado | `sol_agents.status`, verificado en cada claim/heartbeat (sección 5) | Certificado no coincide con `certificate_fingerprint` activo | Revocar, rechazar todo claim futuro | Bajo |
| T4 | Replay de job | Un solo uso, hash del token, binding job+agent+session (sección 5) | `JOB_REPLAY_DETECTED` en el segundo intento | Rechazar, auditar | Bajo |
| T5 | Asignación de job cross-tenant | FKs compuestas tenant-safe (sección 6) | Constraint de BD lo impide estructuralmente | N/A | Bajo |
| T6 | Residuo de sesión de navegador | `BrowserContext` efímero, cleanup verificado (Arquitectura sección 8) | `cleanup-check` (patrón Spike A) | Reintentar limpieza, `CLEANUP_FAILED` si persiste | Bajo-Medio (mismo residual que Spike A documentó para A1) |
| T7 | Cambio de portal SUNAT | `PORTAL_CHANGED` fail-closed (Arquitectura sección 7) | Host/estructura no coincide | Abort inmediato, sin heurística | Bajo-Medio (Spike A `RISK_REGISTER.md` T11) |
| T8 | Contexto de contribuyente incorrecto | Confirmación humana obligatoria (State Machine sección 5) | Respuesta "No" del humano | `WRONG_TAXPAYER_CONTEXT`, fail closed | Medio — depende de percepción humana, sin validación automática (Spike A T9, residual MEDIA sin reducir) |
| T9 | Tentación de automatizar el challenge | `HUMAN_ONLY` estructural, sin API de solver | Barrido estático (ninguna dependencia de OCR/solver) | N/A | Bajo |
| T10 | Agente crashea tras recibir credencial | Sin re-entrega automática (sección 4) | Heartbeat ausente | Nuevo job, nueva autorización | Medio — ventana de vida de la credencial en memoria del agente crasheado, mitigada por el alcance mínimo del envelope (sección 3) |
| T11 | Crash del broker | Reconexión iniciada por el agente (nunca al revés) | Heartbeat/conexión perdida | Sesión pasa a `FAILED`/`BROKER_DISCONNECTED`, nunca queda colgada indefinidamente (TTL) | Bajo |
| T12 | Carrera de cancelación | Idempotencia de cancel (State Machine sección 6) | — | Terminal ya alcanzado gana, cancel es no-op | Bajo |
| T13 | Ejecución duplicada | Un solo claim exitoso (sección 5), idempotency keys en mutaciones (`BLOCK5_2_API_CONTRACTS.md`) | Constraint único de BD | Rechazar el segundo intento | Bajo |
| T14 | Violación de la frontera del Buzón | `SOL_ACCESS_SCOPE` cerrado, sin endpoints/modelos de Buzón en el código (Arquitectura sección 11) | Revisión de código / tests estructurales | N/A — prevención por ausencia de capacidad | Bajo, mientras el scope permanezca cerrado |
| T15 | Logging sensible | Allowlist de logging (sección 8) | Revisión de código | N/A | Bajo |
| T16 | Compromiso local de Windows (la máquina del agente) | Fuera del control del backend — mitigado por "nunca persistir, nunca zeroización garantizada" (sección 1) | Fuera de alcance de detección remota | Revocar el agente (`sol_agents.status=REVOKED`) | **Medio-Alto** — riesgo estructural inherente a cualquier agente de escritorio; no eliminable solo con diseño de backend |
| T17 | Efecto invisible server-side en SUNAT | Fuera de alcance mientras `SOL_ACCESS_SCOPE=AUTHENTICATE_TO_LANDING_ONLY` (el Buzón, donde se validó esta limitación, está prohibido) | N/A en este scope | N/A | Documentado, no mitigable desde el cliente (Spike A A2-TD: `INVISIBLE_SERVER_SIDE_MUTATION_DETECTION_LIMIT=VALIDATED`) — relevante solo si un scope futuro habilita el Buzón |
| T18 | Robo de certificado de agente | mTLS + certificado ligado a `certificate_fingerprint` revisado en cada conexión | Fingerprint no coincide | Revocar y re-parear | Medio — depende de la seguridad del almacenamiento de clave privada en Windows (fuera de alcance de Domain A) |
| T19 | Fuerza bruta de verificación de código dentro de un pairing | TTL 120s + máximo 5 fallos + lockout permanente del intento (Arquitectura sección 5, autoridad durable en BD) **más** `PAIRING_VERIFICATION_RATE_LIMIT` (Domain C.1a — throttle process-local por `(tenant_id, pairing_id)`, `sol_pairing_verification_throttle.py`, ventana mínima acotada entre intentos; nunca consume el contador durable, nunca revela si un código presentado durante el throttle era correcto). **Domain C.1b (resource safety)**: el throttle solo registra estado para un `pairing_id` ya confirmado existente/tenant-safe/`PENDING`/no vencido — el *lookup* (`SELECT ... FOR UPDATE` tenant-scoped) ocurre **antes** del registro, nunca al revés, así que un `pairing_id` arbitrario o inexistente nunca crea entrada. Además, con independencia de esa disciplina de llamada: purga perezosa de entradas caducadas (sin scheduler) y una cota dura de cardinalidad (`_MAX_ENTRIES`) que el diccionario nunca puede superar — el estado nunca depende exclusivamente de que los llamadores se comporten bien. Cada transición terminal del pairing (`COMPLETED`/`LOCKED`/`CANCELLED`/`EXPIRED`) evicta explícitamente su propia entrada | Contador de fallos (durable) | Requiere iniciar un pairing nuevo | Bajo |
| T20 | Agente obsoleto/stale | `last_seen_at` derivado (ver `BLOCK5_2_DATA_MODEL_DRAFT.md`) | Ausencia de heartbeat reciente | Tratar como no disponible para nuevos jobs | Bajo |
| T21 | Disclosure de BD mientras un pairing está `PENDING` (**Domain A.3**) | `pairing_code_verifier` = HMAC-SHA-256 keyed con `PAIRING_VERIFIER_KEY` fuera de PostgreSQL (`BLOCK5_2_DATA_MODEL_DRAFT.md` sección 4) — distinto de un verificador *salted* convencional: un atacante con **solo** un snapshot/backup/réplica de la BD no puede validar ningún candidato de los 10⁶ códigos posibles sin la clave, que nunca reside en la fila ni en ninguna tabla | Auditoría de acceso a BD, rotación operacional de `PAIRING_VERIFIER_KEY` | Rotar la clave (ventana de *drain*, sección "Key rotation" del modelo de datos); los pairings `PENDING` en vuelo expiran por TTL de 120s | **No es riesgo cero**: un atacante que compromete **tanto** la BD **como** el proceso backend/entorno (donde vive `PAIRING_VERIFIER_KEY`) sigue pudiendo enumerar offline — el keyed verifier eleva el listón de "solo BD" a "BD + backend", no lo elimina en un compromiso total. Complementa, no sustituye, los controles online de T19 (TTL/máx. fallos) — defensa en profundidad, ninguno de los dos se relaja por la existencia del otro |
| T22 | Abuso de creación de pairings nuevos para resetear el presupuesto de 5 fallos (Domain C.1) | `PAIRING_CREATION_RATE_LIMIT` (Domain C.1 — `sol_agent_pairing_rate_limit_service.py`, basado en BD, conteo de filas de `sol_agent_pairings` por `(tenant_id, requested_by_user_id)` desde el último `COMPLETED`, backoff exponencial igual que `login_attempt_service.py`) — **distinto** de `PAIRING_VERIFICATION_RATE_LIMIT` (T19): este protege la *creación* de registros nuevos, no los intentos de código dentro de uno ya existente | Conteo de filas recientes | Backoff exponencial hasta `pairing_creation_lockout_max_minutes` | Bajo — durable (sobrevive reinicio del proceso, a diferencia de T19 verification throttle) |

### Domain E1 — Entrega de secreto (arquitectura, ver `BLOCK5_2_SECRET_DELIVERY_ARCHITECTURE.md`)

Las siguientes amenazas continúan la misma tabla — corresponden a la
**arquitectura congelada** de Domain E1-A, no a código productivo
todavía (ver el documento dedicado para el contrato completo).

| T23 | Proceso local no confiable golpea el broker loopback | Bind exclusivo a `127.0.0.1` (Domain D, verificado por socket real), defensas Host/Origin/Sec-Fetch-Site/header de protocolo (Domain D.1) aplican también al futuro endpoint de secreto sin excepción | Igual que el resto del broker | Igual que el resto del broker | Bajo — ninguna superficie nueva, hereda las mismas defensas ya verificadas |
| T24 | Petición estilo navegador/DNS rebinding contra el endpoint de secreto | Mismas defensas Gate 3 de Domain D.1 (`_reject_browser_like_request`), sin excepción por endpoint | Igual | Igual | Bajo |
| T25 | `connection_id` robado/adivinado | Insuficiente por sí solo — sección 36 del encargo E1-A: exige además mTLS + agente ACTIVE + job reclamado + grant válido + vault desbloqueada + gate de BD | El registro nunca expone el `connection_id` fuera del proceso salvo en el header de la propia respuesta de `HELLO` sobre mTLS ya autenticado | Revocar/reautenticar | Bajo, condicionado a que ningún chequeo futuro se relaje a "solo `connection_id`" |
| T26 | Certificado de agente robado | mTLS + huella conocida + agente ACTIVE — mismas defensas ya congeladas en Domain D; un secreto liberado a un certificado robado pero válido es indistinguible de un agente legítimo comprometido (ver T42) | Detección de uso anómalo fuera de alcance de E1 | Revocar el agente | Medio — mismo residual ya documentado en T18, ahora con consecuencia potencial mayor (secreto SOL, no solo transporte) |
| T27 | Certificado duplicado en dos procesos | `ONE_LIVE_LOGICAL_CONNECTION_PER_AGENT` (Domain D.1) + lease/pin de conexión (sección 8 del documento de arquitectura de E1) cierran la ventana específica de entrega de secreto | `test_broker_logical_connection_identity.py::test_C` ya prueba el reemplazo; E1-B añade la prueba N/O sobre el pin | Conexión obsoleta rechazada determinísticamente | Bajo, condicionado a que el pin de E1-B se implemente exactamente como se congeló |
| T28 | Agente revocado en carrera con la solicitud de secreto | Fail-closed explícito (sección 16.2 del documento de arquitectura) — nunca enviar tras conocerse la revocación | Prueba F/O del plan de pruebas E1-B | Nueva sesión si el gate ya se consumió | Bajo |
| T29 | Sesión cancelada/vencida en carrera | BD de Domain C como autoridad; revalidación antes de la escritura de red (sección 16.3) | Prueba G/H/P del plan de pruebas E1-B | Nueva sesión si el gate ya se consumió | Bajo |
| T30 | Tenant/job/sesión incorrectos | Identidad solo por huella mTLS, nunca por campo de protocolo; relaciones cargadas tenant-safe desde el backend (mismo patrón ya congelado en Domain D sección 35 del encargo original) | Constraints de FK compuestas + pruebas D/V | Rechazo genérico | Bajo |
| T31 | Solicitudes de entrega concurrentes duplicadas | Gate de BD de Domain C (`ux_sol_agent_jobs_one_delivery_per_session`) como única autoridad — nunca un lock de Python | Prueba K (dos conexiones PostgreSQL reales) | Rechazo genérico para el segundo | Bajo |
| T32 | Crash del broker antes del gate | Sin plaintext observable, grant no sobrevive (process-local) | Prueba Q | Reautorización humana | Bajo |
| T33 | Crash del broker después del gate | Gate consumido, sin redelivery automática | Prueba R | Nueva sesión SOL | Bajo — conservador por diseño |
| T34 | Fallo de escritura de la respuesta | Gate ya consumido permanece consumido; sin reintento | Prueba M | Nueva sesión SOL | Bajo |
| T35 | Disclosure vía logging/errores | Allowlist estricta (sección 20 del documento de arquitectura), nunca username/password/RUC/grant/claves | Prueba S (barrido de `caplog`, mismo patrón que `test_broker_log_redaction.py`) | N/A — prevención estructural | Bajo, condicionado a disciplina de código en E1-B |
| T36 | Volcado/backup de BD | Ningún secreto en claro persiste nunca fuera de `sol_username_encrypted`/`sol_password_encrypted` ya cifrados (Bloque 5.1); el grant nunca se persiste | Prueba T | N/A | Bajo — hereda la garantía ya existente de Bloque 5.1 |
| T37 | Vault todavía bloqueada al momento de la entrega | Re-verificación obligatoria de `VaultUnlockRegistry` en el paso 6 del orden exacto, nunca solo al crear el grant | Prueba A | Fail-closed, sin decrypt | Bajo |
| T38 | Replay de un grant ya consumido | Grant eliminado irrevocablemente al consumir el gate (sección 12) | Prueba B/C tras un consumo previo | Rechazo genérico | Bajo |
| T39 | Abuso de cardinalidad del registro de grants/leases | Cota dura + purga perezosa + evicción explícita, mismo patrón ya productivo en `sol_pairing_verification_throttle.py`/`ConnectionRegistry` | Prueba U + pruebas de cardinalidad análogas a Domain C.1b/D | N/A | Bajo |
| T40 | Enrutamiento a un worker sin el `unlock`/grant correcto (multi-proceso) | `SECRET_DELIVERY_SINGLE_PROCESS_ONLY=YES` explícito (sección 6 del documento de arquitectura) — fail-closed en vez de enrutar | Mismo mecanismo ya congelado en `sol_vault_worker_gate.py` (Bloque 5.1) | Rechazo si `BACKEND_WORKERS != 1` | Bajo, heredado directamente de una invariante ya productiva |
| T41 | Residuo de memoria del texto plano | Sin garantía física de zeroización para `str` inmutables (sección 10 del documento de arquitectura) — minimización de tiempo de vida como mitigación de mejor esfuerzo | No testeable de forma determinista en CPython | N/A | **Medio** — riesgo residual explícitamente no eliminado, documentado sin eufemismos |
| T42 | Agente legítimo comprometido después de una entrega ya autorizada | Ninguna — fuera del control del backend una vez que el secreto llegó de forma legítima (sección 22 del documento de arquitectura) | Fuera de alcance de detección remota | Revocar el agente para futuras entregas; no revierte la ya ocurrida | **Medio-Alto** — riesgo estructural inherente, mismo principio ya honesto que T16 |
