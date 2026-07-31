# ADR-0066: Arranque local y sincronización explícita fuera del ciclo de FastAPI

## Estado
Aceptado

## Contexto
Luis IA Contable es local-first: una sola PC Windows, sin servidor 24/7, `docker compose` como único
runtime. Hasta este bloque, iniciar el sistema requería una secuencia manual de comandos (`docker compose up
-d`, esperar servicios saludables, `alembic upgrade head`, y disparar manualmente cualquier sincronización de
tareas operativas vía la UI o `python -m app.cli.sync_operational_tasks --tenant-id <uuid>`). El usuario pidió
un único punto de entrada (doble clic en un acceso directo, opcionalmente automático al iniciar sesión de
Windows) que levante todo, se asegure de que la base de datos esté al día, sincronice la información local, y
abra la interfaz — sin bloquear nunca el uso local del sistema por la ausencia de credenciales o conectividad
de integraciones externas (Google, SUNAT SOL, Gmail, WhatsApp).

## Decisión

### La sincronización de arranque es un comando explícito, nunca un hook del ciclo de vida de FastAPI
`backend/app/cli/startup_sync.py` es el único punto de entrada de la sincronización de arranque, invocado
explícitamente por `scripts/start-luis-ia-contable.ps1` (`docker compose exec -T backend python -m
app.cli.startup_sync`). **Prohibido explícitamente** ejecutarla desde: el `lifespan`/evento `startup` de
FastAPI, la importación de un módulo, una migración de Alembic, o la carga de una página `GET`. Motivo: un
hook de `startup` puede ejecutarse varias veces (recarga en caliente en desarrollo), puede duplicarse con
múltiples workers de `uvicorn`, mezcla el ciclo de vida del servidor HTTP con una operación de dominio con
sus propias necesidades de transacción/reintentos/logging, y dificulta razonar sobre cuándo exactamente
corrió. Un CLI explícito, invocado por el orquestador externo, evita las cuatro cosas: corre exactamente una
vez por invocación del orquestador, con su propio control total de transacciones y logging.

### Cero lógica de dominio nueva — reutiliza `task_generation_service.sync_operational_tasks`
`startup_sync.py` no implementa ninguna regla tributaria nueva. Por cada tenant descubierto, llama
exactamente al mismo `task_generation_service.sync_operational_tasks(db, tenant_id, trigger_source="CLI",
triggered_by=None)` que ya usan `POST /tasks/sync` y `app.cli.sync_operational_tasks` (Fase 2/Bloque 1,
Sección 18) — mismo `trigger_source="CLI"` (la fila `task_generation_runs` no distingue "CLI programático de
un tenant" de "CLI de arranque de todos los tenants"; ambos son ejecuciones sin usuario autenticado HTTP). Ese
servicio ya cubre exactamente la sincronización local mínima pedida: generación/actualización de tareas desde
declaraciones/documentos/honorarios, resolución automática de tareas cuya fuente ya es terminal, sin mutar
ninguna declaración/pago/documento fuera de las reglas ya existentes. Las alertas derivadas del dashboard
siguen calculándose en el momento de la consulta (`declaration_risk`/`professional_fee_risk`/
`task_agenda_service`) — este bloque no introduce una segunda tabla de alertas.

### Descubrimiento de tenants y transacción independiente por tenant
`startup_sync.run()` consulta todos los `tenants` (no existe un concepto de tenant inactivo en el modelo
actual — cada fila es una instalación real), ordenados por `id` ascendente (orden determinista, reproducible
entre corridas). Cada tenant se procesa en su **propia** `Session`/transacción (`_sync_tenant`, exactamente el
mismo patrón de `app.cli.sync_operational_tasks`: abrir sesión, `commit()` si todo sale bien, `rollback()` ante
cualquier excepción, cerrar siempre). Un fallo en un tenant nunca aborta ni revierte el trabajo ya comiteado de
otro tenant, y el CLI continúa con los tenants restantes. El código de salida del proceso es distinto de cero
si al menos un tenant falló, para que el orquestador pueda reportarlo con claridad — pero el arranque local
**continúa de todas formas** (la sincronización parcial es preferible a bloquear el acceso al sistema).

### Orden de arranque: PostgreSQL → Alembic → backend/frontend (nunca al revés)
Corrección aplicada tras la revisión del checkpoint inicial de este bloque: la primera versión levantaba los
3 servicios juntos (`docker compose up -d`) y esperaba a que **los tres** (incluido `backend`) estuvieran
`healthy` antes de aplicar `alembic upgrade head`. Eso crea un ciclo evitable en la práctica: si el healthcheck
del backend tarda (compilación, montaje de volumen en desarrollo, arranque de `uvicorn`) por razones que nada
tienen que ver con el estado de la base de datos, el arranque completo queda esperando sin necesidad, ya que
Alembic no depende en absoluto de que el servicio `backend` de larga duración esté arriba. El orden corregido
es:

1. `docker compose up -d db` → esperar solo PostgreSQL `healthy`.
2. `docker compose run --rm --no-deps backend alembic upgrade head` — en un contenedor **efímero**, no en el
   servicio `backend` — inmediatamente después de que PostgreSQL responde, revisando `$LASTEXITCODE`
   explícitamente.
3. `docker compose up -d backend frontend` → esperar a que **ambos** alcancen `healthy`, ahora sí, ya con el
   esquema al día.
4. `startup_sync` (que sí necesita que el backend esté arriba, porque corre `docker compose exec -T backend
   ...` sobre el servicio de larga duración, no un contenedor efímero).

Probado de forma reproducible partiendo de una base de datos deliberadamente atrasada en `0014` (ver
`scripts/tests/Test-StartupOrchestrator.ps1`, caso "base de datos atrasada en 0014 llega a 0015 durante el
arranque"): Alembic corre y termina *antes* de que el script exija `backend` saludable, confirmando que el
ciclo "backend no healthy → no se aplica Alembic → timeout" no puede ocurrir con este orden.

### Clasificación de resultados de `startup_sync` y código de salida
`StartupSyncResult` (4 valores, `backend/app/cli/startup_sync.py`) reemplaza el `bool` binario original
("¿hubo algún fallo?") por una clasificación explícita, con un código de salida estable documentado
(`EXIT_CODES`):

| Resultado | Exit code | Significado | Reacción del orquestador |
|---|---|---|---|
| `SUCCESS` | 0 | Todos los tenants procesados sin fallos (o sin cambios pendientes). | Abre el navegador. |
| `SKIPPED_LOCKED` | 0 | Otra instancia ya sostiene el advisory lock — no es un error. | Abre el navegador, sin sincronizar de nuevo. |
| `PARTIAL_FAILURE` | 2 | Al menos un tenant falló, pero la ejecución continuó con los demás. | Abre el navegador igual (el sistema sigue siendo utilizable) + advertencia clara con la ubicación del log + termina con exit code 2 tras abrir. |
| `FATAL_FAILURE` | 1 | No fue posible conectar a PostgreSQL, adquirir el advisory lock, ni descubrir los tenants de forma segura. | **Nunca** abre el navegador; error claro + ubicación del log; exit code 1. |

El CLI imprime siempre una línea `STARTUP_SYNC_STATUS=<resultado>` (además del resumen humano y una línea
`STARTUP_SYNC_RESULT_JSON=<...>` con conteos y el detalle de tenants fallidos) — el orquestador PowerShell la
lee explícitamente para decidir su comportamiento, en vez de inferir el resultado únicamente del código de
salida (que por diseño es ambiguo entre `SUCCESS` y `SKIPPED_LOCKED`, ambos `0`). `PARTIAL_FAILURE` nunca
oculta qué tenant falló: el resumen JSON incluye `failed_tenants` con el `tenant_id` y el error normalizado de
cada uno.

### Exclusión mutua: advisory lock de sesión de PostgreSQL, no un mutex de Windows ni un lock file
Se evalúaron 3 mecanismos:
- **Advisory lock de PostgreSQL** (elegido): `pg_try_advisory_lock` con una clave de dominio fija (bigint
  derivado una sola vez de `SHA-256("luisiacontable:startup_sync:advisory_lock:v1")`, nunca recalculado).
  Funciona incluso si dos accesos directos se ejecutan casi simultáneamente desde procesos de Windows
  completamente independientes, porque la exclusión vive en el servidor de base de datos, no en el sistema
  operativo del cliente. Se libera automáticamente si el proceso que lo sostiene muere sin liberarlo
  explícitamente (el servidor de PostgreSQL libera los advisory locks de sesión cuando esa conexión se
  cierra), evitando el problema clásico de un lock file huérfano tras un apagado abrupto.
- **Mutex nombrado de Windows**: rechazado — no protege contra dos invocaciones que corren en contenedores o
  sesiones de usuario distintas, y añade una dependencia de la API Win32 que un advisory lock ya cubre mejor
  para este caso (la sincronización real ocurre *dentro* del contenedor `backend`, no en el proceso de
  PowerShell del host).
- **Lock file con PID y detección de proceso huérfano**: rechazado — requiere lógica adicional para detectar
  si el PID registrado sigue vivo (y ese PID podría corresponder a un contenedor, no a un proceso del host,
  complicando la detección), y un apagado abrupto del contenedor puede dejar el archivo sin limpiar.

La sesión que sostiene el lock (`lock_db`) permanece abierta, **sin hacer ningún `commit()` intermedio**, desde
`pg_try_advisory_lock` hasta `pg_advisory_unlock` en un único `finally`. Esto no es un detalle menor: si se
hiciera un `commit()` entre medio, SQLAlchemy devolvería la conexión física al pool de conexiones y la
sentencia de liberación podría ejecutarse sobre una conexión *distinta* a la que adquirió el lock — dejándolo
huérfano hasta que esa conexión se reciclara (bug real encontrado y corregido durante las pruebas de este
bloque). Cuando una segunda instancia no logra adquirir el lock, informa "la sincronización inicial ya está en
curso" y **no lo trata como un error** (código de salida 0, sin sincronizar una segunda vez) — la segunda
instancia del orquestador puede seguir esperando servicios saludables y abrir la aplicación con normalidad.

### Integraciones externas: `SKIPPED_NOT_IMPLEMENTED`/`SKIPPED_NOT_CONFIGURED`, nunca red
Ninguna integración externa (Google Calendar/Drive, SUNAT SOL, Gmail, WhatsApp) está implementada todavía
(Bloques 3.2/3.3/3.4, Fases 4/5) — se reportan como `SKIPPED_NOT_IMPLEMENTED` en el resumen, sin ninguna
llamada de red. La identidad Google (Bloque 3.1, ver ADR-0063/`GOOGLE_LIVE_VALIDATION_DEFERRED`) no tiene
concepto de "sincronización" propio (es una conexión OAuth, no un origen de datos que sincronizar) — el CLI
solo reporta si está configurada o no, informativamente, sin invocar nada. La ausencia total de internet nunca
impide iniciar PostgreSQL/backend/frontend, aplicar migraciones, sincronizar tareas locales, ni abrir la
aplicación — todas las etapas que dependen de red son, por diseño de este bloque, inexistentes.

### El orquestador PowerShell nunca oculta un fallo de comando nativo bajo `$ErrorActionPreference = "Stop"`
`scripts/start-luis-ia-contable.ps1`/`stop-luis-ia-contable.ps1` reutilizan `Invoke-NativeStep`
(`scripts/_backup_lib.ps1`, corregido en el cierre del Bloque 3.1, ver ADR-0063 y la ronda de cierre técnico):
cada invocación de `docker`/`docker compose` evita que PowerShell 5.1 convierta una línea de progreso en
stderr (exit code 0) en un error terminante, mientras que el llamador sigue revisando `$LASTEXITCODE`
explícitamente después de cada paso — un exit code real distinto de cero sigue abortando el script con un
mensaje claro. Las esperas de salud de PostgreSQL/backend/frontend son sondeos con un plazo total finito y
configurable (`-TimeoutSeconds`), nunca una espera indefinida ni una serie de `Start-Sleep` fijos sin verificar
condición real.

### Docker Desktop puede seguir arrancando justo después del inicio de sesión de Windows
Si el arranque automático (Sección de instalación de tarea, abajo) se dispara al iniciar sesión, Docker
Desktop puede no estar listo todavía. `Wait-DockerEngineReady` sondea `docker info` con un plazo total
configurable (`-DockerEngineTimeoutSeconds`, 120s por defecto) e imprime "Esperando Docker Desktop..." una
sola vez mientras reintenta — nunca una espera indefinida, nunca falla en el primer intento fallido.
Adicionalmente, `install-autostart.ps1` aplica un retraso real al propio disparador de la tarea
(`$trigger.Delay`, 30s por defecto, `-LogonDelaySeconds` configurable) para reducir sondeos innecesarios en el
caso común. La tarea usa `MultipleInstancesPolicy = IgnoreNew`: una segunda activación del disparador (p. ej.
un reinicio de sesión rápido) nunca lanza una segunda instancia mientras la primera siga corriendo — capa
adicional sobre el advisory lock de `startup_sync`, que sigue siendo la protección real contra dos
sincronizaciones simultáneas incluso entre instancias lanzadas por medios distintos (tarea programada +
doble clic manual).

## Alternativas consideradas
- **Un endpoint HTTP `POST /admin/startup-sync` disparado por el propio frontend al cargar**: rechazado —
  reintroduce el problema de "puede dispararse varias veces" (cada pestaña abierta, cada recarga), y no
  resuelve el arranque en frío (el frontend no puede estar disponible antes de que el backend/DB lo estén).
- **Ejecutar la sincronización dentro de la migración de Alembic**: rechazado — Alembic gestiona esquema, no
  lógica de dominio; mezclar ambos dificulta el rollback de una migración y viola la separación ya establecida
  en este proyecto entre "cambios de esquema" y "operaciones de servicio".
- **Un daemon/servicio de Windows en segundo plano permanente**: rechazado — contradice el principio
  local-first de este proyecto (Sección 6.1 del plan de Fase 3): ningún proceso 24/7 es requisito de
  corrección, y una tarea de inicio de sesión (opcional) ya cubre "arranque automático" sin necesitar un
  servicio con privilegios elevados.

## Consecuencias
- Cualquier operación de dominio nueva que deba ejecutarse "al iniciar" (p. ej. si un futuro bloque necesita
  refrescar un token OAuth vencido) se añade como un paso explícito dentro de `startup_sync.run()` o como un
  nuevo CLI invocado por el mismo orquestador — nunca como un hook de FastAPI.
- El orquestador (`start-luis-ia-contable.ps1`) y la lógica de dominio (`startup_sync.py`) están
  completamente desacoplados: el primero solo sabe invocar comandos y verificar códigos de salida/salud de
  contenedores; toda regla de negocio vive en el segundo, reutilizando servicios ya probados.
- El acceso directo de escritorio y la tarea de inicio automático de Windows son ambos opcionales y
  completamente equivalentes: los dos invocan el mismo `start-luis-ia-contable.ps1` sin ninguna lógica
  duplicada entre ellos.
