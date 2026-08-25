# Block 5.2 — Máquina de estados de la sesión SOL supervisada

**Domain A — Architecture & Contract Freeze**, congelado en **Domain
A.1 — Persistence & Concurrency Contract Freeze**. Complementa
`BLOCK5_2_ARCHITECTURE.md` y `BLOCK5_2_DATA_MODEL_DRAFT.md`. Sigue el
patrón ya productivo del repo (`Declaration.workflow_status`,
`backend/app/services/declaration_service.py`, ADR-0018/ADR-0022):
enum `str` en el modelo, tabla de transiciones explícita como
`dict[str, set[str]]` en el servicio, `CheckConstraint` en la columna
`String` de la BD (no un tipo `Enum` de SQLAlchemy), bloqueo optimista
por `version`.

**Corrección de nombre (Domain A.1, sección 38)**: el campo antes
llamado `failure_code` se renombra a **`terminal_reason_code`** —
más preciso, porque también se puebla en `CANCELLED`/`EXPIRED`, que no
son "fallos". Ver sección 10.

## 1. Estados — exactamente estos 9, ninguno más

```
CREATED
WAITING_FOR_AGENT
WAITING_FOR_USER
IN_PROGRESS
SUCCEEDED
PARTIAL_FAILURE
FAILED
CANCELLED
EXPIRED
```

No se añade ningún estado sin justificación expresa en una ronda
posterior — la tabla de abajo es exhaustiva.

| Estado | Terminal | Significado | Quién produce la transición | Precondiciones |
|---|---|---|---|---|
| `CREATED` | No | Sesión solicitada, sin agente asignado | Backend (al recibir `POST /sol-access/sessions`) | Tenant/cliente/credential_id válidos y tenant-scoped; usuario con `sol_session:create` |
| `WAITING_FOR_AGENT` | No | Esperando que el agente asignado reclame su job | Backend (tras `CREATED`, al crear el job dirigido al agente) | Existe al menos un agente `ACTIVE` del tenant, o se acepta cola sin agente disponible (ver `BLOCK5_2_API_CONTRACTS.md`) |
| `WAITING_FOR_USER` | No | Bloqueado en una acción humana (login/challenge/confirmación de contribuyente) | Agente (al reportar el punto de bloqueo) | Job reclamado y en ejecución |
| `IN_PROGRESS` | No | Flujo activo, sin bloqueo humano pendiente en este instante | Agente (heartbeats/reportes de avance) | Job reclamado |
| `SUCCEEDED` | **Sí** | La sesión SOL supervisada **terminó dentro del alcance autorizado** (`AUTHENTICATE_TO_LANDING_ONLY`) | Agente (reporte final) → backend valida y persiste | Logout observado + `context.close()` + cleanup verificado |
| `PARTIAL_FAILURE` | **Sí** | La acción principal ocurrió, pero algo posterior al éxito principal falló — ver sección 9 | Agente/backend | Ver matriz exacta, sección 9 — no es catch-all |
| `FAILED` | **Sí** | El flujo no pudo completarse dentro del alcance autorizado | Agente/backend, con `terminal_reason_code` obligatorio | Ver enum de códigos, sección 10 |
| `CANCELLED` | **Sí** | Cancelado explícitamente (usuario, backend, agente, o cierre de navegador) | Cualquiera de las fuentes de cancelación (sección 7) | — |
| `EXPIRED` | **Sí** | Venció sin que el humano completara la acción pendiente | Backend (job scheduler/TTL check) | TTL de sesión o de job vencido en `WAITING_FOR_USER`/`WAITING_FOR_AGENT` |

## 2. Transiciones permitidas

```
CREATED            -> WAITING_FOR_AGENT, CANCELLED, EXPIRED
WAITING_FOR_AGENT   -> IN_PROGRESS, CANCELLED, EXPIRED, FAILED
IN_PROGRESS         -> WAITING_FOR_USER, SUCCEEDED, PARTIAL_FAILURE, FAILED, CANCELLED
WAITING_FOR_USER     -> IN_PROGRESS, CANCELLED, EXPIRED, FAILED
SUCCEEDED            -> (terminal, sin salida)
PARTIAL_FAILURE      -> (terminal, sin salida)
FAILED                -> (terminal, sin salida)
CANCELLED             -> (terminal, sin salida)
EXPIRED                -> (terminal, sin salida)
```

Ningún estado terminal admite reingreso — una sesión `EXPIRED` (o
cualquier otro terminal) **no puede revivirse**; el cliente debe crear
una sesión nueva (ver `BLOCK5_2_API_CONTRACTS.md` sección 7 — "ningún
`GET` muta" también aplica aquí: reactivar una sesión terminal nunca
es un side effect implícito de una consulta).

**Congelado sin excepción (Domain A.1, sección 5)**: los 5 estados
terminales (`SUCCEEDED`, `PARTIAL_FAILURE`, `FAILED`, `CANCELLED`,
`EXPIRED`) **nunca** admiten ninguna transición de salida — siempre,
sin excepción, no solo "por defecto". Esto se aplica a nivel de
mecanismo, no solo de convención: toda mutación de `status` está
condicionada, en el `UPDATE`, a que el estado actual sea uno de los 4
no-terminales (`CREATED`, `WAITING_FOR_AGENT`, `WAITING_FOR_USER`,
`IN_PROGRESS`) — un `UPDATE` que llega tarde contra una fila ya
terminal simplemente no afecta ninguna fila (`rowcount == 0`), nunca
sobrescribe (ver `BLOCK5_2_SECURITY_MODEL.md`, matriz de carreras).

## 3. `session_status` ≠ `operation_result` ≠ `terminal_reason_code`

Tres conceptos deliberadamente separados — nunca colapsados en un
único campo:

| Campo | Qué responde | Valores |
|---|---|---|
| `session_status` | ¿En qué punto de la máquina de estados está la sesión? | Los 9 estados de la sección 1 |
| `operation_result` | ¿Qué se logró desde el punto de vista de negocio? (nullable) | `LANDING_REACHED` — único valor hoy, ver sección 4 |
| `terminal_reason_code` | ¿Por qué terminó así, si no fue `SUCCEEDED` limpio? | Enum cerrado, ver sección 10 |

**`SUCCEEDED` significa únicamente**: *"la sesión SOL supervisada
terminó correctamente dentro del alcance expresamente autorizado"*.

**`SUCCEEDED` NO significa**:
- Buzón leído.
- Trámite ejecutado.
- Documento obtenido.
- Declaración presentada.
- Operación tributaria completada.

## 4. `operation_result` — valores exactos (Domain A.1, congelado)

**Resuelto, no dejado como pregunta abierta**: para el alcance inicial
(`AUTHENTICATE_TO_LANDING_ONLY`), `operation_result` tiene **un único
valor útil hoy**:

```
LANDING_REACHED
```

**Por qué no `LANDING_NOT_REACHED`**: sería puramente redundante con
`status=FAILED` + `terminal_reason_code` — el enum se mantiene
deliberadamente mínimo en vez de agregar un valor que no aporta
información nueva ("evitar diseñar un enum vacío o redundante").

**Cuándo se puebla `LANDING_REACHED`** — la regla es **ortogonal a
`status`**, no un simple espejo de `SUCCEEDED`: se establece siempre
que el landing (`e-menu.sunat.gob.pe`) fue empíricamente alcanzado y
el contribuyente confirmado, **incluso si la sesión termina en
`PARTIAL_FAILURE`** (el problema parcial ocurrió *después* de alcanzar
el resultado de negocio, no antes — ver la matriz de la sección 9).
Queda `NULL` en `FAILED`/`CANCELLED`/`EXPIRED` cuando el landing nunca
se confirmó.

El campo existe con vistas a scopes futuros (`BUZON`, etc., **no
habilitados**, ver `BLOCK5_2_ARCHITECTURE.md` sección 2) — no porque
el alcance actual tenga múltiples resultados de negocio posibles hoy.

## 5. Human challenge (`WAITING_FOR_USER`, parte 1)

```
HUMAN_ONLY
```

Ante cualquier challenge (CAPTCHA, 2FA, verificación adicional de
SUNAT): `session_status = WAITING_FOR_USER`. **Nunca**: OCR, solver de
IA, solver de CAPTCHA, solver de terceros, bypass anti-bot. El usuario
lo resuelve visualmente en el Chromium visible; el agente únicamente
espera (mismo principio que `observer/manual_auth.py` de Spike A: el
agente nunca lee ni completa el campo).

Debe existir, en todo `WAITING_FOR_USER`:
- **Timeout** — vence a `EXPIRED` (o `FAILED` con
  `terminal_reason_code=HUMAN_CHALLENGE_TIMEOUT` si el timeout ocurre
  en medio de un intento activo — a definir el criterio exacto en
  Domain B).
- **Cancel** explícito — transición a `CANCELLED`.
- **Detección de cierre del navegador** — mismo patrón que Spike A
  (`browser.is_connected() == False` → transición segura, nunca hang).

## 6. Confirmación humana de contribuyente (`WAITING_FOR_USER`, parte 2)

Al llegar a `e-menu.sunat.gob.pe`, el sistema **debe detenerse** y
solicitar confirmación humana:

> "¿Confirmas que estás en el contribuyente/cuenta correcta?"

**Nunca** se pide RUC, nombre, ni razón social para esta confirmación
— es una pregunta binaria pura (mismo patrón que
`observer/wrong_taxpayer_guard.py` de Spike A).

- **Sí** → continuar únicamente hacia finalización/logout, dentro del
  `SOL_ACCESS_SCOPE` vigente.
- **No** → `terminal_reason_code = WRONG_TAXPAYER_CONTEXT` →
  `session_status = FAILED` (**nunca `PARTIAL_FAILURE`** — Domain A.1
  sección 39: aunque la autenticación técnica ya ocurrió, el scope no
  se considera exitoso porque la identidad objetivo no fue confirmada)
  → fail closed. Logout/cleanup siguen siendo obligatorios después,
  igual que en cualquier otro camino de cierre.

## 7. Cancelación

Fuentes válidas de cancelación:

```
usuario (vía API)
backend (timeout administrativo)
agente (ej. el agente detecta un error irrecuperable local)
cierre del navegador
apagado del sistema (agente)
```

- **Idempotente**: cancelar una sesión ya `CANCELLED` (o cualquier
  terminal) es un no-op exitoso, nunca un error — salvo que ya exista
  un estado terminal **distinto** previo, en cuyo caso la cancelación
  no puede sobrescribirlo (un `SUCCEEDED` no se convierte en
  `CANCELLED` a posteriori).
- Al cancelar, se debe cerrar (best effort, pero **fail closed** si no
  se puede verificar): `BrowserContext`, el job asociado, y descartar
  cualquier referencia a secretos en memoria del agente.
- **Cancel vs. claim/success/timeout**: mecanismo exacto y matriz de
  carreras — `BLOCK5_2_SECURITY_MODEL.md`.

## 8. Expiración

Tres TTL distintos, **nunca mezclados**:

```
session_ttl   -- vida máxima de una sol_access_session completa
job_ttl       -- vida máxima de un sol_agent_job individual (más corto que session_ttl)
pairing_ttl   -- 120s, ver BLOCK5_2_ARCHITECTURE.md sección 5 (independiente de los dos anteriores)
```

`EXPIRED` es siempre terminal. Una sesión expirada **no puede
revivirse** — se debe crear una sesión nueva. Comparaciones de
expiración siempre usan tiempo de base de datos, nunca el reloj del
agente (`BLOCK5_2_DATA_MODEL_DRAFT.md` sección 5).

## 9. `PARTIAL_FAILURE` — semántica precisa y matriz exacta (Domain A.1, sección 7)

**No es un cajón de sastre para "algo salió mal de forma ambigua".**
Semántica congelada: *la acción principal autorizada dentro del scope
ocurrió (landing alcanzado + contribuyente confirmado), pero un paso
posterior de cierre/auditoría tuvo un fallo parcial que no invalida el
resultado principal* — **y, crucialmente, sin que exista riesgo de una
sesión de navegador activa sin control** (regla dura: si ese riesgo
existe, el resultado es `FAILED`, nunca `PARTIAL_FAILURE`).

### Matriz de decisión

| Landing + taxpayer confirmados | `context.close()` confirmado | Logout observado | Cleanup verificado | Audit final escrito | Resultado |
|---|---|---|---|---|---|
| No | — | — | — | — | `FAILED` (nunca `PARTIAL_FAILURE`) |
| Sí, pero taxpayer=No | — | — | — | — | `FAILED` con `WRONG_TAXPAYER_CONTEXT` (sección 6) |
| Sí | Sí | Sí | Sí | Sí | `SUCCEEDED` |
| Sí | Sí | Sí | Sí | **No** (falla solo la escritura de auditoría) | `PARTIAL_FAILURE` |
| Sí | Sí | Sí | **No** (residuo detectado / verificación falla) | — | `PARTIAL_FAILURE` (`terminal_reason_code=CLEANUP_FAILED`) |
| Sí | **No confirmado** (agente crasheó antes de reportar el cierre) | — | — | — | **`FAILED`** (`BROWSER_CLOSED` o `AGENT_CRASHED`) — riesgo de sesión de navegador activa sin control, la regla dura de arriba se aplica |
| Sí | Sí | **No observado** (navegador se cerró sin un logout explícito capturado, pero `context.close()` SÍ se confirmó) | — | — | `PARTIAL_FAILURE` — el contexto está confirmado cerrado, no hay riesgo activo, solo falta la confirmación del paso de logout en sí |
| Sí | — (no se puede confirmar de forma independiente, ej. reporte final del broker no llegó) | — | — | — | `FAILED` por defecto (fail-closed) — **nunca** se asume `PARTIAL_FAILURE` cuando la evidencia de cierre es incompleta |

**Regla dura, sin excepción**: la condición necesaria para
`PARTIAL_FAILURE` es que `context.close()`/`browser.close()` estén
**confirmados** — si esa confirmación falta, siempre es `FAILED`,
nunca `PARTIAL_FAILURE`, sin importar qué tan bien haya ido el resto
del flujo.

**Restricción explícita**: si no puede definirse con esta misma
precisión un caso nuevo de `PARTIAL_FAILURE` en una ronda futura, ese
caso debe clasificarse como `FAILED` (con su propio
`terminal_reason_code`) en vez de forzarlo dentro de
`PARTIAL_FAILURE`. `PARTIAL_FAILURE` no crece por conveniencia.

## 10. `terminal_reason_code` — enum congelado (Domain A.1, sección 38 — renombrado desde `SolAccessFailureCode`)

Independiente de `session_status` — se puebla en `FAILED`,
`PARTIAL_FAILURE`, `CANCELLED` y `EXPIRED` (nunca en `SUCCEEDED`
limpio). Mismo patrón organizativo que `AuditAction`/`Permission`
(enum plano `SCREAMING_SNAKE`, agrupado por bloque con comentario
banner):

```python
class SolAccessTerminalReasonCode(str, enum.Enum):
    # ---- Fase 5, Bloque 5.2 ----
    AGENT_UNAVAILABLE = "AGENT_UNAVAILABLE"
    PAIRING_REQUIRED = "PAIRING_REQUIRED"
    AGENT_REVOKED = "AGENT_REVOKED"
    JOB_EXPIRED = "JOB_EXPIRED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    JOB_REPLAY_DETECTED = "JOB_REPLAY_DETECTED"
    CREDENTIAL_UNAVAILABLE = "CREDENTIAL_UNAVAILABLE"
    VAULT_LOCKED = "VAULT_LOCKED"
    VAULT_RECOVERY_REQUIRED = "VAULT_RECOVERY_REQUIRED"
    AUTH_HOST_CHANGED = "AUTH_HOST_CHANGED"
    WRONG_LOGIN_MODE = "WRONG_LOGIN_MODE"
    AUTH_FAILED = "AUTH_FAILED"
    HUMAN_CHALLENGE_TIMEOUT = "HUMAN_CHALLENGE_TIMEOUT"
    WRONG_TAXPAYER_CONTEXT = "WRONG_TAXPAYER_CONTEXT"
    PORTAL_CHANGED = "PORTAL_CHANGED"
    BROWSER_CLOSED = "BROWSER_CLOSED"
    CANCELLED_BY_USER = "CANCELLED_BY_USER"
    CLEANUP_FAILED = "CLEANUP_FAILED"
    BROKER_DISCONNECTED = "BROKER_DISCONNECTED"
    AGENT_CRASHED = "AGENT_CRASHED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
```

**20 valores** (19 originales + `SESSION_EXPIRED`, añadido en Domain
A.1: el `EXPIRED` de una *sesión* que nunca llegó a tener un job — ej.
atascada en `WAITING_FOR_AGENT` sin agente disponible antes de que
venza `session_ttl` — es una causa distinta de `JOB_EXPIRED`, que
describe la expiración de un job individual ya creado).

Revisión de nombres: todos describen una condición técnica/de flujo,
ninguno incluye ni referencia PII. `CANCELLED_BY_USER` ya no es
conceptualmente redundante con `status=CANCELLED` ahora que el campo
se llama `terminal_reason_code` (no "failure") — sigue existiendo como
valor propio para permitir auditar *quién/qué* originó la cancelación
cuando conviven varias causas posibles (ej. cancelación de usuario que
llega mientras el cleanup ya estaba fallando de forma independiente).
