# Block 5.2 — Contratos de API, concurrencia e idempotencia

**Domain A — Architecture & Contract Freeze**, congelado en **Domain
A.1 — Persistence & Concurrency Contract Freeze**, corregido en 3
puntos por **Domain A.2 — Final DDL Security & Compatibility Gate**:
`pairing_code_hash` renombrado a `pairing_code_verifier` (sección 3),
scope de idempotencia confirmado contra el patrón real de
`TaxPayment` (sección 7), sin cambios al listado de endpoints ni a su
scoping RBAC — y con el mecanismo exacto de la sección 3 corregido de
nuevo por **Domain A.3 — Pairing Verifier Cryptographic Freeze**: el
verificador **no** es Argon2id (esa afirmación de Domain A.2 era
incorrecta — ver `BLOCK5_2_DATA_MODEL_DRAFT.md` sección 4), es
HMAC-SHA-256 con clave servidor-side. Complementa
`BLOCK5_2_STATE_MACHINE.md` y `BLOCK5_2_SECURITY_MODEL.md`. Endpoints
**candidatos** — no implementados en esta ronda (sin FastAPI routers,
sin Pydantic schemas todavía).

## 1. Modelo de conexión agente ↔ broker

```
agent connects
  → autentica vía mTLS
  → heartbeat/disponibilidad
  → recibe su job dirigido (ya asignado a este agente, no una cola competida)
  → reclama (claim) atómicamente
  → ejecuta
  → reporta estado/eventos
  → termina o vuelve a idle
```

**Sin polling agresivo.** Frecuencia de heartbeat/polling a definir en
Domain B con un límite inferior razonable (ej. no menor a varios
segundos) — nunca un bucle ajustado sin backoff.

**Elección de transporte — a decidir en Domain B, no en esta
congelación**: candidatos son WebSocket, long-poll, o un stream
gRPC-like sobre el canal mTLS. Domain A/A.1 **no elige** todavía porque
la elección depende de decisiones de infraestructura del broker
(Docker/Linux) que exceden el alcance de un freeze de arquitectura de
producto — decisión pendiente explícita en
`BLOCK5_2_IMPLEMENTATION_PLAN.md`, sin impacto de DDL. Lo que sí se
congela: cualquiera que sea el transporte, debe soportar (a) push de
job desde broker a agente sin polling agresivo, y (b) reporte de
eventos de estado en tiempo cercano a real durante `WAITING_FOR_USER`.

## 2. Endpoints candidatos — User API (sesiones)

```
POST   /sol-access/sessions
GET    /sol-access/sessions/{id}
POST   /sol-access/sessions/{id}/cancel
```

- `POST /sol-access/sessions` — crea la sesión (`CREATED`). Requiere
  `sol_session:create`. Body candidato: `client_id`, `credential_id`,
  `scope` (fijo a `AUTHENTICATE_TO_LANDING_ONLY`), `idempotency_key`
  (UUID, ver sección 7). `tenant_id` implícito por auth.
- `GET /sol-access/sessions/{id}` — consulta de estado. Requiere
  `sol_session:read`. **Nunca** devuelve credenciales, cookies, ni
  telemetría sensible. Devuelve `session_status`, `operation_result`
  (si aplica), `terminal_reason_code` (si aplica), y — durante
  `WAITING_FOR_USER` — la acción humana requerida en términos
  genéricos.
- `POST /sol-access/sessions/{id}/cancel` — cancelación explícita.
  Requiere `sol_session:cancel`. Idempotente
  (`BLOCK5_2_STATE_MACHINE.md` sección 7).

## 3. Endpoints candidatos — Gestión de agentes y pairing

**Corrección de Domain A.1 (sección 46-47)**: la lista original de
Domain A (3 endpoints) tenía un hueco real — nada confirmaba el código
de 6 dígitos que el humano ingresa. Se amplía a **6 endpoints**:

```
GET    /sol-access/agents
POST   /sol-access/agents/pairing
GET    /sol-access/agents/pairing/{id}
POST   /sol-access/agents/pairing/{id}/confirm
POST   /sol-access/agents/pairing/{id}/cancel
POST   /sol-access/agents/{id}/revoke
```

- `GET /sol-access/agents` — lista agentes del tenant. Requiere
  `sol_agent:read`.
- `POST /sol-access/agents/pairing` — **inicia** un intento de pairing:
  genera `pairing_code_verifier` (HMAC-SHA-256 keyed con
  `PAIRING_VERIFIER_KEY`, ver `BLOCK5_2_DATA_MODEL_DRAFT.md` sección 4
  — Domain A.3, corrige Gate 2 de Domain A.2) + `expires_at` (120s),
  crea el registro `sol_agent_pairings` en `PENDING`. El código de 6
  dígitos en claro
  se devuelve **una sola vez** en la respuesta de este POST (nunca
  vuelve a estar disponible tras esta llamada — mismo principio que la
  recuperación en dos pasos de Block 5.1). Requiere `sol_agent:manage`.
- `GET /sol-access/agents/pairing/{id}` — consulta de estado de un
  intento (`PENDING`/`COMPLETED`/`LOCKED`/`EXPIRED`/`CANCELLED`), para
  que el frontend pueda reflejar el progreso. Requiere
  `sol_agent:manage`.
- `POST /sol-access/agents/pairing/{id}/confirm` — **el humano
  ingresa el código de 6 dígitos** que ve en el agente (pantalla del
  Windows nativo) aquí. Body: `{code: "123456"}`. Verifica recalculando
  el HMAC-SHA-256 sobre el mensaje canónico (dominio + `tenant_id` +
  `pairing_id` + código) y comparando contra `pairing_code_verifier`
  con `hmac.compare_digest()` (tiempo constante, librería estándar —
  nunca `==` sobre el verificador); en éxito ejecuta la transición
  atómica descrita
  en `BLOCK5_2_SECURITY_MODEL.md` sección 11 (pairing → agente en la
  misma transacción); en fallo incrementa `failed_attempt_count`
  (`LOCKED` al quinto). Requiere `sol_agent:manage`.
- `POST /sol-access/agents/pairing/{id}/cancel` — cancela un intento
  `PENDING`. Requiere `sol_agent:manage`.
- `POST /sol-access/agents/{id}/revoke` — revoca un agente. Requiere
  `sol_agent:manage`.

**Nota de diseño**: la sumisión del CSR del agente (`csr_fingerprint`)
**no** ocurre en `confirm` — es parte del contrato interno
agente↔backend (sección 4), porque el agente mismo, no un humano en el
frontend, es quien genera y somete su CSR sobre el canal ya
autenticado.

## 4. Contrato interno agente/broker — operaciones (Domain A.1, sección 48)

**Se diseña en detalle en Domain B/C** — no se expone vía la User API.
Operaciones conceptuales y qué exige cada una:

| Operación | mTLS identity | Job token | `expected_version` | Idempotencia |
|---|---|---|---|---|
| `authenticate connection` | Sí (la establece) | — | — | — |
| `heartbeat` | Sí | — | — | Implícita (coalescida, `BLOCK5_2_SECURITY_MODEL.md` sección "Heartbeat") |
| `claim job` | Sí | Sí (presentado por el agente, recibido junto con el push del job) | Sí | Estructural (predicado de estado, ver "Claim atomicity") |
| `report state` | Sí | Sí (scopa qué job) | No estrictamente necesario para un reporte simple | Sí (dedupe de reportes repetidos) |
| `request credential delivery` | Sí | Sí | Sí | Sí — gate `credential_delivered_at IS NULL`, un solo éxito posible |
| `complete job` | Sí | Sí | Sí | Sí (transición terminal idempotente) |
| `report failure` | Sí | Sí | Sí | Sí |

**Restricción dura, sin excepción**: **no existe, ni existirá, un
endpoint** `GET credential plaintext` en la User API. La única vía de
entrega de credencial es `request credential delivery` sobre el canal
interno mTLS, nunca alcanzable desde un cliente HTTP externo
autenticado como usuario de la aplicación.

## 5. Contrato de frontend (UI futura, sin implementar)

```
Cliente
  → credencial SOL
  → "Iniciar acceso supervisado"
```

Pantalla de sesión — muestra:
- `session_status` (traducido a lenguaje humano).
- Estado del agente asignado (disponible/ejecutando).
- La acción humana requerida, si `WAITING_FOR_USER`.
- Botón de cancelar.
- Resultado final (`operation_result`/`terminal_reason_code`
  traducido).

Pantalla de pairing — muestra el código de 6 dígitos recibido de
`POST /sol-access/agents/pairing` **una sola vez**, un campo para que
el humano confirme (aunque en este flujo el código lo genera el
sistema y lo muestra el AGENTE en su propia pantalla de escritorio —
el frontend web solo necesita reflejar el estado del intento y, si el
flujo lo requiere, permitir reintentar/cancelar).

**Nunca muestra**: password, token, cookies, telemetría sensible
completa. Durante `WAITING_FOR_USER`, el frontend muestra
**exactamente** la acción humana requerida — ni más contexto técnico
(URLs reales, hosts) ni menos.

## 6. Concurrencia — protecciones requeridas

| Escenario | Mecanismo |
|---|---|
| El mismo agente reclama su job dos veces (doble conexión/retry) | `UPDATE sol_agent_jobs SET status='CLAIMED' ... WHERE status='PENDING' ...` — gana exactamente un `UPDATE`; el resto ve `rowcount == 0` y trata como "ya reclamado" (ver `BLOCK5_2_SECURITY_MODEL.md`, "Claim atomicity") |
| Cancel vs. claim | Ver matriz de carreras, `BLOCK5_2_SECURITY_MODEL.md` sección 11 |
| Cancel vs. auth complete (success) | Ídem |
| Timeout vs. success | Ídem |
| Reporte de estado duplicado | Idempotency key por reporte (sección 7) — un reporte repetido es un no-op |
| Reconexión de agente | El agente reconecta con su identidad mTLS existente; jobs `CLAIMED` por él antes de la desconexión se re-sincronizan por `agent_id`, nunca se re-crean (`BLOCK5_2_SECURITY_MODEL.md`, "Broker disconnect") |
| Replay de job token | Hash verificado contra `sol_agent_jobs.job_token_hash`; el estado del job determina el rechazo |
| Reintentos duplicados de creación de sesión | `idempotency_key` del cliente en `POST /sol-access/sessions` (sección 7) |
| Reintentos duplicados de pairing | `UNIQUE(tenant_id, requested_by_user_id) WHERE status='PENDING'` — un segundo `POST /sol-access/agents/pairing` con un pairing ya pendiente falla, no crea un duplicado silencioso |

**No se confía únicamente en locks de Python** (`threading.Lock`,
`asyncio.Lock`) para nada de lo anterior — todos los mecanismos de la
tabla son a nivel de base de datos (row locks, constraints únicos,
`version`), porque el backend puede correr con múltiples
workers/procesos. **Requiere prueba real de concurrencia en dominios
posteriores** (Domain C/D) — Domain A/A.1 solo congela el mecanismo,
no lo implementa ni lo prueba con carga real.

## 7. Idempotencia — endpoints mutantes

Endpoints que **deben** definir idempotencia explícita:

```
create session
cancel session
pair agent (create)
confirm pairing
claim job
complete job
report state
```

**Formato del idempotency key — resuelto en Domain A.1 (decisión
pendiente #4 de Domain A, ahora RESOLVED)**: se reutiliza
**exactamente** el patrón ya productivo de
`app/services/tax_payment_service.py`
(`create_payment(..., idempotency_key: uuid.UUID, ...)`) — **no** un
header HTTP `Idempotency-Key` (que habría sido una convención nueva,
no alineada con el repo). El cliente provee un `UUID` como **campo del
body** de la request (`idempotency_key`); el backend lo persiste como
columna en la entidad creada
(`sol_access_sessions.idempotency_key`, `UNIQUE(tenant_id,
idempotency_key)` — `BLOCK5_2_DATA_MODEL_DRAFT.md` sección 1) y, ante
una segunda request con la misma clave:

- **Mismo payload** → devuelve la entidad existente (200/201, no-op).
- **Payload distinto** → `409` (`IdempotencyKeyConflictError`, mismo
  nombre de excepción que el patrón ya productivo).

**Confirmado en Domain A.2 (Gate 6/7)**: se releyó
`tax_payment_service.py`/`tax_payment.py` esta ronda — el scope real es
exactamente `(tenant_id, idempotency_key)` (no incluye
`requested_by_user_id`) y la detección de "mismo payload" se hace
comparando el payload entrante directamente contra las columnas de
negocio de la entidad ya persistida (`_idempotency_payload_matches`),
**sin** ninguna columna de fingerprint/hash. `sol_access_sessions`
sigue este mismo patrón sin necesitar columna adicional —
`IDEMPOTENCY_PAYLOAD_FINGERPRINT_COLUMN_REQUIRED=NO`.

Para `claim job` la idempotencia es **estructural** (constraint único
+ predicado de estado, sección 6); para
`cancel`/`complete job`/`report state`/`confirm pairing` la
idempotencia es por el estado terminal/`version` ya descrito en
`BLOCK5_2_SECURITY_MODEL.md`.

**Regla dura**: **ningún `GET` propio muta estado** — la lección
empírica de Spike A/A2-TD (`GET != READ_ONLY GUARANTEE`, escenario B
del fake portal: un `GET` puede mutar estado server-side sin que el
cliente lo vea) se aplica en sentido inverso aquí: el backend de Block
5.2 **garantiza** que sus propios `GET` nunca mutan, precisamente
porque la lección de Spike A fue sobre no poder confiar en que un
tercero (SUNAT) cumpla esa garantía — el backend propio no tiene
excusa para no cumplirla él mismo. El heartbeat, que sí muta
`last_seen_at`, es deliberadamente un `POST`/llamada interna, nunca un
`GET` (`BLOCK5_2_SECURITY_MODEL.md`, "Heartbeat y derivación
online/offline").
