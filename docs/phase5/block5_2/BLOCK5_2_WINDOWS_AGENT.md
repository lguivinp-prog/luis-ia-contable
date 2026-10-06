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
