# Block 5.2 — Domain E1-A: Arquitectura de entrega de secreto SOL

**Estado: ARCHITECTURE / CONTRACT FREEZE. Ningún código de producto de
este documento está implementado.** Domain E1-B implementará contra este
congelamiento; cualquier desviación exige una nueva ronda de freeze, no
una decisión ad-hoc durante la implementación.

**Domain E1-A.1** (esta revisión) resuelve tres gates residuales de
E1-A: (1) el nombre exacto de la operación de `VaultUnlockRegistry`
para entrega de secreto (sección 4), (2) la fuente confiable del RUC en
el payload mínimo del agente (sección 9), y (3) la reserva atómica
process-local del grant de entrega (sección 5.4), más la ratificación
de `CREDENTIAL_DELIVERY_AUTHORIZED` (sección 20). Ninguna sección
anterior no listada aquí se reabre.

Complementa (nunca sustituye): `BLOCK5_2_ARCHITECTURE.md`,
`BLOCK5_2_SECURITY_MODEL.md` (sección 13 y tabla de amenazas T23–T42),
`BLOCK5_2_API_CONTRACTS.md`, `ADR-0068`–`ADR-0071` (Bloque 5.1),
el informe final de Domain C (`mark_credential_delivered`) y el informe
final de Domain D/D.1 (mTLS, `connection_id`, `pending_dispatch`).

Sentinelas de prueba usados en todo este documento —
**nunca credenciales reales**: `TEST_SOL_USER`,
`TEST_SOL_PASSWORD_DO_NOT_USE`.

## 0. Código de producto inspeccionado (fuente de verdad, no memoria)

**Bloque 5.1**:
- `app/models/sol_vault.py` — `SolVault` (`status` persistido
  `ACTIVE`/`RECOVERY_REQUIRED`/`DISABLED`; `LOCKED`/`NOT_INITIALIZED` son
  derivados, nunca persistidos), `SolVaultKeySlot`, `SolVaultDataKey`
  (versionada, `ACTIVE`/`RETIRED`), `SolVaultInvitation`.
- `app/models/client_sol_credential.py` — `ClientSolCredential`:
  `sol_username_encrypted`/`sol_password_encrypted` (`LargeBinary`),
  `data_key_version` (versión específica usada al cifrar, **no
  necesariamente la ACTIVE actual**), `status` (este bloque nunca asigna
  `ACTIVE`, solo `NEEDS_VALIDATION`/`DISABLED`).
- `app/core/sol_vault_crypto.py` — `decrypt_credential_field(envelope,
  credential_dek, aad) -> str`, `encrypt_credential_field`,
  `unwrap_data_key`, `unwrap_vault_root_key`, `aad_credential_field`,
  `zeroize(bytearray)`.
- `app/core/sol_vault_registry.py` — `VaultUnlockRegistry`, clave exacta
  `(tenant_id, vault_id, user_id, app_session_id) -> handle`,
  `resolve(db, ..., operation, now, client_id=None)`,
  `ALLOWED_OPERATIONS = {CREDENTIAL_MANAGEMENT, SLOT_MANAGEMENT,
  DATA_KEY_ROTATION, RECOVERY, VERIFY_ACCESS}`. Singleton de proceso
  `vault_unlock_registry`.
- `app/core/sol_vault_worker_gate.py` — `enforce_single_worker_gate`:
  Bloque 5.1 **ya** congeló la invariante de un único proceso backend
  para `VaultUnlockRegistry` (Dockerfile arranca uvicorn sin
  `--workers`). Domain E1 hereda esta invariante, no la inventa.
- `app/services/sol_vault_service.py` — `_allowed_operations_for` (admin
  → los 5; contador → solo `CREDENTIAL_MANAGEMENT`+`VERIFY_ACCESS`;
  asistente → ninguno), `_unwrap_credential_dek`, `_advisory_lock`
  (`pg_advisory_xact_lock(hashtext(vault_id))`).
- `app/services/client_sol_credential_service.py` — `create_credential`,
  `update_credential`, `disable_credential`, `get_credential_status`.
  **Ninguna función de este módulo descifra para lectura/entrega hoy** —
  solo cifra en alta/actualización. Domain E1-B será el primer código
  productivo que descifra un `client_sol_credentials` para consumo
  externo.
- `app/repositories/sol_vault_repository.py` — `get_active_data_key`,
  **`get_data_key_by_version`** (ya existe: resuelve el gap de qué pasa
  si la credencial fue cifrada con una versión ya `RETIRED`).
- `app/core/permissions.py` — `SOL_CREDENTIAL_READ`,
  `SOL_SESSION_CREATE`, `SOL_VAULT_UNLOCK` ya existen, los tres
  exclusivos de admin+contador (nunca asistente).
- `app/models/client.py` — `Client.ruc` (`String(11)`, `nullable=False`,
  `UniqueConstraint("tenant_id", "ruc")`) — identificador tributario
  real y obligatorio, nunca opcional.
- `app/models/sol_agent_access.py` — `SolAccessSession.client_id`
  (`nullable=False`, FK compuesta a `clients.id`/`clients.tenant_id`,
  **la misma columna** que la FK compuesta hacia
  `client_sol_credentials.client_id` — Domain A.2 ya garantiza a nivel
  de BD que la credencial de una sesión pertenece al mismo cliente que
  `session.client_id`). Cadena confiable confirmada: `SolAgentJob.
  session_id` (inmutable tras `create_job`) → `SolAccessSession.
  client_id` → `Client.ruc`, enteramente tenant-safe y derivable solo
  desde estado de backend ya cargado — el agente nunca la provee.

**Domain C**: `app/services/sol_agent_job_service.py`
(`mark_credential_delivered`, `claim_job`, `_lock_session_then_job`,
`_JOB_ACTIVE_STATUSES`), `app/services/sol_access_session_service.py`
(`cancel_session`, `expire_session`, `SESSION_TERMINAL_STATUSES`).

**Domain D**: `app/broker/mtls.py` (`resolve_agent_identity`),
`app/broker/connection_registry.py` (`ConnectionRegistry.
validate_and_touch`), `app/broker/pending_dispatch.py`
(`PendingDispatchStore`), `app/broker/server.py`
(`_authenticated_agent_with_live_connection`, `_dispatch`,
`_reject_browser_like_request`).

**Spike B** (evidencia únicamente, `spike/phase5-sol-agent-ipc`, nunca
merge/cherry-pick/rebase): Alternativa A (backend descifra y entrega un
único par usuario/contraseña en claro por job, nunca un DEK) ya frozen
como "Decisión G" en el informe final de Domain D. Este documento la
implementa productivamente sin reabrir la comparación contra la
Alternativa B (DEK-based, ya rechazada).

## 1. Principio de seguridad primario (congelado)

```
CANAL SEGURO  !=  AUTORIZACIÓN PARA LIBERAR UN SECRETO
```

mTLS + agente ACTIVE + `connection_id` vigente es **necesario pero
nunca suficiente**. La liberación de un secreto exige además, **todas**
simultáneamente:

1. Contexto humano/backend explícitamente autorizado (sección 3).
2. Vault actualmente desbloqueada para el `(tenant, vault, user,
   app_session)` exacto (sección 4), **re-verificada en el momento de
   entrega**, nunca solo al crear la autorización.
3. Binding correcto de sesión/job (job `CLAIMED`, sesión no terminal).
4. Autorización de un solo uso (`SecretDeliveryGrant`, sección 5).
5. Conexión lógica viva y **fijada** durante la ventana crítica
   (sección 8).
6. Gate de entrega de Domain C (`mark_credential_delivered`).

## 2. Modelo de autorización humana/backend (`authorize_credential_delivery`)

**No implementado en E1-A.** Operación interna futura, invocada por una
User API autenticada aún no construida — nunca por el agente, nunca por
el broker por iniciativa propia.

**Contrato conceptual**:

```
authorize_credential_delivery(
    db, *,
    tenant_id, session_id, job_id,
    actor_user_id, app_session_id,
    grant_registry: SecretDeliveryGrantRegistry,
) -> None  # nunca retorna texto plano
```

**Nunca descifra.** Su único efecto es crear una entrada en
`SecretDeliveryGrantRegistry` (sección 5) — una "ventana de
oportunidad" de un solo uso para que el job ya `CLAIMED` correspondiente
pueda, después, solicitar su credencial.

### 2.1 Vocabulario de permisos (freeze de la sección 7 del encargo)

Inspeccionado `app/core/permissions.py`: **`sol_session:create`** y
**`sol_credential:read`** ya existen, ambos exclusivos de
admin+contador (nunca asistente) — ver `_CONTADOR_ONLY_ADDITIONS`.
**Ningún permiso nuevo es necesario.**

**Decisión congelada**: v1 exige **ambos**, no uno solo:

- `sol_session:create` — el actor tiene autoridad para gestionar el
  ciclo de vida de sesiones de acceso SOL supervisado (de donde nace
  esta autorización).
- `sol_credential:read` — el actor tiene autoridad para usar/leer la
  credencial SOL subyacente (de donde nace el secreto en sí).

Ninguno de los dos por sí solo es semánticamente completo: el primero
sin el segundo autorizaría "gestionar sesiones" sin tocar credenciales;
el segundo sin el primero autorizaría "leer credenciales" sin el
contexto de una sesión de acceso supervisado. `NEW_RBAC_PERMISSION_REQUIRED=NO`.

## 3. Actor binding (freeze de la sección 8 del encargo)

**Regla v1 (conservadora, congelada)**: el usuario que autoriza la
entrega debe ser exactamente `sol_access_sessions.requested_by_user_id`
de esa sesión. Ninguna sustitución silenciosa de usuario.

Justificación desde el código real: `SolAccessSession.
requested_by_user_id` ya es la única columna de autoría de la sesión
(`app/models/sol_agent_access.py`); no existe hoy ningún concepto de
"co-autorizador" o delegación en Domain A/B/C. Introducir uno sería
ampliar el modelo de datos — fuera de alcance de E1 y prohibido por la
sección 52 del encargo (`NO 0019`). Si un futuro requerimiento de
negocio necesita que un admin autorice la entrega de una sesión creada
por un contador (por ejemplo), eso es una decisión de producto que debe
congelarse explícitamente en una ronda futura — **no se asume aquí**.

## 4. Vault unlock binding (freeze de la sección 9 del encargo)

Confirmado en el código real (`sol_vault_registry.py`): la clave de
`VaultUnlockRegistry` es exactamente

```
(tenant_id, vault_id, user_id, app_session_id) -> handle
```

Domain E1 **hereda** este binding sin modificarlo. La futura
autorización de entrega debe registrar `user_id`/`app_session_id` en el
grant (sección 5) — nunca inventar un binding distinto. El **agente**
nunca provee `user_id`/`app_session_id`/`vault_id` como identidad
autoritativa: estos valores viajan **exclusivamente** desde el estado
de backend ya confiable (la fila de `SolAccessSession` y el grant
process-local), nunca desde ningún campo del protocolo del agente.

`resolve()` exige un `operation` de `ALLOWED_OPERATIONS`. **Ninguno de
los 5 valores actuales** (`CREDENTIAL_MANAGEMENT`, `SLOT_MANAGEMENT`,
`DATA_KEY_ROTATION`, `RECOVERY`, `VERIFY_ACCESS`) describe
semánticamente "liberar un secreto puntual a un agente para uso
automatizado" — es una acción de mayor riesgo que "gestionar" (crear/
actualizar/deshabilitar credenciales vía UI autenticada). `
ALLOWED_OPERATIONS` es un `frozenset` de Python, **no** un
`CheckConstraint` de base de datos — añadir un sexto valor en E1-B
**no requiere migración**.

**Congelado en Domain E1-A.1**: el nombre exacto es
**`CREDENTIAL_DELIVERY`** — capacidad/operación de
`VaultUnlockRegistry` únicamente. Explícitamente:

- **No** es un permiso RBAC (`app/core/permissions.py` no cambia).
- **No** es un valor de enum de base de datos.
- **No** requiere migración.
- **No** es un alcance de cara al usuario.

Domain E1-B queda autorizado a añadir `CREDENTIAL_DELIVERY` al
vocabulario de `ALLOWED_OPERATIONS` (y a `_ADMIN_OPERATIONS`/
`_CONTADOR_OPERATIONS` en `sol_vault_service.py`, según la sección 2.1
de este documento). **Nunca** debe reutilizarse `VERIFY_ACCESS` para
este propósito — `VERIFY_ACCESS` ya tiene una semántica distinta y más
débil (verificar que el desbloqueo sigue vigente, sin autorizar ninguna
liberación de secreto) en el código actual; conflar ambas
permitiría que cualquier flujo que hoy solo necesita "verificar acceso"
autorizara, sin quererlo, una entrega de credencial real. La futura
entrega **debe** resolver el handle de desbloqueo específicamente con
`operation=CREDENTIAL_DELIVERY` — nunca implementado en E1-A.1, solo
congelado como el nombre y la regla de no-reutilización.

`VAULT_OPERATION_FOR_SECRET_DELIVERY=CREDENTIAL_DELIVERY`,
`VAULT_OPERATION_REUSES_VERIFY_ACCESS=NO`.

## 5. `SecretDeliveryGrantRegistry` (arquitectura únicamente)

Registro process-local, en memoria, **nunca** persistido — mismo
patrón exacto ya productivo en `VaultUnlockRegistry` (Bloque 5.1) y
`ConnectionRegistry`/`PendingDispatchStore` (Domain D).

**Clave**: `(tenant_id, job_id)`. Justificación: un job ya tiene, por
diseño de Domain C, a lo sumo un `agent_id` y una `session_id`
asociados de forma inmutable (`SolAgentJob.agent_id`/`session_id` no
cambian tras `create_job`); `job_id` es ya el identificador más
granular y estable disponible, y coincide con la clave que
`pending_dispatch.py` ya usa por `agent_id` — aquí se prefiere
`(tenant_id, job_id)` en vez de `(tenant_id, agent_id)` porque un mismo
agente podría, en teoría, tener grants sucesivos de jobs distintos en
rápida sucesión, y atar por `job_id` evita cualquier ambigüedad
cross-job.

**Metadata contenida** (nunca más que esto):

```
tenant_id
session_id
job_id
agent_id
user_id
app_session_id
credential_id
created_at   (monotonic, proceso)
expires_at   (monotonic, proceso)
```

**Explícitamente prohibido dentro del grant**: `sol_username`,
`sol_password`, `ruc`, `vault_root_key`, `credential_dek`, master
password, recovery secret, cualquier material criptográfico.
`SECRET_DELIVERY_GRANT_CONTAINS_PLAINTEXT=NO`,
`SECRET_DELIVERY_GRANT_CONTAINS_CRYPTO_KEYS=NO`.

**PostgreSQL sigue siendo la autoridad final** (nunca el registro
process-local): la reserva atómica `IN_FLIGHT` de la sección 5.4 es
**defensa en profundidad únicamente** — reduce descifrados
desperdiciados y cierra una ventana de carrera antes del gate, pero
`credential_delivered_at` y el índice único parcial
`ux_sol_agent_jobs_one_delivery_per_session` (Domain A.2) siguen siendo
la única autoridad transaccional real para "a lo sumo una entrega por
sesión". La pila de defensa completa, en orden:

```
grant válido
  ↓
adquisición atómica IN_FLIGHT (proceso, sección 5.4)
  ↓
descifrado (una sola vez)
  ↓
gate de un solo uso de PostgreSQL (Domain C)
  ↓
commit
  ↓
respuesta de red
```

Ningún lock de Python sustituye jamás al gate de BD.

### 5.1 Sin token de grant hacia el agente

**Decisión v1 (congelada)**: ningún token de grant se transmite al
agente. El grant vive **exclusivamente** en el broker/backend. El
agente ya prueba, sobre mTLS: identidad de certificado + `connection_id`
vigente + job reclamado — el broker es quien consulta, del lado
confiable, si existe un grant autorizado para ese job/sesión/agente
exactos. Esto refleja la misma decisión ya más estricta de Domain D
para el token de job (`JOB_TOKEN_TRANSPORTED_TO_AGENT_OVER_WIRE=NO`,
resuelto broker-side): aquí no hay siquiera un artefacto que decidir no
transmitir, porque nunca se generó. `SECRET_DELIVERY_GRANT_SENT_TO_AGENT=NO`.

### 5.2 TTL

**Congelado: 60 segundos** (límite superior inicial recomendado por el
encargo, adoptado sin evidencia en contra en el código actual). El TTL
efectivo real es `min(60s, tiempo_restante_de_sesión,
tiempo_restante_de_job)` — un grant nunca sobrevive más que el job/
sesión que autoriza. `SECRET_DELIVERY_GRANT_TTL_SECONDS=60`.

Un grant **nunca** mantiene la vault desbloqueada — es solo una
"ventana de oportunidad" para pedir la entrega; el desbloqueo real se
re-verifica en el momento de la entrega (sección 22 del encargo /
sección 9 de este documento).

### 5.3 Seguridad de recursos

Mismo patrón ya productivo (`_MAX_ENTRIES` + purga perezosa +
evicción explícita en transiciones terminales — ver
`sol_pairing_verification_throttle.py` y
`app/broker/connection_registry.py`):

- **A lo sumo un grant activo por job** — un segundo intento de
  autorización para el mismo job reemplaza (nunca duplica) al anterior.
- Cota dura de cardinalidad, independiente de la disciplina de llamada.
- Purga perezosa de entradas vencidas por TTL.
- Evicción explícita en: consumo exitoso del gate (sección 12), sesión/
  job terminal, revocación del agente, reemplazo de conexión (política
  a definir en E1-B, ver sección 8), y apagado del broker.
- Un `job_id`/`session_id` desconocido o inexistente **nunca** crea
  estado — la autorización (sección 2) debe validar la existencia y el
  estado del job/sesión **antes** de registrar el grant, mismo patrón
  ya corregido en Domain C.1b para el throttle de verificación de
  pairing (lookup antes que registro, nunca al revés).

### 5.4 Máquina de estados de reserva atómica (Domain E1-A.1, Gate 3)

El gate de BD de Domain C (`credential_delivered_at`) impide una doble
entrega **autorizada**, pero dos solicitudes concurrentes podrían
ambas superar la autorización y el descifrado y solo entonces competir
por el gate de BD — desperdiciando dos descifrados en vez de uno, y
abriendo una ventana (aunque el gate de BD seguiría siendo la autoridad
final) donde el proceso ya hizo trabajo de descifrado innecesario. Se
congela una máquina de estados process-local sobre el propio
`SecretDeliveryGrant`:

```
AUTHORIZED  →  IN_FLIGHT  →  CONSUMED
                   ↓
              INVALIDATED
```

- **`AUTHORIZED`**: estado inicial, creado por
  `authorize_credential_delivery` (sección 2).
- **`IN_FLIGHT`**: adquirido atómicamente por **una única** solicitud
  de entrega — la transición `AUTHORIZED → IN_FLIGHT` es la **única**
  transición de adquisición permitida, y debe ejecutarse bajo el mismo
  lock interno que ya protege el resto del registro (mismo patrón
  `threading.Lock` ya productivo en `ConnectionRegistry`/
  `PendingDispatchStore`/`VaultUnlockRegistry`) — compare-and-swap
  atómico: solo la primera solicitud que observa `AUTHORIZED` logra
  escribir `IN_FLIGHT`; cualquier otra que llegue mientras el estado ya
  es `IN_FLIGHT` (o no existe) se rechaza de inmediato, **antes** de
  intentar cualquier descifrado.
- **`CONSUMED`**: alcanzado únicamente tras un commit exitoso del gate
  de BD de Domain C (paso 10 del orden exacto, sección 11) — la entrada
  se elimina del registro inmediatamente después (irrevocable).
- **`INVALIDATED`**: alcanzado ante **cualquier** fallo posterior a la
  adquisición de `IN_FLIGHT` (descifrado, pérdida de vault, fallo de
  BD, pérdida del lease de conexión, revocación del agente, fallo de
  estado de sesión/job, fallo al preparar la respuesta) — la entrada se
  elimina del registro inmediatamente.

**Congelado, sin excepción**: una vez adquirido `IN_FLIGHT`, el grant
**nunca** vuelve a `AUTHORIZED`. No existe una transición de
"reintento" que reutilice el mismo grant tras una falla dentro de la
ventana `IN_FLIGHT` — cualquier reintento exige una **nueva**
autorización humana explícita (`authorize_credential_delivery` de
nuevo), salvo la única excepción ya congelada en la sección 12 (fallo
transitorio de BD **antes** del gate, donde el grant completo —no la
adquisición `IN_FLIGHT` específica— puede sobrevivir hasta su TTL para
un reintento del **mismo** agente; ver la nota de reconciliación al
final de esta subsección).

**Fallos antes de adquirir el grant correcto nunca deben quemar el
grant de otro actor** (sección 7 del encargo E1-A.1): un agente
equivocado, tenant equivocado, job equivocado, job desconocido, mTLS
inválido, o `connection_id` obsoleto/incorrecto detectado **antes** de
localizar el grant correspondiente por su clave `(tenant_id, job_id)`
deben fallar **sin tocar** el registro de grants en absoluto — nunca
una escritura, nunca una transición de estado, nunca una eliminación.
Esto es, deliberadamente, la misma disciplina ya corregida en Domain
C.1b para el throttle de verificación de pairing (el *lookup* del
recurso correcto ocurre antes que cualquier mutación de estado) — sin
ella, un atacante podría enviar solicitudes con identidad incorrecta
específicamente para invalidar el grant legítimo de otro agente, un
primitivo de denegación de servicio. `WRONG_AGENT_CAN_INVALIDATE_LEGITIMATE_GRANT=NO`.

**Reconciliación con la sección 12** (grant sobrevive un fallo
transitorio de BD antes del gate): esa supervivencia se refiere al
grant en su conjunto dentro de su TTL, no a una `IN_FLIGHT` colgada.
Congelado explícitamente aquí: si la adquisición `IN_FLIGHT` fallara a
mitad de camino por un error transitorio (por ejemplo, un timeout de
base de datos durante los pasos 9–10, antes de poder decidir
`CONSUMED`/`INVALIDATED`), la implementación de E1-B debe tratar ese
caso como `INVALIDATED` inmediato del intento `IN_FLIGHT` actual —
nunca dejarlo indefinido — y **solo entonces**, si el grant original
(la fila lógica, no el intento) seguía dentro de su TTL y la falla fue
clasificada como transitoria de infraestructura (no de autorización/
seguridad), permitir que un **nuevo** intento del mismo agente
adquiera un **nuevo** ciclo `AUTHORIZED → IN_FLIGHT` sobre el mismo
grant lógico. Esta distinción (grant lógico vs. intento `IN_FLIGHT`)
es la responsabilidad de diseño de E1-B; aquí se congela la regla, no
la implementación exacta del contador/estructura.

`SECRET_DELIVERY_GRANT_STATES=[AUTHORIZED, IN_FLIGHT, CONSUMED, INVALIDATED]`,
`ATOMIC_GRANT_IN_FLIGHT_ACQUISITION=YES`,
`FAILED_IN_FLIGHT_GRANT_RETURNS_TO_AUTHORIZED=NO`.

### 5.5 Semántica de concurrencia (sección 10 del encargo)

Para dos solicitudes de entrega simultáneas sobre el mismo
`(tenant_id, job_id)`:

```
CONCURRENT_DELIVERY_MAX_DECRYPTIONS=1
CONCURRENT_DELIVERY_MAX_GATE_COMMITS=1
CONCURRENT_DELIVERY_MAX_SECRET_RESPONSES=1
```

La adquisición atómica `IN_FLIGHT` (sección 5.4) garantiza que **como
mucho una** solicitud llega siquiera al paso de descifrado (sección
11, paso 8) — nunca dos descifrados por una carrera, aunque el gate de
BD de Domain C seguiría siendo, de todas formas, la autoridad final e
independiente (sección 5, "DB remains final authority": el lock
process-local es defensa en profundidad, nunca un sustituto).

## 6. Restricción de proceso único (freeze de la sección 14 del encargo)

`VaultUnlockRegistry` y `ConnectionRegistry`/`PendingDispatchStore` son
**ya**, cada uno independientemente, process-local. El
`SecretDeliveryGrantRegistry` debe coexistir con **ambos** dentro del
mismo proceso Python — no puede resolver un grant sin poder consultar
`vault_unlock_registry` (import directo del singleton) ni sin poder
consultar el registro de conexión del broker.

**Consecuencia arquitectónica que este freeze hace explícita**:
`app/core/sol_vault_worker_gate.py` ya exige `BACKEND_WORKERS=1` para
el proceso backend principal. Para que Domain E1-B pueda invocar
`vault_unlock_registry.resolve(...)` directamente (import de objeto
Python, no llamada de red), **el broker debe ejecutarse dentro del
mismo proceso del backend principal** — nunca como un proceso
`BrokerServer` separado con su propio intérprete. Domain D dejó el
broker deliberadamente como un módulo importable, iniciado
explícitamente solo por las pruebas (nunca cableado al arranque de
`app.main`); Domain E1-B **deberá** decidir cómo arrancar los listeners
del broker como hilos de fondo dentro del mismo proceso uvicorn de
`app.main` — no una decisión de E1-A, pero una precondición estructural
que se congela aquí. Fallar cerrado (rechazar la solicitud de secreto)
es preferible a enrutarla hacia un worker sin el `unlock`/`grant`
correcto — nunca replicación distribuida (Redis u otro) de secreto en
E1. `SECRET_DELIVERY_SINGLE_PROCESS_ONLY=YES`.

## 7. Borrador del endpoint futuro (no implementado)

```
POST /agent/jobs/{job_id}/credential
```

Consistente con las convenciones ya productivas del broker
(`/agent/jobs/{job_id}/claim`, `/agent/jobs/{job_id}/status`). **Nunca
GET** — un secreto nunca viaja en una URL cacheable/logueable por
convención HTTP genérica. Sin query string. Requiere, simultáneamente:

1. Certificado de cliente mTLS + huella conocida + agente `ACTIVE`
   (`app/broker/mtls.py::resolve_agent_identity`, ya productivo).
2. `connection_id` vigente para ese `agent_id`
   (`ConnectionRegistry.validate_and_touch`, ya productivo) — ver
   sección 8 para el refuerzo adicional exigido aquí.
3. Job `CLAIMED`, asignado a **este** agente autenticado (nunca un
   `agent_id` de payload — la identidad viene solo de mTLS, mismo
   principio ya congelado en Domain D sección 35 del encargo original).
4. Sesión no terminal, no vencida (`SESSION_TERMINAL_STATUSES`,
   `expires_at` vía `clock.db_now`).
5. `SecretDeliveryGrant` vigente para `(tenant_id, job_id)`.
6. Re-verificación en vivo de `VaultUnlockRegistry` para el
   `(tenant_id, vault_id, user_id, app_session_id)` exacto del grant.

## 8. Lease de conexión / TOCTOU (freeze crítico, sección 16 del encargo)

Domain D usa peticiones HTTP discretas y la política
`NEW_REPLACES_OLD`: un nuevo `HELLO` del mismo certificado sustituye
inmediatamente la entrada de `ConnectionRegistry`. Esto crea una
ventana real: el agente A puede pasar el chequeo de `connection_id` al
**entrar** a `/agent/jobs/{job_id}/credential`, y ser reemplazado por
una instancia B (mismo certificado) **antes** de que la respuesta se
escriba — momento en el que A ya no debería recibir el secreto.

**Decisión congelada**: validar el `connection_id` una sola vez, al
entrar, es **insuficiente**. Se requiere un mecanismo de **pin/lease de
conexión** que cubra toda la ventana crítica, desde el paso 2 hasta el
paso 12 del orden exacto (sección 11) — nunca solo el paso 2 aislado.

**Diseño preferido para E1-B** (arquitectura, no implementación):
extender `ConnectionRegistry` con un lease de corta vida,
`pin(agent_id, connection_id) -> lease_token_interno` que:

- Falla inmediatamente si `connection_id` ya no es el vigente (mismo
  chequeo que `validate_and_touch`, sin duplicar su lógica — debe
  **llamar** a esa validación, nunca reimplementarla).
- Mientras el lease está activo, un `HELLO` de reemplazo (mismo
  certificado, nueva conexión) puede **completarse igual**
  (`NEW_REPLACES_OLD` no se relaja — un agente real que reconecta
  nunca debe bloquearse), pero el pin queda marcado inválido de forma
  atómica en el mismo registro, de modo que el paso 11 (revalidación
  antes de escribir) lo detecte determinísticamente.
- Se libera explícitamente al finalizar la petición (éxito o error) —
  nunca queda un lease huérfano; TTL de lease acotado como red de
  seguridad adicional (mismo principio que el TTL de grant).

Esto es una extensión **acotada** del registro ya existente — no un
mecanismo nuevo paralelo, y no reabre la política `NEW_REPLACES_OLD` ya
congelada en Domain D.1 (sección 6 del encargo original de esa ronda).

## 9. Payload mínimo en claro (freeze de las secciones 23–24 del encargo, revisado en E1-A.1)

Inspeccionado `client_sol_credentials`: los únicos campos cifrados
(`sol_username_encrypted`, `sol_password_encrypted`) corresponden
exactamente a las dos credenciales de login SOL. **No existe** ningún
otro campo cifrado en el modelo.

**Revisión E1-A.1**: E1-A había excluido el RUC del payload por falta
de evidencia de una fuente confiable. Se inspeccionó la relación real
`SolAgentJob.session_id` → `SolAccessSession.client_id` → `Client.ruc`
(sección 0) y se confirma: `Client.ruc` es `nullable=False`, único por
tenant, y `session.client_id` está ligado por FK compuesta a la
**misma** columna que ya usa `client_sol_credentials.client_id` — el
cliente de la credencial y el cliente de la sesión son, por
construcción de BD, el mismo. **No hay conflicto arquitectónico.**
`TRUSTED_RUC_SOURCE_CONFIRMED=YES`.

| Campo propuesto | Clasificación | Justificación |
|---|---|---|
| `ruc` | **SENSITIVE_METADATA** | Identificador tributario, no criptográficamente secreto, pero de alta sensibilidad — obtenido **exclusivamente** de la cadena job→session.client_id→`Client.ruc`, cargada del lado del backend. El agente **nunca** puede sustituirlo ni proveer uno propio; ningún campo del protocolo de Domain D acepta un RUC de entrada. **Nunca** se duplica hacia `client_sol_credentials` — se resuelve en cada entrega desde `clients.ruc`, la única fuente de verdad ya existente (Bloque 1). |
| `sol_username` | SECRET | Requerido por el agente para autenticar en SUNAT — único origen: `decrypt_credential_field` sobre `sol_username_encrypted`. |
| `sol_password` | SECRET | Idéntico razonamiento, sobre `sol_password_encrypted`. |
| `credential_kind` | INTERNAL_ID | Ya existe en claro en `client_sol_credentials.credential_kind` (`SECONDARY`/`PRIMARY_OWN_RUC`/`PRIMARY_EXCEPTIONAL`) — no es secreto, pero **no se incluye** en el payload salvo que una necesidad real del agente lo justifique (no evidenciada hoy). Se omite en v1. |
| `job_id` / `session_id` / `scope` / `expires_at` | INTERNAL_ID / no sensible | Ya parte del contrato de Domain D (`JobAvailableResponse`) — no se dupla su clasificación aquí. |

**Congelado (reemplaza el freeze anterior)**:
`MINIMUM_AGENT_LOGIN_PAYLOAD = [ruc, sol_username, sol_password]`.
Payload conceptual (no implementado en E1-A.1):

```
{
  "ruc": "...",
  "sol_username": "...",
  "sol_password": "..."
}
```

Sin campos adicionales: nunca nombre/dirección del cliente, nunca
documentos, nunca cookies, nunca IDs de vault, nunca versiones de
clave criptográfica, nunca master password, nunca ninguna clave. Ningún
metadato de cliente más allá del RUC ya justificado tiene una ruta de
cifrado o una necesidad evidenciada en el modelo actual.

### 9.1 Higiene de cable para el RUC (sección 3 del encargo)

Aunque el RUC se clasifica `SENSITIVE_METADATA` y no `SECRET`
(criptográficamente hablando, nunca se cifra ni se deriva de material
de clave), el endpoint futuro debe aplicarle la **misma** higiene de
transporte que a `sol_username`/`sol_password`:

- Solo sobre mTLS ya autenticado, nunca en claro por otro canal.
- Nunca en logs, nunca en payload de auditoría.
- Nunca en URL/query string.
- Nunca cacheado (mismos headers `Cache-Control: no-store` de la
  sección 19).
- Nunca persistido en ningún registro process-local del broker (ni el
  grant, sección 5, ni el futuro lease de conexión, sección 8).

No se reclasifica como secreto criptográfico solo por esta higiene
compartida — la distinción `RUC=SENSITIVE_METADATA` vs.
`USERNAME/PASSWORD=SECRET` se mantiene explícita en todo este
documento y en el futuro código de E1-B (por ejemplo, en qué campos
exige `zeroize`/minimización de tiempo de vida más estricta: los
`SECRET` la exigen con la misma prioridad que hoy `vault_root_key`/
`credential_dek`; el RUC la recibe por higiene general de transporte,
no por ser criptográficamente sensible).

## 10. Semántica de memoria (freeze de la sección 26 del encargo)

`decrypt_credential_field` devuelve `str` — **inmutable en CPython**.
Se congela explícitamente, sin eufemismos:

- **No se garantiza zeroización física** de los `str` de Python
  (`sol_username`/`sol_password` en claro) — el recolector de basura,
  copias internas del intérprete, `copy-on-write` al bifurcar, memoria
  de intercambio (swap) y volcados de núcleo quedan **fuera** de este
  control, igual que ya documenta `sol_vault_crypto.zeroize`'s propio
  docstring para `vault_root_key`/`credential_dek`.
- **Sí se puede y debe**: minimizar el tiempo de vida (crear el `str`
  lo más tarde posible — justo antes de serializar la respuesta —, y
  soltar toda referencia local inmediatamente después, incluyendo
  dentro de un `finally`); nunca almacenar en una variable de módulo,
  atributo de instancia de larga vida, registro process-local, ni caché
  de ningún tipo; nunca en logs/auditoría/trazas de excepción; los
  búferes **mutables** intermedios (`bytearray`/`bytes` del propio
  `credential_dek` ya usan `zeroize` — reutilizar ese mismo mecanismo
  para cualquier búfer mutable adicional que E1-B introduzca).
- `PYTHON_SECURE_ZEROIZATION_GUARANTEED=NO`,
  `PLAINTEXT_MEMORY_LIFETIME_MINIMIZED=YES` (como objetivo de diseño,
  no como garantía criptográfica).

## 11. Orden exacto de entrega (congelado, revisado en E1-A.1)

1. Autenticar mTLS/huella/agente `ACTIVE`.
2. Validar `connection_id` vigente y **fijar (pin)** la conexión
   lógica para esta entrega (sección 8).
3. Cargar relaciones job/sesión/cliente/credencial de forma
   tenant-safe, confiando solo en el backend (nunca en el request del
   agente) — incluye resolver `Client.ruc` (sección 9).
4. Exigir job `CLAIMED` y sesión no terminal/no vencida.
5. Exigir un `SecretDeliveryGrant` válido (estado `AUTHORIZED`) para
   `(tenant_id, job_id)` — fallar sin mutar el registro si la clave no
   corresponde exactamente a este agente/tenant/job (sección 5.4).
6. Adquisición atómica `AUTHORIZED → IN_FLIGHT` (sección 5.4) — a
   partir de aquí, cualquier fallo termina en `INVALIDATED`, nunca en
   un retorno a `AUTHORIZED`.
7. Re-verificar `VaultUnlockRegistry` para el
   `(tenant_id, vault_id, user_id, app_session_id)` exacto del grant,
   con `operation=CREDENTIAL_DELIVERY` (sección 4) — nunca
   `VERIFY_ACCESS`.
8. Descifrar la credencial **justo a tiempo**, en memoria efímera.
9. Invocar `sol_agent_job_service.mark_credential_delivered(...)`
   dentro de la transacción de BD — única autoridad, nunca reabierta ni
   reimplementada.
10. Commit del consumo del gate de entrega.
11. Revalidar/preservar el lease de conexión + autorización del agente
    (detectar si el pin del paso 2 se invalidó mientras tanto).
12. Solo después del commit exitoso **y** de superar la revalidación
    del paso 11: serializar/escribir la respuesta de secreto sobre la
    misma petición mTLS ya autenticada.
13. Transición `IN_FLIGHT → CONSUMED` y eliminación del grant
    (irrevocable); soltar toda referencia al texto plano inmediatamente.

### 11.1 ¿Por qué descifrar antes del commit del gate? (sección 28 del encargo)

Si el descifrado falla (vault/clave/credencial no disponible), se
prefiere **no** consumir el gate y **no** enviar nada — un fallo de
descifrado nunca debe "gastar" la única oportunidad de entrega de la
sesión. Una vez que el texto plano existe con éxito en memoria efímera,
se consume el gate, se hace commit, y **solo entonces** se hace
observable externamente. La contrapartida asumida explícitamente: el
texto plano existe brevemente en memoria del backend **antes** del
commit del gate — ventana pequeña, en el mismo proceso ya confiable que
ya maneja `vault_root_key`/`credential_dek` (Bloque 5.1), sin nuevo
componente de confianza.

### 11.2 Análisis de órdenes alternativos

| Orden | Descripción | Veredicto |
|---|---|---|
| A. gate commit → decrypt → send | Si el descifrado falla después del commit, el gate ya se consumió sin que el secreto llegara nunca a existir observable — **desperdicia la única entrega posible por un fallo de descifrado no relacionado con autorización**. | Rechazado. |
| B. decrypt → gate commit → send | Ver sección 11.1 — solo gasta el gate cuando el secreto ya existe con éxito. Un fallo posterior a este punto (pasos 11 a 13) ya no puede "deshacer" el gate (correcto: ver sección 12/17 del encargo), pero al menos el fallo de descifrado nunca lo consume. | **Elegido.** |
| C. decrypt → send → gate commit | Un secreto podría transmitirse y luego el commit del gate fallar/hacer rollback, permitiendo teóricamente una re-entrega bajo la ilusión de que el gate seguía disponible. Prohibido explícitamente por el encargo. | Rechazado. |

`DECRYPT_BEFORE_GATE_COMMIT=YES`, `GATE_COMMIT_BEFORE_NETWORK_WRITE=YES`,
`NETWORK_WRITE_BEFORE_GATE_COMMIT=NO`.

## 12. Consumo del grant

- **Commit exitoso del gate** → grant consumido/eliminado
  irrevocablemente (paso 13 del orden exacto).
- **Fallo de seguridad terminal** (agente revocado, sesión terminal,
  conexión reemplazada de forma irreversible, huella desconocida) →
  eliminar el grant inmediatamente, nunca dejarlo disponible para un
  reintento silencioso.
- **Vencimiento por TTL** → eliminado por purga perezosa/explícita.
- **Reinicio del proceso** → desaparece (process-local, sección 6).
- **Fallo transitorio de BD antes del gate** (ej. error de conexión a
  Postgres durante el paso 9, no un rechazo de autorización): el grant
  **puede sobrevivir hasta su TTL** para permitir un reintento
  legítimo del mismo agente sin exigir nueva autorización humana —
  **decisión explícita**, distinta de un fallo de autorización/
  seguridad (que sí invalida el grant de inmediato). La distinción es
  intencional: un fallo transitorio de infraestructura no es evidencia
  de que la autorización original dejó de ser válida.

## 13. Semántica del gate de entrega (reafirmada, sin renombrar)

`credential_delivered_at` (Domain C) significa **únicamente**: el gate
transaccional de un solo uso fue consumido. **Nunca** prueba que el
agente recibió el texto plano, que la escritura TCP se completó, que el
agente lo parseó, que lo usó, ni que SUNAT autenticó. Ninguna
renombrada a "entregado con éxito" — el disclaimer ya explícito en el
docstring de `mark_credential_delivered` (Domain C) permanece la única
fuente de verdad semántica.

## 14. Fallos antes del gate (matriz, sección 32 del encargo)

| Tipo de fallo | Efecto en plaintext | Grant | Reintento permitido |
|---|---|---|---|
| Conexión obsoleta (paso 2/11) | Nunca se genera / nunca se envía | Invalidado | Requiere nuevo `HELLO` + repetir desde el paso 2 con la conexión vigente (misma autorización humana, si el grant seguía dentro de TTL — ver sección 12) |
| Agente revocado | Nunca se genera / nunca se envía | Invalidado | Ninguno — nueva sesión + nueva autorización |
| Vault bloqueada (paso 7) | Nunca se genera | Sobrevive hasta TTL (transitorio: el usuario puede volver a desbloquear su vault) | Sí, dentro del TTL del grant, si el unlock se restablece |
| Grant vencido | Nunca se genera | Ya no existe | Requiere nueva autorización humana |
| Job ya no `CLAIMED` (paso 4) | Nunca se genera | Invalidado | Nueva sesión (el job dejó ese estado por una razón durable de Domain C) |
| Sesión terminal (paso 4) | Nunca se genera | Invalidado | Nueva sesión |
| Fallo transitorio de BD (paso 7/9, antes de commit) | Nunca se envía | Sobrevive hasta TTL (sección 12) | Sí, reintento del agente dentro del TTL |
| Fallo de descifrado (paso 8) | Nunca existe con éxito | Invalidado (sección 12 — se trata como fallo de seguridad, ya que normalmente indica corrupción/incoherencia de versión de clave, no un problema transitorio) | Ninguno — nueva sesión |

## 15. Fallos después del gate (sección 20/31 del encargo)

Si el gate ya se comprometió (commit exitoso) pero:

- la escritura de la respuesta falla,
- la conexión se cae,
- la conexión fue reemplazada,
- el agente crashea,
- ocurre un timeout,

→ **NO hay re-entrega automática. NO hay una segunda respuesta de
credencial para la misma sesión.** El operador/usuario debe crear una
**nueva** sesión de acceso SOL y autorizar explícitamente de nuevo.
Deliberadamente conservador. `POST_GATE_TRANSPORT_FAILURE_REQUIRES_NEW_SESSION=YES`,
`AUTOMATIC_SECRET_REDELIVERY=NO`.

## 16. Carreras (freeze explícito)

### 16.1 Reemplazo de conexión

- **Antes** de consumir el gate: no decrypt/no send; el grant puede
  invalidarse (sección 14).
- **Después** de consumir el gate pero **antes** de que el secreto sea
  observable externamente (fallo detectado en el paso 11): no se envía
  a la conexión obsoleta; el gate **permanece consumido**; **sin**
  re-entrega automática; se requiere nueva sesión (idéntico a sección
  15 — desde la perspectiva del gate, este es solo un caso particular
  de "fallo después del gate").

### 16.2 Revocación

Agente `ACTIVE` al iniciar la petición, `REVOKED` antes de la escritura
del secreto: fail-closed — nunca enviar texto plano tras conocerse la
revocación. Si el gate ya se consumió: igual que 16.1, sin
reintento/redelivery automático.

### 16.3 Cancelación/expiración de sesión

Autoridad: el estado de BD de Domain C. Ningún secreto puede enviarse
para una sesión terminal. Si la transición terminal gana **antes** del
gate: el gate nunca se consume (la propia lógica de
`mark_credential_delivered`, ya productiva, rechaza sesiones
terminales/vencidas — Domain C no necesita cambios). Si el consumo del
gate gana primero: una cancelación posterior **nunca** revierte el
gate (mismo principio de "terminal es terminal" ya congelado en el
State Machine de Domain A.1). Antes de la escritura de red: revalidar
el mínimo estado necesario (sesión no terminal) inmediatamente antes
del paso 12, para que una autorización obsoleta no pueda resultar en
liberación de secreto.

### 16.4 Entregas concurrentes

A lo sumo **una** puede pasar el gate de BD de Domain C — la misma
garantía transaccional ya usada por
`ux_sol_agent_jobs_one_delivery_per_session` (índice único parcial,
Domain A.2). La otra recibe un resultado genérico de
"ya-consumido/no-disponible", nunca una distinción de motivo. **Nunca**
un lock de Python como única autoridad — el índice de BD ya es el
backstop real, igual que el patrón ya usado en `claim_job`/
`AgentCertificateFingerprintConflictError`.

## 17. Matriz de crash (sección 45 del encargo)

| Caso | Gate consumido? | Grant sobrevive reinicio? | Redelivery automática? | Nueva sesión requerida? |
|---|---|---|---|---|
| A. antes de decrypt | No | No (process-local) | No | Si el job ya no es reclamable tras el reinicio, sí |
| B. después de decrypt, antes del commit del gate | No | No | No | No necesariamente — un nuevo intento del agente, tras reautenticar, puede reintentar desde el paso 1 si el grant original seguía vigente dentro de su TTL antes del crash; tras el reinicio el grant ya no existe, así que en la práctica requiere nueva autorización si el TTL no alcanza a sobrevivir el reinicio (nunca sobrevive, por definición process-local) |
| C. después del commit del gate, antes de la escritura HTTP | Sí | No | **No** | Sí — mismo principio que sección 15 |
| D. durante la escritura HTTP | Sí (ya ocurrió en el paso 10, antes del 12) | No | **No** | Sí |
| E. después de la escritura HTTP completa | Sí | No (ya consumido, irrelevante) | No aplica — entrega ya completada desde la perspectiva del gate | No, salvo que el agente igual no haya podido usarlo (fuera de control del backend, ver sección 18 de este documento) |

## 18. Semántica de reinicio (sección 44 del encargo)

El reinicio del broker/backend pierde: `ConnectionRegistry`,
`SecretDeliveryGrantRegistry`, `VaultUnlockRegistry` (los tres
process-local). **Preserva**: estado de sesión/job en BD,
`credential_delivered_at`, datos cifrados de la vault. Tras el
reinicio: **sin** reconstrucción/redelivery automática de secreto. Un
job `CLAIMED` sin gate consumido puede requerir nuevo `HELLO` +
reautorización humana explícita (el grant desapareció). Un gate ya
consumido exige nueva sesión SOL (sección 15) — el reinicio no cambia
esa regla, solo la reafirma.

## 19. Contrato de respuesta (arquitectura, sección 25 del encargo)

Modelo estricto futuro (`extra="forbid"`, mismo patrón ya productivo en
`app/broker/protocol.py`), sin `__repr__` que exponga texto plano
(mismo patrón ya productivo en `JobCreateResult`/`PairingCreateResult`
de Domain C), nunca serializado en logs. Headers propuestos:
`Cache-Control: no-store`, `Pragma: no-cache`,
`X-Content-Type-Options: nosniff`. Sin compresión HTTP. Sin consumo de
navegador (ya cubierto estructuralmente por las defensas Host/Origin/
Sec-Fetch-Site/header-de-protocolo de Domain D.1, que aplican a
**todos** los endpoints del listener operacional sin excepción).

**Tamaño**: cota estricta y pequeña, deliberadamente **menor** que
`MAX_MESSAGE_BYTES` genérico del broker (`app/broker/protocol.py`) —
dos campos de texto corto no necesitan 16 KB. Propuesto: **1024 bytes**
como cota superior de cuerpo de respuesta para este endpoint
específico (ningún certificado/documento se agrupa con el payload de
credencial).

**Timeouts**: mismo mecanismo de timeouts ya congelado y verificado
empíricamente en Domain D.1 (`sol_broker_read_timeout_seconds`,
`sol_broker_idle_timeout_seconds`) — un cliente local lento no debe
poder fijar memoria de secreto indefinidamente. El lease de conexión
(sección 8) debe tener su propio TTL acotado, corto (orden de segundos,
nunca el TTL completo del grant de 60s), para que un cliente que nunca
completa la lectura de la respuesta no mantenga el pin activo más de lo
necesario.

## 20. Logging y auditoría (secciones 37–39 del encargo)

**Auditoría durable**: `CREDENTIAL_DELIVERY_GATE_CONSUMED` (ya
existente, Domain C.1) sigue siendo suficiente para el consumo del
gate — no se propone renombrar ni añadir `CREDENTIAL_DELIVERED` (no
demostrable). **Ratificado en Domain E1-A.1, semántica corregida en
Domain E1-A (checkpoint final)** (todavía no implementado):
`CREDENTIAL_DELIVERY_AUTHORIZED`, registrada cuando el humano/backend
aprueba la solicitud de autorización de entrega (sección 2) —
justificación: es el único punto donde una decisión humana explícita
ocurre, distinta del consumo transaccional automático del gate; sin
ella, no queda ningún rastro durable de "quién autorizó, cuándo"
separado del acto técnico de consumo.

**Semántica exacta ratificada (corregida)**:

```
CREDENTIAL_DELIVERY_AUTHORIZED
=
el usuario autorizado aprobó con éxito una autorización de entrega de
credencial de corta vida para la sesión/job SOL vinculados.

Prueba que la DECISIÓN DE AUTORIZACIÓN quedó registrada de forma
durable.
```

**No prueba**:

- que el `SecretDeliveryGrant` process-local llegó a materializarse;
- que el grant permaneció disponible;
- que la credencial fue descifrada;
- que el gate de entrega de BD fue consumido;
- que ocurrió transmisión de red;
- que el agente recibió el texto plano;
- que ocurrió autenticación en SUNAT.

**Corrección explícita respecto a la redacción anterior de E1-A.1**: la
versión previa de este documento afirmaba que la acción significaba
"que un usuario autorizado solicitó con éxito la **creación** de un
`SecretDeliveryGrant`" — redacción **incorrecta**, ya reemplazada por
el texto de arriba. La auditoría durable (PostgreSQL) y el
`SecretDeliveryGrantRegistry` (memoria de proceso) **no** comparten una
transacción atómica (sección 20.1) — afirmar que el evento de auditoría
prueba la creación del grant habría sido una afirmación de atomicidad
distribuida que el diseño real no ofrece. Es una acción **distinta e
independiente** de `CREDENTIAL_DELIVERY_GATE_CONSUMED`, que permanece
sin cambios y sigue siendo la única autoridad durable sobre el consumo
del gate — las dos acciones conviven, nunca se fusionan ni se
sustituyen entre sí.

`NEW_AUDITACTION_RATIFIED=CREDENTIAL_DELIVERY_AUTHORIZED` (ratificada
para E1-B; su implementación real —tabla, migración de enum de string
libre, `AuditAction`— sigue sin ocurrir en esta ronda, mismo patrón
aditivo sin DDL ya usado para el resto del catálogo).
`CREDENTIAL_DELIVERY_AUTHORIZED_SEMANTICS=AUTHORIZATION_APPROVED_NOT_GRANT_OR_TRANSPORT_PROOF`.
**Ninguna otra** `AuditAction` nueva queda autorizada en este freeze.

### 20.1 Orden fail-closed de auditoría del grant — sin transacción distribuida (sección 4/5 del encargo E1-A final)

Se congela explícitamente que **no existe, y nunca se afirma que
exista, una transacción atómica distribuida** entre el estado de
auditoría de PostgreSQL y el `SecretDeliveryGrantRegistry` en memoria
de proceso — son dos sistemas de persistencia completamente distintos
(uno durable y transaccional, el otro volátil y process-local) sin
protocolo de compromiso de dos fases entre ambos. El diseño de
seguridad evita necesitar esa atomicidad imposible mediante un
**orden estricto de dos pasos**, nunca simultáneo:

```
validar actor/sesión/job (sección 2/3)
  ↓
registrar CREDENTIAL_DELIVERY_AUTHORIZED de forma durable (BD, commit)
  ↓
SOLO SI el commit de auditoría tuvo éxito:
  crear el grant (SecretDeliveryGrantRegistry, estado AUTHORIZED)
```

Si el registro durable de auditoría falla (por ejemplo, un error de
escritura en `audit_log`), la operación completa debe fallar **sin**
crear el grant — nunca un grant "silencioso" sin rastro de quién lo
autorizó.

**Caso explícitamente aceptado** (no un defecto, una consecuencia
congelada de la falta de atomicidad distribuida): si el proceso
crashea **después** del commit de auditoría pero **antes** de que el
grant se materialice en el registro process-local —

- la fila de auditoría **puede permanecer** (ya se comprometió en
  PostgreSQL, autoridad durable);
- **ningún** grant existe (nunca se llegó a crear, o se perdió en el
  crash — process-local, sección 6);
- **ningún** texto plano existe;
- **ningún** gate de entrega de BD fue consumido;
- **ninguna** entrega automática de credencial ocurre.

Esto es aceptable precisamente porque `CREDENTIAL_DELIVERY_AUTHORIZED`
significa "la autorización fue aprobada", nunca "el grant quedó
instanciado con certeza" — la corrección semántica de esta sección es
lo que hace seguro este caso límite: ningún código ni ninguna
interpretación posterior debe tratar la presencia de esta fila de
auditoría como prueba de que existe (o existió) un grant utilizable.
Un usuario cuya autorización quedó auditada pero cuyo grant nunca se
materializó simplemente no ve ningún efecto (el agente nunca podrá
completar una entrega, porque no hay grant) — no es un estado inseguro,
es un estado inerte que requiere que el usuario reintente la
autorización.

`AUDIT_FAILURE_CAN_LEAVE_ACTIVE_GRANT=NO`,
`AUDIT_AND_PROCESS_LOCAL_GRANT_ATOMIC_TRANSACTION=NO`. Arquitectura
únicamente — no implementado en este checkpoint.

**Logging del endpoint de secreto**: allowlist idéntica al resto del
broker (`tenant`/`session_id`/`job_id`/`agent_id`/código de evento o
error estable) — nunca campos de credencial, nunca RUC, nunca username/
password, nunca cuerpo de request/response, nunca contenido del grant,
nunca material de unlock/claves, nunca el `connection_id` en claro
(mismo principio ya productivo en `_log_event` de Domain D). El logging
de acceso del broker (`_BrokerHttpHandler.log_message`, ya
sobrescrito a no-op en Domain D) ya no logea URL/headers/body crudos —
Domain E1-B no debe revertir eso para este endpoint.

## 21. Reconocimiento del agente (sección 43 del encargo)

Ningún ACK del agente puede formar parte de la semántica durable de
entrega — un ACK no prueba uso seguro. Si un futuro protocolo añade un
ACK, es puramente informativo/telemetría; el gate ya quedó consumido
antes de que la respuesta fuera observable externamente,
independientemente de si el agente confirma.

## 22. Límite de agente comprometido (sección 49 del encargo, honestidad explícita)

Una vez que una credencial autorizada llegó legítimamente a un proceso
de agente confiable, **los controles de backend no pueden impedir que
un proceso de agente totalmente comprometido la lea en memoria**. Las
mitigaciones de este documento reducen: quién puede recibirla, cuándo,
con qué frecuencia, cuánto tiempo vive, y si persiste — nunca eliminan
por completo el riesgo de un agente comprometido después de una entrega
legítima. No se afirma protección imposible.

## 23. Resumen de decisiones de freeze (sección 55 del encargo, actualizado en E1-A.1)

| Decisión | Valor congelado |
|---|---|
| `SECRET_DELIVERY_GRANT_MODEL` | Registro process-local, clave `(tenant_id, job_id)`, sin token hacia el agente, máquina de estados `AUTHORIZED→IN_FLIGHT→CONSUMED/INVALIDATED` (sección 5.4) |
| `SECRET_DELIVERY_GRANT_STATES` | `AUTHORIZED`, `IN_FLIGHT`, `CONSUMED`, `INVALIDATED` |
| `SECRET_DELIVERY_GRANT_TTL` | 60s (acotado además por expiración de sesión/job) |
| `SECRET_DELIVERY_GRANT_KEY` | `(tenant_id, job_id)` |
| `SECRET_DELIVERY_SINGLE_PROCESS_POLICY` | Sí — broker debe correr en el mismo proceso que `VaultUnlockRegistry` |
| `VAULT_UNLOCK_BINDING` | `(tenant_id, vault_id, user_id, app_session_id)`, heredado sin cambios de Bloque 5.1 |
| `VAULT_OPERATION_FOR_SECRET_DELIVERY` | `CREDENTIAL_DELIVERY` (nuevo valor de `ALLOWED_OPERATIONS`, sin migración, sin permiso RBAC nuevo) — nunca reutiliza `VERIFY_ACCESS` |
| `ACTOR_BINDING` | El autorizador debe ser `requested_by_user_id` de la sesión |
| `MINIMUM_AGENT_LOGIN_PAYLOAD` | `ruc` (SENSITIVE_METADATA, desde `Client.ruc` vía `session.client_id`), `sol_username` (SECRET), `sol_password` (SECRET) |
| `SECRET_DELIVERY_ENDPOINT_METHOD` | `POST` |
| `SECRET_DELIVERY_ENDPOINT_PATH_DRAFT` | `/agent/jobs/{job_id}/credential` |
| `CONNECTION_LEASE_POLICY` | Pin/lease de corta vida sobre `ConnectionRegistry`, cubriendo desde la autenticación (paso 2) hasta la escritura de respuesta (paso 12) |
| `ATOMIC_GRANT_IN_FLIGHT_ACQUISITION` | Sí — compare-and-swap process-local antes de cualquier descifrado; nunca sustituye el gate de BD |
| `WRONG_AGENT_CAN_INVALIDATE_LEGITIMATE_GRANT` | No — fallos de identidad/routing antes de localizar el grant correcto nunca mutan el registro |
| `DECRYPT_VS_GATE_ORDER` | Descifrar → commit del gate → enviar (orden B) |
| `POST_GATE_TRANSPORT_FAILURE_POLICY` | Sin redelivery automática; nueva sesión requerida |
| `PRE_GATE_RETRY_POLICY` | Ver matriz sección 14 — depende del tipo de fallo |
| `CONCURRENT_DELIVERY_POLICY` | Adquisición atómica `IN_FLIGHT` (proceso) + gate de BD de Domain C (autoridad final); a lo sumo un descifrado, un commit de gate, una respuesta de secreto |
| `RESTART_POLICY` | Todo estado process-local se pierde; sin reconstrucción de secreto |
| `AUDIT_POLICY` | `CREDENTIAL_DELIVERY_GATE_CONSUMED` suficiente para el gate; `CREDENTIAL_DELIVERY_AUTHORIZED` **ratificado**, semántica = "autorización aprobada" (nunca "grant materializado" ni prueba de transporte, sección 20, aún no implementado); auditoría durable debe preceder la disponibilidad del grant, sin transacción atómica distribuida entre BD y registro process-local (`AUDIT_FAILURE_CAN_LEAVE_ACTIVE_GRANT=NO`, `AUDIT_AND_PROCESS_LOCAL_GRANT_ATOMIC_TRANSACTION=NO`, sección 20.1) |
| `MEMORY_ZEROIZATION_LIMIT` | Sin garantía física para `str` inmutables; minimización de tiempo de vida como objetivo, no garantía |
