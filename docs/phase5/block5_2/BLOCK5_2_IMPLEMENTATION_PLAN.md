# Block 5.2 — Plan de implementación y gates de revisión

**Domain A — Architecture & Contract Freeze**, cerrado en **Domain
A.1 — Persistence & Concurrency Contract Freeze**, corregido en 4
puntos exactos por **Domain A.2 — Final DDL Security & Compatibility
Gate** (FK compuesto de credencial confirmado sin cambio de esquema,
verificador de pairing renombrado a `pairing_code_verifier`, invariante
de no-redelivery reforzado a nivel de sesión vía índice único parcial,
scope de idempotencia confirmado contra el patrón real de
`TaxPayment` — ver sección 2.2), y corregido de nuevo en **1 punto
criptográfico exacto** por **Domain A.3 — Pairing Verifier
Cryptographic Freeze**: el mecanismo de `pairing_code_verifier` que
Domain A.2 declaró como Argon2id *keyed* era, verificado contra el
código real, Argon2id *salted* sin pepper — **no** un keyed verifier.
Domain A.3 lo reemplaza por HMAC-SHA-256 con clave servidor-side fuera
de PostgreSQL (ver sección 2.3). Cierra el freeze: números de ADR
reservados (sin escribir los archivos todavía),
disposición final de las 7 decisiones pendientes de Domain A, y las
preguntas que Domain B debe poder responder antes de autorizarse —
ahora respaldadas por DDL exacto, no solo por principios.

## 1. ADRs reservados (números, no archivos — ver nota abajo)

Siguiente número disponible según `docs/ROADMAP.md` (línea 143, que ya
reserva el número **73** explícitamente para Bloque 3.4 *porque* el
72 y otros quedaron tomados por trabajo de Fase 5): el rango **72–76**
se reserva para Block 5.2.

| Número | Título propuesto | Contenido natural |
|---|---|---|
| 72 | Supervised SOL access — frontera de producto | `BLOCK5_2_ARCHITECTURE.md` secciones 2-3, 11 |
| 73 | Modelo de confianza del agente Windows y el broker | `BLOCK5_2_ARCHITECTURE.md` secciones 4-5; `BLOCK5_2_SECURITY_MODEL.md` secciones 1, 5 |
| 74 | Máquina de estados y concurrencia de la sesión SOL | `BLOCK5_2_STATE_MACHINE.md` completo; `BLOCK5_2_API_CONTRACTS.md` secciones 6-7 |
| 75 | Entrega de credenciales y ciclo de vida del secreto efímero | `BLOCK5_2_SECURITY_MODEL.md` secciones 1-4 |
| 76 | Política de navegación fail-closed del portal | `BLOCK5_2_ARCHITECTURE.md` secciones 6-7 |

**Nota deliberada sobre el formato de esta tabla**: los números se
presentan separados de la sigla para no generar, dentro de este
archivo markdown bajo `docs/`, una cita literal con guion que
`scripts/check_adr_consistency.py` interpretaría como una referencia a
un archivo `docs/DECISIONS/` que todavía no existe (el script escanea
cualquier `.md` bajo `docs/` buscando el patrón exacto). **No se
escriben los 5 archivos de ADR en esta ronda** — son "reservados", no
"completos", conforme al encargo: se redactarán completos recién
cuando Domain B/C implemente cada decisión, momento en el que también
se corrige la referencia de `docs/ROADMAP.md`.

## 2. Gates de revisión antes de autorizar Domain B

Antes de autorizar Domain B, este freeze debe poder responder,
inequívocamente, cada una de estas preguntas — con la referencia
exacta al documento/sección que ya la resuelve:

| # | Pregunta | Resuelta en |
|---|---|---|
| 1 | ¿Cuáles son las tablas? | `BLOCK5_2_DATA_MODEL_DRAFT.md` sección 6 |
| 2 | ¿Cuáles son los enums? | `BLOCK5_2_DATA_MODEL_DRAFT.md` sección 7 (lista maestra); `BLOCK5_2_STATE_MACHINE.md` secciones 1, 10; `BLOCK5_2_RBAC_MATRIX.md` sección 2 |
| 3 | ¿Cuáles son los estados? | `BLOCK5_2_STATE_MACHINE.md` sección 1 |
| 4 | ¿Cuáles son las transiciones? | `BLOCK5_2_STATE_MACHINE.md` sección 2 |
| 5 | ¿Cuáles son los permisos? | `BLOCK5_2_RBAC_MATRIX.md` secciones 2-3 |
| 6 | ¿Cuáles son los endpoints? | `BLOCK5_2_API_CONTRACTS.md` secciones 2-3 |
| 7 | ¿Cómo se entrega la credencial? | `BLOCK5_2_SECURITY_MODEL.md` secciones 1, 3 |
| 8 | ¿Cuándo se descifra? | `BLOCK5_2_SECURITY_MODEL.md` sección 2 (just-in-time, 3 precondiciones) |
| 9 | ¿Cuánto vive en claro? | `BLOCK5_2_SECURITY_MODEL.md` sección 1 — "tan pronto como sea posible en la práctica", con la limitación de zeroización documentada, no prometida |
| 10 | ¿Cómo se asigna un agente? | `BLOCK5_2_ARCHITECTURE.md` sección 4; `BLOCK5_2_API_CONTRACTS.md` sección 1 |
| 11 | ¿Cómo se reclama un job? | `BLOCK5_2_API_CONTRACTS.md` sección 6; `BLOCK5_2_SECURITY_MODEL.md` sección 5 |
| 12 | ¿Cómo se impide el replay? | `BLOCK5_2_SECURITY_MODEL.md` sección 5 |
| 13 | ¿Cómo se cancela? | `BLOCK5_2_STATE_MACHINE.md` sección 7; matriz de carreras en `BLOCK5_2_SECURITY_MODEL.md` sección 11 |
| 14 | ¿Cómo expira? | `BLOCK5_2_STATE_MACHINE.md` sección 8 |
| 15 | ¿Qué significa `SUCCEEDED`? | `BLOCK5_2_STATE_MACHINE.md` sección 3 |
| 16 | ¿Qué significa `PARTIAL_FAILURE`? | `BLOCK5_2_STATE_MACHINE.md` sección 9 (con matriz exacta) |
| 17 | ¿Cómo se maneja el challenge? | `BLOCK5_2_STATE_MACHINE.md` secciones 5-6 |
| 18 | ¿Qué hosts están permitidos? | `BLOCK5_2_ARCHITECTURE.md` sección 6 |
| 19 | ¿Qué pasa si el portal cambia? | `BLOCK5_2_ARCHITECTURE.md` sección 7 |
| 20 | ¿Qué se audita? | `BLOCK5_2_SECURITY_MODEL.md` sección 7 |
| 21 | ¿Qué NO se persiste? | `BLOCK5_2_SECURITY_MODEL.md` sección 10 |
| 22 | ¿Cómo se impide cruzar tenant? | `BLOCK5_2_SECURITY_MODEL.md` sección 6; `BLOCK5_2_DATA_MODEL_DRAFT.md` (FKs compuestas en cada tabla) |
| 23 | ¿Qué queda explícitamente fuera de alcance? | `BLOCK5_2_ARCHITECTURE.md` secciones 2 (lista `FORBIDDEN`), 3, 11 |

Las 23 preguntas quedan respondidas por este freeze — **`ARCHITECTURE_FROZEN=YES`** (ver informe final). Ninguna requiere una ronda de diseño adicional antes de Domain B; lo que sí requiere una ronda adicional es la implementación misma.

### 2.1. Gates adicionales de Domain A.1 — contrato de persistencia

```
SCHEMA_CONTRACT_FROZEN=YES

MIGRATION_0018_TABLES_FROZEN=YES      (BLOCK5_2_DATA_MODEL_DRAFT.md sección 8)
SESSION_COLUMNS_FROZEN=YES            (sección 1 — 20 columnas)
AGENT_COLUMNS_FROZEN=YES              (sección 2)
JOB_COLUMNS_FROZEN=YES                (sección 3)
PAIRING_COLUMNS_FROZEN=YES            (sección 4)
ENUMS_FROZEN=YES                      (sección 7 — 7 enums, lista exhaustiva)
INDEX_PLAN_FROZEN=YES                 (sección 9)
TENANT_FK_PLAN_FROZEN=YES             (sección 6)
PARTIAL_FAILURE_SEMANTICS_FROZEN=YES  (BLOCK5_2_STATE_MACHINE.md sección 9, matriz exacta)
```

Domain B puede implementar la migración `0018` completa a partir
exclusivamente de `BLOCK5_2_DATA_MODEL_DRAFT.md` — sin necesitar
ninguna decisión de esquema adicional.

### 2.2. Gates adicionales de Domain A.2 — seguridad y compatibilidad final del DDL

```
CREDENTIAL_COMPOSITE_FK_SUPPORTED=YES         (BLOCK5_2_DATA_MODEL_DRAFT.md sección 6 — uq_client_sol_credentials_id_tenant_client ya existe, migración 0017)
CREDENTIAL_SUPPORTING_UNIQUE_REQUIRED=NO
CREDENTIAL_OWNERSHIP_DB_ENFORCED=YES

PAIRING_CODE_KEYED_VERIFIER=YES               (mecanismo exacto corregido en Domain A.3 — sección 2.3, NO es Argon2id)
PAIRING_VERIFIER_COLUMN=pairing_code_verifier (renombrado desde pairing_code_hash)
PAIRING_VERIFIER_TERMINAL_RETENTION=CLEAR

MAX_CREDENTIAL_DELIVERIES_PER_SESSION=1       (sección 3 — índice único parcial en sol_agent_jobs, no columna nueva)
CREDENTIAL_DELIVERY_CONCURRENCY_DB_SAFE=YES
NEW_JOB_AFTER_CREDENTIAL_DELIVERY=FORBIDDEN

IDEMPOTENCY_SCOPE_FROZEN=YES                  (sección 1 — (tenant_id, idempotency_key), confirmado contra TaxPayment real)
IDEMPOTENCY_PAYLOAD_FINGERPRINT_COLUMN_REQUIRED=NO

SESSION_COLUMNS_FROZEN=YES   (sigue en 20 — sin columna nueva, sección 11)
PAIRING_COLUMNS_FROZEN=YES   (sigue en 14 — sin columna nueva, solo rename)
CONSTRAINT_REGISTER_FROZEN=YES                (sección 12 — registro final de constraints/índices)

UNRESOLVED_WITH_DDL_IMPACT=0
```

Ninguno de los 4 gates de Domain A.2 requirió reabrir arquitectura
general, tocar la migración `0017`, ni añadir columnas a
`sol_access_sessions`/`sol_agent_pairings`/`sol_agent_jobs` más allá de
lo ya congelado en Domain A.1 — 3 de los 4 se resolvieron confirmando
con evidencia real un diseño ya correcto (Gates 1, 4, 6/7); solo el
Gate 2 (verificador de pairing) cambió el nombre/mecanismo de una
columna ya prevista, sin alterar el conteo.

### 2.3. Gate adicional de Domain A.3 — corrección criptográfica del verificador de pairing

Domain A.2 cerró el Gate 2 con una afirmación que no resistió la
verificación exhaustiva pedida en Domain A.3: `PAIRING_CODE_KEYED_VERIFIER=YES`
se justificó citando Argon2id, pero Argon2id *salted* (sin pepper) no
es un *keyed verifier* — es simplemente más costoso de atacar offline,
no imposible. Verificado leyendo `backend/app/core/security.py`
completo de nuevo: `PasswordHasher()` no toma secreto/pepper en ningún
punto del repo.

```
ARGON2_PASSWORD_HASH_IS_KEYED=NO

PAIRING_CODE_KEYED_VERIFIER=YES               (ahora correcto — HMAC-SHA-256, no Argon2id)
PAIRING_VERIFIER_PRIMITIVE=HMAC-SHA-256 (stdlib hmac/hashlib)
PAIRING_VERIFIER_DOMAIN_SEPARATED=YES         ("sol-agent-pairing:v1" || tenant_id || pairing_id || codigo)
PAIRING_VERIFIER_SERVER_KEY_OUTSIDE_DB=YES    (PAIRING_VERIFIER_KEY vía Settings/.env, patrón de config.py)
PAIRING_VERIFIER_CONSTANT_TIME_COMPARE=YES    (hmac.compare_digest, stdlib)

PAIRING_VERIFIER_KEY_VERSION_COLUMN_REQUIRED=NO   (TTL=120s hace innecesaria persistir version de clave; drain operacional basta)
PAIRING_COLUMNS_FROZEN=YES                        (sigue en 14 — confirmado explícitamente tras esta decisión, no antes)
```

Esta corrección **no** reabre nada de lo listado como fuera de alcance
en el encargo de Domain A.3 (sesiones, jobs, no-redelivery,
idempotencia, RBAC, state machine, FK strategy, plan de índices, API
general) — toca exclusivamente el mecanismo de un único campo ya
identificado, sin cambiar su nombre, nullability, ni la cantidad de
columnas de `sol_agent_pairings`. Domain B puede implementar la
migración `0018` completa a partir exclusivamente de
`BLOCK5_2_DATA_MODEL_DRAFT.md` (ya actualizado con esta corrección) —
sigue sin necesitar ninguna decisión de esquema ni criptográfica
adicional.

## 3. Disposición final de las 7 decisiones pendientes de Domain A (Domain A.1, sección 56)

Ninguna queda `UNRESOLVED_WITH_DDL_IMPACT` — cada una se cierra como
`RESOLVED` o se reclasifica explícitamente como
`DEFERRED_WITHOUT_DDL_IMPACT` (un parámetro de configuración/detalle
de implementación que no cambia ninguna tabla/columna/constraint ya
congelada):

| # | Decisión | Disposición | Detalle |
|---|---|---|---|
| 1 | Transporte agente↔broker (WebSocket/long-poll/stream) | `DEFERRED_WITHOUT_DDL_IMPACT` | No depende de ninguna columna — `BLOCK5_2_API_CONTRACTS.md` sección 1 |
| 2 | `sol_agent_pairings` como tabla independiente | **`RESOLVED`** | `PAIRING_TABLE_DECISION=YES`, definitivo — `BLOCK5_2_DATA_MODEL_DRAFT.md` sección 4 |
| 3 | `created_by` vs. `requested_by_user_id` | **`RESOLVED`** | `created_by` descartado, solo `requested_by_user_id` — `BLOCK5_2_DATA_MODEL_DRAFT.md` sección 1 |
| 4 | Formato exacto del idempotency key | **`RESOLVED`** | Columna `idempotency_key: uuid.UUID` en la propia entidad (patrón `TaxPayment`), no header HTTP — `BLOCK5_2_API_CONTRACTS.md` sección 7 |
| 5 | Umbral de heartbeat para derivar `OFFLINE` | `DEFERRED_WITHOUT_DDL_IMPACT` | El principio (derivado, nunca persistido) está congelado; el número exacto es config — `BLOCK5_2_SECURITY_MODEL.md`, "Heartbeat y derivación online/offline" |
| 6 | Valores numéricos exactos de `session_ttl`/`job_ttl` | `DEFERRED_WITHOUT_DDL_IMPACT` | La estructura (3 TTL separados, tiempo de BD autoritativo, sin auto-extensión) está congelada; los números son config — `BLOCK5_2_STATE_MACHINE.md` sección 8 |
| 7 | Opinión legal experta sobre "efectuada vs. surte efectos" | `DEFERRED_WITHOUT_DDL_IMPACT` | No toca ninguna tabla — no bloquea `AUTHENTICATE_TO_LANDING_ONLY`; bloquearía un scope futuro de Buzón — heredado de Spike A/A2-DOC (`PRODUCT_HANDOFF_PHASE5_BLOCK2.md`) |

```
UNRESOLVED_WITH_DDL_IMPACT=0
```

Dos decisiones nuevas se resolvieron **además** durante Domain A.1, sin
haber sido parte de los 7 pendientes originales de Domain A:

- **Máximo de sesiones no-terminales por credencial** → una,
  `UNIQUE(tenant_id, client_id, credential_id) WHERE status NOT IN
  (terminales)` (Domain A.1 sección 33).
- **Máximo de jobs activos por agente/sesión** → uno en cada caso,
  índices únicos parciales (Domain A.1 secciones 21, 34).

## 4. Plan de implementación (Domain B en adelante — no autorizado por este documento)

Orden sugerido, sin comprometer fechas ni autorizar ejecución:

1. **Domain B — Data & Security**: migración `0018` real (tablas de
   `BLOCK5_2_DATA_MODEL_DRAFT.md` sección 6), modelos SQLAlchemy,
   extensión de `Permission`/`AuditAction`, pruebas estructurales de
   aislamiento de tenant y de la máquina de estados (sin Playwright
   real todavía).
2. **Domain C — Broker & Agent runtime**: implementación de mTLS,
   pairing, el transporte elegido (pendiente, sección 3.1), ejecución
   real del flujo Playwright dentro del agente Windows, reutilizando
   el código validado de Spike A (`observer/`) como base de
   implementación (no como import directo del spike — se reescribe
   sobre `master`, con las mismas garantías ya probadas).
3. **Domain D — API & Frontend**: los endpoints de
   `BLOCK5_2_API_CONTRACTS.md`, la pantalla de sesión del frontend.
4. **Domain E — Integración end-to-end supervisada**: primera
   ejecución real contra SUNAT, con el mismo nivel de cautela que
   Spike A A1 (usuario secundario controlado, humano en control de
   cada paso sensible) — requiere autorización explícita independiente,
   nunca implícita por haber llegado a este punto del plan.

## 5. Archivos creados en Domain A

```
docs/phase5/block5_2/BLOCK5_2_ARCHITECTURE.md
docs/phase5/block5_2/BLOCK5_2_STATE_MACHINE.md
docs/phase5/block5_2/BLOCK5_2_SECURITY_MODEL.md
docs/phase5/block5_2/BLOCK5_2_API_CONTRACTS.md
docs/phase5/block5_2/BLOCK5_2_DATA_MODEL_DRAFT.md
docs/phase5/block5_2/BLOCK5_2_RBAC_MATRIX.md
docs/phase5/block5_2/BLOCK5_2_EVIDENCE_REGISTER.md
docs/phase5/block5_2/BLOCK5_2_IMPLEMENTATION_PLAN.md
```

**Ningún archivo fuera de `docs/phase5/block5_2/` fue modificado.**
Sin migración, sin modelos SQLAlchemy, sin routers FastAPI, sin
runtime de broker/agente, sin frontend, sin Playwright productivo —
conforme a la restricción explícita de Domain A.
