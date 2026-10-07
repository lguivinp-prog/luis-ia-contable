# Block 5.2 — Domain E2: Agente Windows nativo

Documento mínimo y coherente para el runtime productivo del Agente Windows
(`backend/windows_agent/`), endpoint del IPC agente↔broker ya congelado en
Domain D/E1-B/E1-B.1. No duplica los documentos ya congelados de Domain E1
(`BLOCK5_2_SECRET_DELIVERY_ARCHITECTURE.md`, `BLOCK5_2_SECURITY_MODEL.md`) —
remite a ellos donde aplica.

## 1. Alcance de este dominio

Implementado: identidad local, pairing, mTLS operacional (HELLO/heartbeat/
descubrimiento/reclamo de job/cancelación/capacidad de solicitud de
credencial), ciclo de vida del proceso.

Explícitamente **fuera** de alcance: automatización de navegador
(Playwright/Chromium), acceso a SUNAT, invocación automática de la
capacidad de solicitud de credencial tras un reclamo de job (ver sección 5).

## 2. Arquitectura y capas

```
windows_agent/
    __main__.py       # CLI: pair | run | status
    config.py          # AgentConfig (no secreto) + directorio de datos
    identity.py         # WindowsDpapiIdentityStore (ver ADR-0072)
    single_instance.py   # mutex nombrado de Windows
    pairing.py            # flujo de primer pairing
    broker_client.py       # cliente mTLS + backoff + capacidad de credencial
    runtime.py               # maquina de estados + bucle de jobs
    logging_setup.py          # logging estructurado sin secretos
```

`app/broker/product_startup.py` (backend) cierra el hallazgo de Domain
E1-B.1 (`BROKER_PRODUCT_STARTUP_ENTRYPOINT_EXISTS=NO`): arranque opcional
del broker DENTRO del proceso backend, guardado tras
`settings.sol_broker_enabled` (`False` por defecto), invocado desde
`@app.on_event("startup")`/`"shutdown"` en `app/main.py` — nunca un
proceso separado (`VaultUnlockRegistry`/`SecretDeliveryGrantRegistry` son
memoria de un único proceso, ver `sol_vault_worker_gate.py`/ADR-0071).

## 3. Identidad local y almacenamiento

Ver **ADR-0072** para la decisión completa (DPAPI de usuario + ACL +
PKCS#8 cifrado + frase de paso DPAPI entregada a OpenSSL por callback — la clave privada jamás se escribe en claro a disco —, hallazgos reales de `ctypes` corregidos tras
validación en Windows real).

**Alcance DPAPI (sección 15 del encargo E2)**: protege *exclusivamente*
la identidad TLS del agente (clave privada de certificado mTLS). Nunca
sustituye ni modifica la jerarquía criptográfica de la bóveda SOL (Bloque
5.1: `vault_root_key`/`credential_dek`/contraseña maestra/secreto de
recuperación) — ningún material de esa jerarquía se persiste jamás con
este mecanismo ni por ningún otro camino de `windows_agent/`.

Directorio de datos: `%LOCALAPPDATA%\LuisIAContable\SolAgent` — nunca el
árbol del repositorio, Descargas, Escritorio, ni el directorio de trabajo
actual. Lista blanca de contenido persistente (`windows_agent/config.py`):
id de agente, certificado, clave privada cifrada (PKCS#8) + frase de paso protegida por DPAPI, metadata de
protocolo/timeouts no secreta. Nunca RUC/usuario/clave SOL, token de job,
`connection_id`, ni material de la bóveda.

## 4. Protocolo y ciclo de vida en ejecución

Reutiliza el protocolo ya congelado (`app/broker/protocol.py`, Domain D):
`POST /pairing/csr` (bootstrap), `POST /agent/hello`, `POST
/agent/heartbeat`, `GET /agent/jobs/next`, `POST /agent/jobs/{id}/claim`,
`POST /agent/jobs/{id}/status`, `POST /agent/error`, `POST
/agent/jobs/{id}/credential` (Domain E1-B).

`connection_id`: memoria de proceso únicamente (`AgentRuntime.
_connection_id`) — nunca disco/registro/logs/entorno/CLI. Se pierde por
completo al reiniciar el agente; queda inválido de inmediato si el broker
reinicia (Domain D.1, `NEW_REPLACES_OLD`/lease de secreto sin cambios).

Backoff (`windows_agent/broker_client.py::call_with_backoff`): secuencia
acotada `1s/2s/4s/8s` + jitter aleatorio (`random.uniform(0, 0.5)`),
reutilizando la filosofía ya validada en Spike B — solo para fallos de
TRANSPORTE (`httpx.TransportError`/`TimeoutException`), nunca para
errores de aplicación (401/409 vía `raise_for_status()`, siempre fuera
del reintento).

Estados locales (`windows_agent/runtime.py::AgentState`): `UNPAIRED`,
`DISCONNECTED`, `CONNECTING`, `CONNECTED`, `REVOKED`, `STOPPING` —
puramente en memoria, nunca un enum de BD/migración nueva. El backend
sigue siendo la única autoridad de `sol_agents.status`.

## 5. Invariante crítico: no auto-solicitud de credencial

`AgentRuntime.poll_and_claim_once()` **nunca** invoca la capacidad de
solicitud de credencial tras un reclamo exitoso — no existe ninguna ruta
de código que conecte "job reclamado" con "solicitar credencial". La
capacidad (`OperationalBrokerClient.fetch_credential_for_job`) existe
para que **Domain E3** la invoque explícitamente cuando el navegador
supervisado esté listo para consumirla. Verificado por prueba dedicada
con espía sobre el método interno real (`test_N_O_P_W_...`,
`tests/integration/windows_agent_e2/`), nunca inferido solo de la
ausencia de una respuesta.

La política de reintento del endpoint de credencial es **deliberadamente
distinta** de los demás endpoints (heartbeat/descubrimiento pueden
reintentar con backoff; la credencial nunca) — cualquier ambigüedad de
transporte en ese endpoint específico falla cerrado, sin reintento
automático, porque es security-sensitive y de un solo uso.

## 6. Política de memoria del secreto

`CredentialMaterial` (`broker_client.py`) es un contenedor MUTABLE con
`__slots__` — el consumidor lo usa dentro de un bloque `with
client.fetch_credential_for_job(...) as credential:`, y al salir del
bloque, `_clear()` suelta activamente las referencias (`None`). Nunca se
asigna a un campo de larga vida del runtime (`AgentRuntime` nunca tiene
un atributo `current_credential`).

`PYTHON_SECURE_ZEROIZATION_GUARANTEED=NO` — los objetos `str` de Python
son inmutables; esto acota la VIDA DE LA REFERENCIA, nunca pretende un
borrado físico de memoria. `LONG_LIVED_AGENT_SECRET_REFERENCE_FOUND=NO`
(verificado por inspección de código + prueba de residuo, ver el informe
final de Domain E2) es la afirmación honesta que sí se sostiene.


## 7. Validación manual en Windows real

> **E2.1-A (corrección):** la ejecución en Windows real descrita abajo validó el diseño ANTERIOR (puente
> efímero de disco), que fue reemplazado (ADR-0072). Re-ejecutar `_manual_windows_validation.py` contra el
> diseño actual está **pendiente** (NOT_RUN): no hay un runtime Python de Windows establecido en el proyecto
> (la suite corre en Docker/Linux). Las partes DPAPI/mutex siguen siendo las mismas.

Ver el informe final de Domain E2 para el bloque de evidencia completo.
Resumen: `windows_agent/_manual_windows_validation.py` (nunca importado
por el paquete productivo ni por pytest) ejecutado directamente en este
host Windows 11 real confirmó, contra la API real de Windows:

- round-trip DPAPI real (`CryptProtectData`/`CryptUnprotectData`);
- el flujo completo de `WindowsDpapiIdentityStore` (guardar/cargar
  identidad, construir un `ssl.SSLContext` real; E2.1-A: re-validación pendiente de ejecutar —
  el diseño anterior con puente efímero fue reemplazado);
- `WindowsSingleInstanceGuard` — un segundo proceso con el mismo mutex es
  bloqueado; tras liberar, un proceso nuevo sí puede adquirirlo.

Esta ejecución real expuso y corrigió dos bugs reales de `ctypes`
(prototipos de `LocalFree` faltantes; `GetLastError()` no fiable sin
`use_last_error=True`) — invisibles en cualquier prueba dentro del
contenedor Linux, donde estas rutas de código son estructuralmente
inalcanzables. Confirma la necesidad de la validación manual en Windows
real más allá de la suite automatizada.

El flujo completo de protocolo (pairing real/mTLS real/broker real/
endpoint real de Domain E1-B) se validó con PostgreSQL real y mTLS real
dentro del contenedor de pruebas Linux, usando el código PRODUCTIVO de
`windows_agent/` sin mocks del lado del agente — la limitación honesta
declarada es que el proceso del BROKER en esas pruebas corre en Linux
(el mismo contenedor ya usado por toda la suite de Domain D/E1-B), no en
un broker productivo alojado nativamente en Windows.

## 8. Windows native acceptance evidence (E2.1-A / E2.1-B)

**Entorno.** Windows 11 x64 (10.0.26300), Python 3.13.14, venv dedicado `.venv-win-e2` (excluido localmente vía
`.git/info/exclude`) con solo `cryptography==43.0.3` y `httpx==0.28.1`. Regresión canónica del backend en Docker
(Python 3.12.14).

**Topología.**
- *Broker*: el broker REAL (PostgreSQL real, mTLS real) corre en un contenedor Docker DESECHABLE
  (`tests/windows_native_acceptance/harness_serve_broker.py`), con los puertos 18443/18444 publicados en
  `127.0.0.1` del host Windows.
- *Agente*: nativo en Windows — DPAPI real, mutex nombrado real, `python -m windows_agent run` como subproceso real
  (`LOCALAPPDATA` apuntando a un directorio de trabajo desechable) + orquestador
  `harness_windows_orchestrator.py` y `harness_secret_holder.py`.
- Solo credenciales/identidades ficticias de prueba (`TEST_SOL_USER`/`TEST_SOL_PASSWORD_DO_NOT_USE`). Sin SOL real, sin SUNAT.

**Excepción de arnés (documentada explícitamente).** El arnés enlazó TEMPORALMENTE el listener del broker a
`0.0.0.0` DENTRO del contenedor Docker desechable (sustituyendo `_ALLOWED_HOSTS` en memoria de ese proceso de
pruebas), únicamente para que Docker pudiera publicarlo en `127.0.0.1` del host Windows. **El código del broker de
producto NO se modificó.** Es comportamiento exclusivo del arnés de validación y NO relaja el requisito
loopback-only del producto (`BrokerServer` sigue rechazando hosts no-loopback). Limitación honesta: el broker de
estas pruebas corre en Linux, no nativo en Windows.

**Reproducción.**
```
docker compose run -d --rm --no-deps --name e21b-broker -p 127.0.0.1:18443:18443 -p 127.0.0.1:18444:18444 \
  -v <dir_arnes>:/harness backend python -m pytest /harness/harness_serve_broker.py -p no:cacheprovider \
  --rootdir=/app -c /app/pyproject.toml -p tests.conftest -p tests.integration.broker.conftest
# (copiar harness_serve_broker.py a <dir_arnes>; crear <dir_arnes>/out)
cd backend
..\.venv-win-e2\Scripts\python.exe tests\windows_native_acceptance\harness_windows_orchestrator.py <dir_arnes> <dir_trabajo>
```

**Resultados (todos PASS).**

| Verificación | Resultado |
|---|---|
| DPAPI real; mutex nombrado real | PASS (`_manual_windows_validation`: round-trip DPAPI; segundo proceso bloqueado, exit 3) |
| Pairing real (Windows) / mTLS / HELLO / heartbeat | PASS |
| Reinicio del agente ×2 con la misma identidad | PASS (sin ninguna escritura de archivos en los reinicios) |
| Bypass de proxy | PASS (`HTTPS_PROXY`/`HTTP_PROXY`/`ALL_PROXY` inválidos no afectan al agente; `trust_env=False`) |
| L — broker caído al arrancar | PASS: proceso vivo; esperas 2 s → 4 s → 8 s (acotadas hasta 30 s); 0.0 s de CPU en 10 s de inactividad; hilos 4→4; 0 solicitudes de credencial; identidad intacta |
| M-A — el broker vuelve | PASS: el MISMO proceso conecta con una conexión nueva |
| M-B — reinicio del broker | PASS: mismo proceso, `connection_id` NUEVO (huella distinta); nunca se reutiliza el id/lease anterior |
| Q — cancelación | PASS: `job_cancellation_observed` registrado, job liberado; 0 solicitudes de credencial en el broker (contador acumulado desde L) |
| AO — kill abrupto (TerminateProcess externo) | PASS: identidad cifrada sigue usable; reinicio conecta por mTLS; mutex recuperado; 0 claves/frase en claro en disco |
| AP — secreto SOL de prueba vivo en memoria + kill + reinicio | PASS: credencial entregada al proceso holder y proceso terminado en seco; 0 residuos en archivos/logs; el agente reiniciado NO solicita credencial por su cuenta |
| Archivos de clave en claro creados / residuos | 0 / 0 (auditoría `sys.addaudithook` de escrituras + vigilante + barrido de contenido; archivos persistentes = clave PKCS#8 cifrada, frase protegida por DPAPI, certificado público, metadata no secreta, config, log) |

**Contrato de cancelación.** El contrato Domain C/D no define un mensaje de confirmación del agente
(`SessionCancelNotice`: "el agente no necesita confirmar recepción"). "Reportar" significa observar el
`cancel_notice` del heartbeat, liberar el job localmente y registrar el evento estructurado
`job_cancellation_observed`. No se añadió ningún protocolo nuevo.

**Defectos reales corregidos en E2.1-B** (hallados al auditar el producto): (1) `python -m windows_agent run` moría
si el broker no estaba disponible al arrancar; (2) tras perder/reiniciar el broker, el bucle quedaba inutilizado
(`connection_id=None` + `assert`); (3) el `cancel_notice` del heartbeat nunca se procesaba. Ahora
`AgentRuntime.run_forever` conecta, hace heartbeat/reclamo, reconecta con HELLO nuevo y escala de espera acotada
(2/4/8/16/30 s), y procesa cancelaciones.

## 9. Domain E3 — Autenticación supervisada hasta el landing (`AUTHENTICATE_TO_LANDING_ONLY`)

> **Estado:** implementada y validada contra un **portal local determinista** con Playwright/Chromium real en
> Windows. **`SUNAT_LIVE_ACCEPTANCE=NOT_RUN`**: no se accedió a SUNAT real ni se usaron credenciales reales. La
> evidencia de Spike A sustenta el DISEÑO (hosts, ruta, etiquetas, título); no es aceptación de producto de E3.

**Alcance exacto.** Abrir el punto de entrada autorizado → identificar el login → obtener la credencial ya
autorizada por el contrato E1-B/E2 → llenar solo los campos requeridos → enviar **una vez** → esperar (si hace
falta) una acción humana → determinar si se llegó al landing aceptado → reportar → **detener la automatización**.
No hay Buzón, listado/lectura de mensajes, descargas, declaraciones, rectificaciones ni pagos; no hay clics ni
navegación posteriores al landing (`POST_LANDING_AUTOMATED_CLICKS=0`).

**Estructura** (`backend/windows_agent/`):

| Módulo | Rol |
|---|---|
| `portal_contract.py` | Contrato de identidad del portal (hosts, ruta, título, etiquetas) + `AuthOutcome` + mapeo a códigos terminales de Domain C |
| `portal_identity.py` | Clasificación **pura** de la superficie (Page/Frame) — sin Playwright |
| `browser_executor.py` | Ejecutor Playwright (navegador visible, contexto efímero) + `run_authentication` (frontera de credencial) |
| `runtime.py` | `authenticate_claimed_job` (heartbeats durante la espera, cancelación, reporte) |

**Principio: Page/Frame es la fuente de verdad.** El éxito exige simultáneamente host exacto, esquema `https`,
prefijo de ruta, título y ausencia de formulario de login visible; el tráfico de red nunca prueba autenticación.
Un login dentro de un frame, hosts desconocidos (incluidos hosts SUNAT conocidos pero no autorizados), superficies
ambiguas o un landing sin la identidad esperada fallan cerrado como `PORTAL_CHANGED`
(`UNKNOWN_PORTAL_STATE_CONTINUES_AUTOMATION=NO`); jamás se prueban selectores alternativos.

**Evidencia de Spike A usada** (`spike-phase5-sol-portal-recon-a2-td-v1`; los puntos de login fueron corregidos por LIVE-A1, ver sección 12): host de credenciales
`api-seguridad.sunat.gob.pe` (exacto, Page de nivel superior); ruta "MIS TRAMITES Y CONSULTAS" → "Ingresar";
modo "Ingresa por RUC"; campos "Usuario"/"Contraseña", botón "Iniciar sesión"; landing `e-menu.sunat.gob.pe`
`/cl-ti-itmenu/AutenticaMenuInternet.htm`, título "SUNAT - Menú SOL".

**LIVE_UNVERIFIED (hallazgo explícito de E3; SUPERADO por LIVE-A1/A2, ver la sección 12 — el contrato actual ya no usa etiquetas, "Ingresa por RUC" ni texto en mayúsculas).** Spike A nunca automatizó el llenado (el humano escribía todo), por lo que
**no** estableció: ids/nombres DOM, la etiqueta del campo RUC (`"RUC"` asumida por el contrato), los textos de
error de credenciales (vacíos en producción ⇒ una página de login que persiste tras el envío se trata como
`WAITING_FOR_USER`, nunca como éxito ni se reenvía) ni indicador de challenge ni la estructura exacta de las
tarjetas de la portada (el enlace de entrada es "el primer 'Ingresar' que sigue, en orden de documento, al
encabezado 'MIS TRAMITES Y CONSULTAS'"). Si alguno no coincide en vivo, el ejecutor falla cerrado
(`PORTAL_CHANGED`) **antes** de pedir la credencial. La aceptación en vivo (no autorizada aún) debe confirmarlos o
corregir el contrato.

**Navegador.** Visible (`headless=False`); `BrowserContext` efímero (`chromium.launch` sin `user_data_dir`);
sin `storage_state`, sin trazas, sin video, sin capturas, descargas deshabilitadas; contexto y navegador se
destruyen siempre (éxito, fallo o cancelación); si la limpieza falla un éxito pasa a `CLEANUP_FAILED`. Cada sesión
parte de un estado de navegador nuevo.

**Frontera de secreto y un solo envío.** La credencial se pide por el endpoint existente **una sola vez**, solo
cuando el login ya fue identificado y la sesión no está cancelada; se escribe en los campos, se marca el envío
(`credential_submissions`, máximo 1 — `LOGIN_SECRET_SUBMISSION_MAX_PER_SESSION=1`) y las referencias se sueltan de
inmediato. Tras el envío no hay reintento de login, reenvío de contraseña ni de challenge. Un fallo posterior a la
entrega **no** re-solicita el secreto (entrega de un solo uso, E1-B): se requiere una sesión nueva. Reintentos
acotados (máx. 2 intentos) solo para fallos transitorios de arranque/navegación **previos** a la credencial.

**Challenge humano.** `CHALLENGE_AUTOMATION=NONE`: no se resuelve, no hay OCR ni IA, no se capturan. Se espera
(`WAITING_FOR_USER`, tope configurable) con el navegador visible y se re-evalúa la identidad Page/Frame; el
runtime sigue enviando heartbeats durante la espera (también entregan `cancel_notice`). Tiempo agotado ⇒
`EXPIRED` (`HUMAN_CHALLENGE_TIMEOUT`).

**Resultados → contratos existentes (sin enums ni migraciones nuevas).**

| `AuthOutcome` | Código terminal (Domain C) | Reporte del agente |
|---|---|---|
| `AUTH_SUCCEEDED` | — | `report_status(COMPLETED)` |
| `INVALID_CREDENTIALS` | `AUTH_FAILED` | `report_error` + `report_status(FAILED)` |
| `WAITING_FOR_USER` | — (no terminal) | evento estructurado local |
| `PORTAL_CHANGED` | `PORTAL_CHANGED` / `AUTH_HOST_CHANGED` (login en frame) | error + `FAILED` |
| `TRANSIENT_NAVIGATION_FAILURE` | `INTERNAL_ERROR` | error + `FAILED` |
| `BROWSER_FAILURE` | `BROWSER_CLOSED` / `CREDENTIAL_UNAVAILABLE` / `CLEANUP_FAILED` | error + `FAILED` |
| `CANCELLED` | `CANCELLED_BY_USER` | nada (la cancelación ya es autoritativa en BD) |
| `EXPIRED` | `HUMAN_CHALLENGE_TIMEOUT` | error + `FAILED` |

**Hallazgo de contrato — RESUELTO en E3.1 (ver sección 10).** Hasta E3 el broker solo persistía el estado del job yregistraba el código de `/agent/error`; el resultado del navegador no se proyectaba en la sesión. E3.1 añade esaproyección autoritativa en el backend (la columna "Reporte del agente" de la tabla anterior queda sustituida por lasección 10).

**Interruptor.** `python -m windows_agent run` **no** abre el navegador por defecto: la autenticación exige
`--enable-sol-auth` (opt-in explícito), de modo que ninguna ejecución de E2 puede tocar el portal.

**Pruebas.** `tests/e3_portal/` (portal local determinista, solo pruebas) + escenarios con Chromium real:
éxito, credenciales inválidas, challenge → humano → landing, challenge → timeout, challenge → cancelación,
selector cambiado, landing inesperado/identidad distinta, login en frame, timeout de navegación, cierre del
navegador y crash de página, cancelación antes/después de la entrega del secreto, fallo de entrega sin reintento.
Se ejecutan en Windows con `tests/windows_native_acceptance/harness_e3_windows_runner.py` (Playwright 1.63.0,
Chromium 1243, `PLAYWRIGHT_BROWSERS_PATH` local fuera de git); en Docker se saltan (Playwright no está en la
imagen) y corren las pruebas puras/integración (`test_windows_agent_e3_*`).

**Cachés de Playwright** (distintas de un perfil autenticado): navegadores en
`<repo>\.pw-browsers-win-e2\` (excluido vía `.git/info/exclude`); el perfil temporal de Chromium vive en `%TEMP%`
y Playwright lo elimina al cerrar (verificado: 0 restos).

**Dependencia del Agente Windows (no del backend).** `playwright==1.63.0` (+ Chromium 1243) se instala solo en el
entorno nativo del agente (`.venv-win-e2`), **no** en `backend/requirements.txt` ni en la imagen Docker
(el backend nunca ejecuta un navegador). Instalación reproducible:
`python -m pip install playwright==1.63.0` y, con `PLAYWRIGHT_BROWSERS_PATH=<ruta local>`,
`python -m playwright install chromium`. Spike A no había fijado versión; 1.63.0 es la versión validada en E3.

## 10. Domain E3.1 — Proyección autoritativa del resultado del navegador en la sesión

**Frontera de autoridad.** El Agente nunca elige un estado de BD. Reporta un evento semántico de **lista cerrada**
por el endpoint autenticado YA existente (`POST /agent/jobs/{id}/status`, extendido; sin endpoint nuevo). El
backend (`app/services/sol_browser_result_service.py`), en **una transacción** con los locks de Domain C (sesión
antes que job), valida tenant, agente, `connection_id` vivo, job, relación job/sesión, estado actual y legalidad de
la transición, y solo entonces transiciona reutilizando `start_progress`/`report_waiting_for_user`/
`report_succeeded`/`report_failed`. **Sin migración, sin cambios a la máquina de estados, sin RBAC nuevo**
(`ALEMBIC_HEAD=0018`).

**Contrato extendido (`JobStatusReport`, esquema estricto).** `status ∈ {COMPLETED, WAITING_FOR_USER, FAILED}`,
`reason_code` (obligatorio solo con `FAILED`, lista cerrada de 9 códigos: `AUTH_FAILED`, `PORTAL_CHANGED`,
`AUTH_HOST_CHANGED`, `WRONG_LOGIN_MODE`, `HUMAN_CHALLENGE_TIMEOUT`, `BROWSER_CLOSED`, `CLEANUP_FAILED`,
`CREDENTIAL_UNAVAILABLE`, `INTERNAL_ERROR`) y `session_id` opcional (si se envía debe coincidir con el del job).
Se rechazan campos extra (credenciales, RUC, DOM, HTML, URLs, estados de BD) y cualquier código fuera de la lista;
los códigos que decide el backend (`CANCELLED_BY_USER`, `SESSION_EXPIRED`, `AGENT_REVOKED`, vault, job…) **no** son
reportables por el Agente.

| Resultado del navegador | Reporte del Agente | Efecto autoritativo |
|---|---|---|
| `AUTH_SUCCEEDED` | `COMPLETED` | sesión `SUCCEEDED` (`operation_result=LANDING_REACHED`) **y** job `COMPLETED` en la misma transacción; exige credencial realmente entregada |
| `WAITING_FOR_USER` | `WAITING_FOR_USER` | `IN_PROGRESS → WAITING_FOR_USER`; el job sigue `CLAIMED` |
| `INVALID_CREDENTIALS` | `FAILED`+`AUTH_FAILED` | sesión `FAILED` + job `FAILED` |
| `PORTAL_CHANGED` / login en frame | `FAILED`+`PORTAL_CHANGED` / `AUTH_HOST_CHANGED` | ídem |
| `BROWSER_FAILURE` | `FAILED`+`BROWSER_CLOSED` / `CREDENTIAL_UNAVAILABLE` / `CLEANUP_FAILED` | ídem |
| `TRANSIENT_NAVIGATION_FAILURE` (terminal) | `FAILED`+`INTERNAL_ERROR` | ídem |
| tiempo de challenge agotado (local) | `FAILED`+`HUMAN_CHALLENGE_TIMEOUT` | ídem |
| `CANCELLED` | nada | autoridad existente: `cancel_session` (job `CANCELLED`) |
| `EXPIRED` (TTL de sesión) | nada | autoridad existente: `expire_session` |

**Notas de diseño.**
- `WAITING_FOR_USER → SUCCEEDED` se materializa con dos saltos **ya legales** en una transacción
  (`WAITING_FOR_USER → IN_PROGRESS`: el humano completó la acción; luego `IN_PROGRESS → SUCCEEDED`). No se modificó la
  matriz. (Defensa residual: un resultado sobre una sesión aún `WAITING_FOR_AGENT` la promueve a `IN_PROGRESS`; en el flujo
  normal el claim ya lo hizo, ver E3.1.1.)
- **Job vs sesión.** `WAITING_FOR_USER` es estado de sesión; el job permanece activo. El job solo pasa a `COMPLETED`
  junto con la sesión `SUCCEEDED`; un HTTP 200 nunca basta: sin `credential_delivered_at` el éxito se rechaza.
- **Carreras y reanimación.** Un resultado tardío sobre una sesión terminal (`CANCELLED`/`EXPIRED`/`FAILED`/
  `SUCCEEDED`) se rechaza (409): jamás `CANCELLED/EXPIRED → SUCCEEDED`. El lock de sesión serializa cancelación y
  resultado: gana una sola transición legal.
- **Idempotencia.** El mismo resultado ya aplicado (`WAITING_FOR_USER`, `COMPLETED`, `FAILED` con el mismo código) →
  200 sin mutar; un resultado conflictivo → 409. Job ajeno, de otro tenant o con sesión que no coincide → 404 (sin
  oráculo); `connection_id` obsoleto → 409 (lease de E1/D).
- **Compuerta de credencial.** La proyección no toca ni reabre la entrega de un solo uso: ni `WAITING_FOR_USER`, ni
  el éxito, ni el fallo permiten una segunda entrega.
- **Ubicación durable del motivo terminal:** `sol_access_sessions.terminal_reason_code` (columna existente) + metadata
  de la auditoría `SESSION_FAILED` (el éxito deja la auditoría `AUTH_SUCCEEDED`). No se creó ninguna columna.
- **Cambio de comportamiento visible.** El `COMPLETED` previo a E3.1 solo marcaba el job; ahora exige credencial
  entregada y proyecta la sesión. `test_broker_smoke` se ajustó (éxito sin credencial → 409; el ciclo termina con
  `FAILED`+`INTERNAL_ERROR`).

**Validación.** Backend (broker/PostgreSQL/mTLS reales, solo credenciales ficticias): matriz A–R en
`tests/integration/broker/test_broker_browser_result_projection.py`. Extremo a extremo NATIVO en Windows
(`harness_e31_projection_runner.py`: Agente real con DPAPI + Chromium real contra el portal local + broker en
Docker): login normal → `SUCCEEDED`; challenge → `WAITING_FOR_USER` → `SUCCEEDED`; portal cambiado → `FAILED`
(sin consumir credencial); credenciales inválidas → `FAILED`; cancelación durante la espera → `CANCELLED`.
`SUNAT_LIVE_ACCEPTANCE=NOT_RUN`.

## 11. Domain E3.1.1 — El claim mueve la sesión a `IN_PROGRESS`

El claim exitoso del job es el evento autoritativo: en `sol_agent_job_service.claim_job`, **después** de toda la
validación (token, job `PENDING`, job/sesión no vencidos, sesión no terminal, agente `ACTIVE`) y en la **misma
transacción y con el mismo orden de locks** (sesión antes que job), si la sesión está `WAITING_FOR_AGENT` pasa a
`IN_PROGRESS` con el helper existente `start_progress`. El agente no reporta `IN_PROGRESS` y no hay endpoint nuevo.

- Un claim rechazado (agente equivocado, job inexistente, replay, conexión obsoleta, sesión terminal o vencida por
  reloj) **no** muta la sesión; nunca se resucita una sesión terminal.
- Si la cancelación/expiración gana primero, el claim se rechaza y la sesión queda terminal; si el claim gana, la
  cancelación/expiración normal transiciona desde `IN_PROGRESS` (el lock de sesión serializa ambos caminos).
- La compuerta de entrega de credencial (grant, un solo uso, lease de conexión, autorización de usuario, desbloqueo de
  bóveda) no cambió.
- Flujos resultantes: `WAITING_FOR_AGENT → IN_PROGRESS → SUCCEEDED`; con challenge,
  `… → IN_PROGRESS → WAITING_FOR_USER → (IN_PROGRESS) → SUCCEEDED`.

## 12. E3-LIVE-A1 / A2 — Contrato pre-auth congelado con la evidencia observada del portal real

**LIVE-A1** fue un reconocimiento **de solo lectura y sin autenticar** del portal real (0 credenciales, 0 escritura en
campos, 0 clics en "Iniciar sesión", 0 llamadas al endpoint de credencial; navegador y contexto efímeros). **LIVE-A2**
(esta sección) congela solo lo observado, sin tocar la red: el portal local replica esa estructura y toda la suite se
ejecuta offline (`SUNAT_LIVE_NETWORK_ACCESS=NO` en A2).

| Hecho | Estado | Evidencia sanitizada (LIVE-A1) |
|---|---|---|
| Estructura de la entrada pública | **LIVE CONFIRMED** | 5 enlaces "Ingresar"; el correcto está en el contenedor hermano siguiente del encabezado |
| Texto DOM del encabezado | **LIVE CONFIRMED** | `Mis tramites y consultas` (el mayúsculas es CSS `text-transform`; el literal en mayúsculas del contrato previo era incorrecto) |
| Host/ruta/título del login | **LIVE CONFIRMED** | `api-seguridad.sunat.gob.pe`, `/v1/clientessol/<client-id>/oauth2/loginMenuSol` (client-id dinámico), título "SUNAT - Menú SOL" |
| Login en Page de nivel superior, sin frame | **LIVE CONFIRMED** | 0 frames de autenticación |
| Patrón de dos Pages | **LIVE CONFIRMED** | la Page pública permanece en `www.sunat.gob.pe`; el login abre en una Page **nueva** |
| `#txtRuc` | **LIVE CONFIRMED** | `input` text, placeholder `RUC`, `maxlength=11`, sin `name` |
| `#txtUsuario` | **LIVE CONFIRMED** | `input` text, placeholder `Usuario`, `maxlength=8`, sin `name` |
| `#txtContrasena` | **LIVE CONFIRMED** | `input` password, placeholder `Contraseña`, `maxlength=12`, sin `name` |
| `#btnAceptar` | **LIVE CONFIRMED** | `button type=button`, nombre "Iniciar sesión" (único por rol e id) |
| Modo RUC por defecto | **LIVE CONFIRMED** | `#btnPorRuc` (texto "RUC") con clase `active`; `#txtRuc` visible; `#txtDni` oculto; `#btnPorDni` inactivo; **no existe** un control "Ingresa por RUC" |
| Ausencia de `<label>` | **LIVE CONFIRMED** | los campos se identifican por id/placeholder, no por etiqueta |
| `input#captcha` | **LIVE CONFIRMED (no es challenge)** | `type=hidden`, contenedor no visible, sin iframe ni texto visible ⇒ **no** es un challenge activo |
| Texto/marcador de credenciales inválidas | **LIVE UNVERIFIED** | no se envió ninguna credencial (`INVALID_CREDENTIAL_TEST_LIVE=NOT_RUN`) |
| Marcador visible de un challenge humano real | **LIVE UNVERIFIED** | no se provocó ninguno |
| Identidad del landing bajo el producto E3 | **LIVE CONFIRMED en LIVE-B** (ver sección 13) | LIVE-A1 no llegó a `e-menu`; LIVE-B sí: `https://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm`, título "SUNAT - Menú SOL" |

**Contrato congelado (A2).** `portal_contract.py`: `SOL_PORTAL_CONTRACT` describe solo lo observado.

- *Entrada pública:* encabezado por **rol** con texto DOM exacto (sin distinguir mayúsculas), exactamente 1; enlace
  "Ingresar" **dentro del contenedor hermano siguiente** (`following-sibling::*[1]`), exactamente 1. Nunca por índice
  global, orden global ni texto global (`PUBLIC_ENTRY_GLOBAL_INGRESAR_INDEX_SELECTOR=NO`).
- *Identidad del login:* esquema `https`, host exacto, **ruta por patrón con client-id dinámico**, título, Page de nivel
  superior y ningún frame de autenticación (un login en un frame ⇒ `PORTAL_CHANGED`/`AUTH_HOST_CHANGED`). Estas
  comprobaciones estrictas se exigen **antes de pedir la credencial**; después del envío la URL de la página de
  autenticación puede cambiar (POST/redirección) y no se rechaza por eso.
- *Controles:* selectores primarios por id (`#txtRuc`, `#txtUsuario`, `#txtContrasena`, `#btnAceptar`) **corroborados**
  por tipo/placeholder/maxlength (y nombre del botón). Id ausente, **duplicado** o con atributos distintos ⇒
  `PORTAL_CHANGED`; nunca se cae a un textbox cercano. No se exigen `<label>`.
- *Modo RUC:* se valida (`#btnPorRuc`/`#btnPorDni`/`#txtDni`). Si el modo RUC ya está activo **no se hace clic**
  (`RUC_MODE_CLICK_WHEN_ALREADY_ACTIVE=NO`). Si está inactivo se permite **un** clic sobre el mismo `#btnPorRuc`
  (`RUC_MODE_MAX_ACTIVATION_CLICKS=1`) y se exige después `#btnPorRuc` activo, `#txtRuc` visible y `#txtDni` oculto;
  si no, `PORTAL_CHANGED`.
- *Challenge:* `input#captcha` hidden **no** dispara `WAITING_FOR_USER`
  (`HIDDEN_CAPTCHA_INPUT_MEANS_CHALLENGE=NO`); el ejecutor ni siquiera inspecciona ese elemento. Sin marcador de
  challenge real (`LIVE_UNVERIFIED`), una página de login que persiste tras el envío sigue tratándose
  conservadoramente como `WAITING_FOR_USER` (supervisión humana), sin reenvío.
- *Orden del secreto:* toda la identidad del portal (host/ruta/Page/controles/submit/modo RUC) se valida **antes** de
  pedir la credencial (`CREDENTIAL_FETCH_BEFORE_PORTAL_IDENTITY_VALIDATION=NO`); si algo falla, el endpoint de
  credencial recibe 0 llamadas, y se revalida (sin activar el modo) justo antes de escribir.

**Pruebas.** Puras: `test_windows_agent_e3_live_contract.py` (identidad del login, patrón de dos Pages, matriz
fail-closed de controles/modo, auditoría del código). Con Chromium real contra el portal local sintético (misma
estructura que LIVE-A1, sin copiar HTML real): normal, entrada correcta entre varios "Ingresar", segunda Page, modo RUC
activo sin clic / inactivo con un clic / activación fallida, captcha oculto sin challenge, y 15 variantes negativas
(encabezado ausente/cambiado, enlace ausente, `txtRuc` ausente/duplicado/tipo/maxlength, `txtUsuario` ausente,
contraseña no-password, `btnAceptar` ausente/ambiguo, host/ruta inesperados, login en frame) — todas fail closed antes de
la credencial.

## 13. E3-LIVE-B — Una autenticación SOL real supervisada hasta el landing (evidencia sanitizada)

**Qué se hizo.** UNA autenticación real (`REAL_AUTH_ATTEMPTS=1`; no hay un segundo intento en esta fase) con el usuario
SOL secundario autorizado, por el camino de producto completo: Agente Windows nativo (DPAPI + mTLS) → broker → entrega
E1-B de un solo uso → ejecutor E3 → SUNAT real → proyección E3.1/E3.1.1 a `sol_access_sessions`. Los secretos se
tecleaban con `getpass` y viajaron por un pipe (stdin) al broker desechable; nunca por argv, entorno, archivos ni
conversación. El resultado (`live_b_result.json`) es **sanitizado** (esquema cerrado) y **no** forma parte del
repositorio.

**LIVE CONFIRMED (LIVE-B).**

| Hecho | Valor observado |
|---|---|
| Contrato pre-auth validado antes de la credencial | sí (`preauth_contract_match=true`) |
| Bóveda del harness | KDF **de producción** (`time_cost=2`, `memory_cost_kib=393216`, `parallelism=1`) |
| Entregas de credencial / envíos de login | 1 / 1 (`credential_requests_at_broker=1`, `credential_submissions=1`) |
| Activación del modo RUC | 0 clics (el modo RUC ya estaba activo) |
| Challenge humano | no apareció en este intento (la sesión nunca pasó por `WAITING_FOR_USER`) |
| Resultado | `AUTH_SUCCEEDED`; sesión `SUCCEEDED` (`operation_result=LANDING_REACHED`), job `COMPLETED` |
| Secuencia de estados | `WAITING_FOR_AGENT` → `IN_PROGRESS` (claim) → `SUCCEEDED` |
| Landing (Page de nivel superior) | `https://e-menu.sunat.gob.pe`, ruta `/cl-ti-itmenu/MenuInternet.htm`, título `SUNAT - Menú SOL`, control de contraseña no visible, `matches_landing_contract=true` |
| Patrón de dos Pages | la Page pública (`www.sunat.gob.pe/sol.html`) permaneció; el landing es la Page del flujo de login |
| Clics tras el landing | 0 (`POST_LANDING_AUTOMATED_CLICKS=0`) |
| Buzón / mensajes / descargas / declaraciones / rectificaciones / pagos | no se tocó nada de eso; ninguna automatización los implementa |
| Higiene | 0 secretos en archivos/resultado, 0 procesos Chromium huérfanos, 0 perfiles temporales, 0 artefactos de navegador |

**LIVE STILL UNVERIFIED.**

- Marcador/texto de **credenciales inválidas** natural (no se envió ninguna credencial inválida: `INVALID_CREDENTIAL_TEST_LIVE=NOT_RUN`).
- Marcador de un **challenge humano real** (no apareció ninguno).
- Host real del frame hijo del landing (ver abajo): `LIVE_FRAME_HOST_IDENTITY=UNVERIFIED`.
- Un marcador estructural **positivo** del landing (ver la auditoría): no existe en el contrato ni se registró en LIVE-B.

**Auditoría del contrato del landing.** `matches_landing_contract=true` se produjo porque el producto evaluó, sobre la
Page de nivel superior del landing, la conjunción acotada: (1) esquema `https`; (2) host exactamente
`e-menu.sunat.gob.pe`; (3) ruta con prefijo `/cl-ti-itmenu/` (la observada es `/cl-ti-itmenu/MenuInternet.htm`; el
prefijo también cubre la `originalUrl` `AutenticaMenuInternet.htm`; se **mantiene el prefijo** y no la ruta exacta para no
rechazar un estado transitorio de la redirección posterior al envío, que no se reintenta); (4) título que contiene
`Menú SOL` (observado exacto: `SUNAT - Menú SOL`); (5) **ausencia** del control de contraseña del login (`#txtContrasena`
no visible); y, a nivel de superficie, (6) exactamente una Page de landing, ninguna Page de autenticación simultánea,
ninguna Page fuera de la superficie conocida y ningún frame con el host de autenticación. **No** interviene la telemetría
de red (`LANDING_SUCCESS_NETWORK_ONLY=NO`): `is_accepted_landing(view, contract)` solo recibe la vista de Page/Frame.
Brecha reportada, sin debilitar el estándar: el contrato **no tiene un marcador estructural positivo** de "shell
autenticado" (la arquitectura no define uno y LIVE-B no registró ninguno); la única condición estructural es la
ausencia del formulario de login. Añadir uno exigiría una nueva observación (de solo lectura) en el landing y se decide
en otra ronda; no se inventó ninguna constante de menú/cuenta.

**Marcador `<unexpected-host>` en `frame_hosts` del landing.** Causa (verificada en el código del sanitizador, no en la
página): `_safe_host` escribe ese marcador cuando el hostname de la URL del frame es `None`/vacío (`about:blank`,
`about:srcdoc`, `data:`, `blob:`…) **o** contiene caracteres fuera de `[a-z0-9.-]` (IPv6, `_`, no ASCII). La evidencia
sanitizada no permite distinguir cuál; `LIVE_FRAME_HOST_IDENTITY=UNVERIFIED`. No condiciona el contrato: el clasificador solo
compara los frames con el host de **autenticación** (`api-seguridad`); un frame con ese host habría aparecido como host
válido y habría hecho fallar el clasificador (`PORTAL_CHANGED`), lo que no ocurrió. Un frame hijo, por sí solo, nunca
crea ni impide el éxito (pruebas con `about:blank`/`srcdoc`/`data:`/otro host).

**Pruebas añadidas (offline).** Landing sintético alineado con lo observado (ruta `MenuInternet.htm` precedida por un salto
`AutenticaMenuInternet.htm`, frames hijos sin hostname normal) y negativos: host incorrecto/sufijo malicioso, ruta
incorrecta, título incorrecto, esquema, control de contraseña visible, login confundido con landing, frame con host de
autenticación, frame con la URL del landing en una Page ajena (nunca éxito), y —con Chromium real— landing con ruta
incorrecta, título incorrecto y formulario de login visible tras UN envío ⇒ `PORTAL_CHANGED` sin segundo envío.

**Nota operativa (marca de un solo intento).** `live_b_attempted.flag` se escribe con el Python de la Microsoft Store, que
**virtualiza** `%LOCALAPPDATA%`: el archivo real vive en
`%LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python.3.13_*\LocalCache\Local\LuisIAContable\live_b_attempted.flag`
y solo ese intérprete lo ve en la ruta lógica. La marca está intacta; un intérprete distinto no la vería.

**Regla de seguridad futura (congelada, aceptada con el checkpoint de E3).** El reconocimiento actual del landing no usa un
marcador DOM positivo de "shell autenticado" (solo: `https`, host exacto `e-menu.sunat.gob.pe`, familia de ruta
`/cl-ti-itmenu/`, título `Menú SOL`, ausencia del control de contraseña del login, exactamente una Page de landing, sin Page
de autenticación simultánea y sin frame con el host de autenticación). Esto se **acepta** únicamente para el alcance actual
`AUTHENTICATE_TO_LANDING_ONLY`, porque la automatización se detiene al reconocerlo. **ANTES DE AUTORIZAR CUALQUIER
AUTOMATIZACIÓN DE PRODUCTO POSTERIOR AL LANDING, ES OBLIGATORIO un marcador estructural positivo de shell autenticado,
validado en vivo** (observación de solo lectura, sin inventarlo). `LIVE_FRAME_HOST_IDENTITY=UNVERIFIED` y no forma parte de
la decisión de éxito.

## Domain F — Acceso SOL supervisado (UI / plano de control humano)

Alcance: `AUTHENTICATE_TO_LANDING_ONLY`. Sin automatización SUNAT nueva, sin migración (Alembic head 0018), sin permisos nuevos.

- API (`/api/v1/sol-access`): `GET /agents`, `GET /clients/{id}/session`, `GET /sessions/{id}`, `POST /sessions` (idempotente por `idempotency_key`; 201 al crear, 200 en repetición), `POST /sessions/{id}/cancel`. Permisos existentes: `sol_session:*`, `sol_agent:read`, `sol_credential:read`. Errores `{"detail": {"code": ...}}`; respuestas `no-store`.
- Autorización de entrega JUST-IN-TIME: el agente reclama el job (IN_PROGRESS), abre y reconoce el login y señala `PREAUTH_READY` por el contrato existente `/agent/jobs/{id}/status`. Solo entonces el broker valida (sesión IN_PROGRESS, job CLAIMED, contexto humano en memoria, Vault desbloqueado, usuario activo) y crea el grant E1-B. Si falla, no hay grant ni entrega (`CREDENTIAL_UNAVAILABLE`). El contexto humano (`SessionAuthorizationRegistry`) guarda solo ids, vive en memoria (TTL 3600 s) y se pierde al reiniciar (falla cerrado).
- El broker debe correr en el proceso del backend (`SOL_BROKER_ENABLED=true`); si no, la UI muestra el servicio como no disponible.
- Vencimiento: sin scheduler; se materializa de forma perezosa en las lecturas con la autoridad `expire_session`.
- UI (`/clients/{id}/sol-access`): sondeo solo al backend con backoff, se detiene en estado terminal, restaura la sesión activa al recargar, sin inicio automático, sin acciones posteriores al menú SOL, desafío nunca mostrado ni automatizado, contraseña de Vault solo en estado del componente (se borra tras la solicitud), códigos de motivo mapeados por lista permitida.

### Domain F.1 — Semánticas congeladas y límites documentados

- **Estado de la credencial.** `NEEDS_VALIDATION` **no** significa "inutilizable": significa que el ciclo de validación aún no está productizado en una transición a `ACTIVE`. Es elegible para autenticación supervisada, igual que `ACTIVE`. `INVALID`, `LOCKED_BY_SUNAT`, `PASSWORD_EXPIRED` y `DISABLED` se rechazan al crear la sesión y de nuevo en el momento JIT. La transición `NEEDS_VALIDATION -> ACTIVE` queda **sin definir** (ADR-0068) y NO se implementa en Domain F: ni `AUTH_SUCCEEDED` ni ningún fallo muta el estado de la credencial. No debe afirmarse que un acceso exitoso persistió una validación `ACTIVE`.
- **Contexto de autorización humana.** Es local al proceso (solo ids, TTL 3600 s). Un reinicio del backend lo pierde; no se reconstruye desde la BD, no se recrea ningún grant y no hay entrega de secretos tras la pérdida: el usuario debe iniciar explícitamente un flujo supervisado nuevo. La aceptación de un reinicio real del proceso instalado queda diferida a Domain G.
- **Habilitación del broker.** Domain F requiere despliegue de producto con `SOL_BROKER_ENABLED=true` y CA/runtime del broker correctamente aprovisionados. El compose de desarrollo no lo habilita a propósito; la configuración operativa pertenece a Domain G.
- **401 de dominio.** Una contraseña de bóveda incorrecta (`SOL_VAULT_BAD_PASSWORD`, 401) ya no redirige a `/login`: no es una sesión vencida.
