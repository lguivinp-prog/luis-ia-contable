# Changelog — Luis IA Contable

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/).

## [Unreleased]

### Producto M2 — SUNAT operativo: destinos seguros, Ficha RUC y alta asistida (migración 0020, ver ADR-0075)

- **Agente**: marcador de shell autenticado estructural, destinos `MENU_SOL`/`FICHA_RUC` por allowlist con presupuesto de clics, parser de Ficha RUC por rótulos, congelación de la automatización y estado `HUMAN_CONTROL`.
- **Backend**: sesiones con `destination`/`extract_ficha`, `POST /agent/jobs/{id}/ficha`, snapshots de Ficha con diff y aceptación/rechazo humano, `client-onboardings` (iniciar/completar/cancelar). Sin permisos nuevos.
- **UI**: Centro SUNAT (destinos, Actualizar Ficha RUC), revisión de propuesta y "Alta asistida por SUNAT".
- **Cierre de auditoría**: reconciliación de sesiones `HUMAN_CONTROL` huérfanas (heartbeat con `holding_job_id`, lease de 120 s, readopción tras reinicio del broker, reconexión del agente), variantes candidatas de rótulos de la Ficha y sonda de rótulos solo para desarrollo.
- **Corrección de acceso (login)**: abriendo la app por `127.0.0.1` el servidor de desarrollo de Next bloqueaba los recursos de HMR/hidratación (`allowedDevOrigins` ahora incluye `127.0.0.1`), y el formulario se enviaba de forma nativa por GET, dejando correo y contraseña en la URL (historial y logs) y recargando la página vacía. El formulario de acceso queda deshabilitado hasta hidratarse y todo formulario con campo de contraseña declara `method="post"` (prueba estática de regresión). Los puertos publicados por `docker-compose.yml` quedan en loopback y la base de desarrollo ya no se publica en el host.
- **Aislamiento de credenciales (autocompletado)**: los campos SOL y de Bóveda dejan de ser `<Input>` comunes y pasan a `DeliberateInput`: `name` único sin significado, `autocomplete="new-password"`/`off` más marcas de gestores de terceros, solo lectura hasta enfocar y solo se aceptan eventos de entrada humana (el autocompletado del navegador no llega al estado). Antes, sin `name` ni `autocomplete` útil, Chrome los trataba como un login y rellenaba la credencial guardada del administrador.
- **Avisos intermedios de SUNAT y sesión atascada (aceptación en vivo)**: un aviso/modal que cubría el menú hacía que `locator.click()` agotara los 30 s con un `TimeoutError` que solo se registraba: el navegador se cerraba, la sesión quedaba `IN_PROGRESS` y el agente seguía declarando el job. Ahora `guarded_click` comprueba la accionabilidad SIN efectos (`trial=True`) y, si algo cubre el control, no pulsa nada: informa `WAITING_FOR_USER`, mantiene el navegador abierto, espera al humano (límite acotado) y reintenta el mismo paso con el presupuesto intacto; sin salida falla de forma controlada. Cualquier error inesperado del ejecutor se reporta `FAILED` y el agente suelta el job. Un aviso en otra ventana del mismo host se ignora si exactamente una página es el landing. El backend cierra sesiones `IN_PROGRESS` sin ninguna transición durante 180 s (`JOB_EXPIRED`), huérfanas (`AGENT_CRASHED`) o con agente ausente 120 s, y acepta la Ficha tras `WAITING_FOR_USER`. La pantalla de recuperación permite reintentar (y desbloquear la bóveda) sin volver a escribir la clave SOL.
- **Salto del host de aterrizaje antes del login (segundo fallo en vivo)**: el diagnóstico estructural y el preflight real (`scripts/m2_portal_preflight.py`, sin credenciales) mostraron que el login pasa, en ~50 % de las cargas, por una página de tránsito del host de aterrizaje que el contrato rechazaba al instante como `LANDING_IDENTITY_MISMATCH`. Nuevo estado `PENDING` con espera acotada y sin clics ni credenciales; la identidad del landing y el marcador del shell no se debilitan. Preflight real tras la corrección: 12/12 `LOGIN_FORM_READY=YES`.
- **Login y enlace de recuperación (tercer bloqueo en vivo)**: la cookie de sesión es `SameSite=Strict` (se conserva) y el navegador no la envía en navegaciones iniciadas desde otro sitio (un enlace pulsado desde una app o un chat). La primera petición llegaba sin cookie, `proxy.ts` redirigía a `/login` aunque la sesión siguiera vigente y el login siempre iba a `/clients`, perdiendo el destino. Ahora el proxy y el cliente de la API conservan el destino en `?next=` (solo rutas internas seguras, `lib/authRedirect.ts`), el login vuelve a él tras autenticar y reconoce una sesión ya válida con una petición del propio sitio. Probado en Chromium real (enlace desde otro sitio, sesión revocada, `localhost` frente a `127.0.0.1`, usuario sin permisos, alta inexistente).
- **Preautenticación SOL: tránsito con título del Menú SOL (cuarto bloqueo en vivo)**: una página del host de aterrizaje con ruta y título del Menú SOL pero sin ningún id del shell (evidencia real del preflight, 8/30) se tomaba por «landing antes de autenticar». Ahora solo el marcador DOM positivo decide: con marcador completo antes de la credencial se falla cerrado; sin marcador es un tránsito pendiente (espera acotada, pasiva, sin clics ni credenciales) que debe terminar en el formulario de login completo; si no, falla con `LANDING_TRANSIT_UNRESOLVED`. 39 escenarios nativos (variaciones de latencia y carga, Pages simultáneas, popups, cierre, cancelación, expiración) y preflight real 30/30.
- **Aviso de contacto y Ficha real (cuarto intento en vivo)**: el aviso «Valida tus datos de contacto / Informativo» bloqueaba el primer clic; el agente esperó sin pulsar nada (`WAITING_FOR_USER`) y siguió al resolverlo el humano. La extracción fallaba por la estructura real de la Ficha (filas de 3 celdas con `:` y ninguna fila «Número de RUC»). Ahora: clasificador estructural saneado de avisos con motivo de espera visible, revalidación del shell tras la intervención, extracción solo con contenido cargado y RUC por correspondencia (reemplazado, ver el siguiente ítem).
- **Cierre de M2 (supervisión de SUNAT y alta/actualización por RUC)**: flujos A, B y C comprobados contra SUNAT real. Propuesta de la Ficha aceptada (Dependencia SUNAT) y régimen corregido a RMT de forma manual y auditada. El guardado del formulario del cliente ahora confirma, muestra errores, evita el reenvío y refresca «Información SUNAT»; una actualización duplicada ya no repite la auditoría. Límite conocido: la extracción de tributos afectos, domicilio estructurado, representantes y establecimientos es parcial y está pendiente de validación real (el extractor mejorado solo está probado con fixtures); el régimen oficial por extracción sigue «no confirmado» y la credencial permanece en `NEEDS_VALIDATION` por diseño.
- **Recuperación tras reinicio y cierre de la revisión de la Ficha**: el 404 de «Revisar la propuesta» era una caché de rutas obsoleta del servidor de desarrollo; ahora se descarta en cada arranque y hay chequeo/autorreparación. La propuesta real (un cambio: Dependencia SUNAT) se abre, recarga y navega bien. El extractor lee también bloques plegados presentes en el DOM (sin clics) y registra contadores por sección para distinguir contenido ausente / plegado / no renderizado. Las correcciones manuales del régimen quedan auditadas y rotuladas como manuales. «1 cambio» en singular.
- **Cierre funcional de M2: información oficial completa, régimen oficial y Flujo B**: el cliente creado por alta asistida mostraba «General» por defecto aunque SUNAT lo registra en RENTA - RÉGIMEN MYPE TRIBUTARIO. Ahora el alta usa «Sin determinar», el régimen oficial se deriva del registro de tributos afectos (solo si es inequívoco) y se propone para aceptación humana. La Ficha aporta domicilio fiscal estructurado, tributos afectos con fechas, establecimientos anexos y representantes legales (protegidos por RBAC y minimizados), visibles en «Información SUNAT» sin abrir SUNAT y con su fecha. Flujo B: «Actualizar datos desde Ficha RUC» compara con el último snapshot, ofrece cada cambio por separado y, si no hay cambios, indica «Ficha RUC verificada, sin cambios» sin crear otro snapshot. El Centro SUNAT distingue «Abrir Menú SOL», «Abrir Ficha RUC» y «Actualizar datos desde Ficha RUC». No se asignan obligaciones automáticamente.
- **Auditoría de la autenticación posterior al envío (séptimo intento en vivo)**: la ventana «Bienvenidos a SUNAT» quedó otra vez como una página estable sin controles durante 60 s; sin datos de carga/HTTP/errores no se puede afirmar la causa. Se añade una traza estructural minimizada (host por allowlist, forma de ruta, tipos y clases de estado, tokens `ERR_*`, contadores, estado del documento) y un `stall_reason` deducido solo de ella. Con un proveedor sintético se encontró un defecto propio: tras el envío, las lecturas de DOM sin plazo se bloqueaban hasta ~25 s ante una navegación sin respuesta; ahora el sondeo es acotado y no bloqueante. 59 escenarios nativos M2 (incluye identificadores/código/state/fragmento sintéticos que no deben llegar a la traza: rutas con lista blanca y categorías opacas); el siguiente intento real aportará el diagnóstico que faltaba.
- **Login administrativo: el fallo genérico ocultaba que el servicio estaba caído**: el mensaje «No se pudo iniciar sesión» aparecía con HTTP 500 del proxy porque el backend aislado (:8011) y el agente habían dejado de ejecutarse (sin apagado limpio). El login distingue ahora credenciales incorrectas (401), límite de intentos (429), servicio no disponible (5xx) y fallo de red, con mensajes diferentes que no culpan a la contraseña cuando el fallo es técnico (`lib/loginError.ts` y pruebas).
- **Autenticación: «Bienvenidos a SUNAT» ya no es una espera humana falsa (sexto intento en vivo)**: tras enviar la credencial, una página de autenticación sin formulario ni verificación activaba `WAITING_FOR_USER` ("completa una verificación") sin nada que completar. Ahora la navegación pendiente se observa de forma pasiva con plazo; solo un desafío identificado positivamente pide al humano, y si el tránsito no avanza termina con `AUTH_NAVIGATION_STALLED` (mensaje claro, nueva sesión supervisada). 56 escenarios nativos M2.
- **Extractor del encabezado oficial de la Ficha RUC (quinto intento en vivo)**: la Ficha visible seguía terminando en `FICHA_TAXPAYER_NOT_IDENTIFIED`. La evidencia saneada mostró que el único número de 11 dígitos está en la celda «FICHA RUC: <RUC>» (tabla 0, fila 0, celda 0) y no hay fila «Número de RUC». Ahora el RUC se toma solo de ese encabezado (único, en su posición, sin conflicto), con fixtures de encabezado anidado/con saltos de línea, ausente, duplicado, fuera de posición, número incidental, otro RUC, carga lenta y documento parcial; actividades con y sin prefijo. 50 escenarios nativos M2. Pendiente: confirmar el encabezado en un intento real supervisado.
- **Pendiente**: validación en vivo de rótulos de la Ficha y aceptación real por UI.

### Producto M1 — Operación mensual Multi-RUC: workspace, estados manuales y navegación (migración 0019, ver ADR-0074)

- **Migración 0019** (reversible 0019 ↔ 0018): `client_period_operations` (Buzón, Contabilidad, Comunicación al cliente y notas por tenant/cliente/periodo; creación perezosa; UNIQUE por alcance, FKs compuestas con tenant, CHECK de enums/versión/notas) y `client_period_operation_history` (quién, cuándo, campo, de → a).
- **API**: `GET /api/v1/cockpit/clients/{client_id}/periods/{period_id}` (agregado del workspace), `GET …/history` y `PUT …/operations` (permiso nuevo `cockpit:update`, concurrencia optimista por `row_version`, 409 con `CLIENT_PERIOD_OPERATIONS_VERSION_CONFLICT`). Permisos: admin 57, contador 49, asistente 22.
- **Cockpit**: columnas Buzón/Contabilidad/Comunicación y tarjetas `buzon_pending`, `accounting_pending`, `client_communication_pending`. **Corrección SIRE:** SIRE configurado con el periodo sin generar ahora es `NOT_STARTED` (antes `null`).
- **UI**: workspace `/cockpit/[clientId]?period=…&tab=…` con 10 pestañas que reutilizan paneles existentes (se extrajeron `DeclarationSections`/`useDeclarationWork` y `ClientFeesPanel`); menú agrupado y responsivo (cajón en móvil); `/communications/new?client_id=` precarga el cliente.
- Sin acceso a SUNAT, CONTASIS ni DARWIN; la única acción SUNAT expuesta sigue siendo "Acceso SOL supervisado".

### Producto M1, Domain B — Cockpit Multi-RUC mensual (solo lectura, ver docs/COCKPIT_MULTI_RUC.md)

- `GET /api/v1/cockpit/monthly`: una fila por cliente activo y periodo (incluye clientes sin declaraciones generadas), con SIRE derivado (`NOT_CONFIGURED` distinto de `NOT_APPLICABLE`), fase mensual, riesgo global determinista, agregados de impuesto/honorarios y tarjetas resumen; paginación por cliente. Sin migración ni permisos nuevos.
- UI `/cockpit` y entrada "Cockpit mensual" en el menú; el Tablero mensual existente no cambia. `main` del layout recibe `min-width: 0` para que las tablas anchas se desplacen dentro de su contenedor.

### Fase 5, Bloque 5.2, Domain G — Empaquetado y operación del Agente SOL (ver ADR-0073)

- Agente Windows empaquetado con PyInstaller one-folder (`packaging/windows-agent/`), aplicación por usuario con autoarranque por Task Scheduler (NO servicio de Windows), Chromium propio de la app, manifiesto SHA-256, instalación/upgrade/desinstalación/purga con identidad separada de los binarios.
- Perfil de despliegue local nativo (`deploy/windows-local/`) con broker habilitado, CA aprovisionada explícitamente (`app.broker.provision`), diagnóstico seguro (`app.broker.diagnostics`) y herramienta de operador de pairing (`app.broker.pair_cli`).
- Corrección: la API de F reporta un Agente como disponible solo con conexión registrada Y latido reciente (un Agente matado sin desconexión limpia seguía "disponible").

### Fase 5, Bloque 5.1 — Bóveda de credenciales SOL (migración 0017, ver ADR-0068 a ADR-0071)

> Precedido por un spike experimental cerrado en la rama `spike/phase5-sol-vault-crypto` (commit `2096a9b`,
> tag `spike-phase5-sol-vault-crypto-v1`) que validó la arquitectura antes de escribir código productivo; el
> spike es evidencia técnica histórica, ningún módulo productivo depende de él en tiempo de ejecución.

- **Jerarquía criptográfica de tres niveles**: contraseña personal → (Argon2id) → KEK del slot →
  (AES-256-GCM) → `vault_root_key` (envuelta, una copia por slot) → (AES-256-GCM) → `credential_dek`
  (envuelta, versionada) → (AES-256-GCM, por campo) → `sol_username`/`sol_password`. AES-256-GCM con sobre
  versionado y AAD determinista obligatorio en cada capa (`app/core/sol_vault_crypto.py`), mismo patrón que
  el ya productivo `app/core/integration_crypto.py`. Argon2id vía `hash_secret_raw` con parámetros fijos
  (`memory_cost_kib=393216`, `time_cost=2`, `parallelism=1`, `hash_len=32`, `kdf_version=1`), persistidos por
  slot, nunca reducidos automáticamente por presión de memoria (ADR-0068).
- **`Argon2ConcurrencyGate`**: como máximo una derivación Argon2id simultánea por proceso backend, espera
  acotada, liberación garantizada. **Single-worker backend obligatorio en v1**: `enforce_single_worker_gate`
  hace fallar el arranque completo del proceso ante `BACKEND_WORKERS != 1`, verificado a nivel de módulo antes
  de construir la aplicación (ADR-0068).
- **`VaultUnlockRegistry`**: registro en memoria de un único proceso, nunca persistido, indexado por
  `(tenant_id, vault_id, user_id, app_session_id)` con verificación en vivo contra la sesión real en cada uso.
  `LOCKED`/`NOT_INITIALIZED` son estados derivados, nunca columnas. Reiniciar el backend bloquea
  automáticamente todas las bóvedas de todos los usuarios (ADR-0071).
- **Recuperación en dos pasos**: código de recuperación mostrado una única vez, confirmado criptográficamente
  reingresándolo; reemplazo con coexistencia hasta la confirmación; `recovery_confirmed_at` vive solo en el
  slot `RECOVERY`, nunca en `sol_vaults` (fe de erratas del spike, corregida). Invitaciones de dos actores con
  asociación efímera invitador→invitado solo en memoria, nunca persistida (ADR-0069).
- **Rotación ordinaria de `credential_dek`**: procedimiento atómico de 14 pasos con advisory lock
  (`pg_advisory_xact_lock`), verificado con dos conexiones PostgreSQL reales forzando una excepción a mitad de
  la rotación (rollback atómico completo). `vault_root_key` nunca rota en esta versión — el caso de compromiso
  queda como procedimiento manual diferido, deliberadamente no implementado como endpoint (ADR-0070).
- **Credenciales SOL por cliente**: `credential_kind` real (`SECONDARY`/`PRIMARY_OWN_RUC`/
  `PRIMARY_EXCEPTIONAL`, nunca un valor `PRIMARY` genérico); toda alta/actualización aterriza en
  `NEEDS_VALIDATION`; `PRIMARY_EXCEPTIONAL` exige `authorization_basis` explícita.
- **5 tablas nuevas** (migración `0017`): `sol_vaults`, `sol_vault_key_slots`, `sol_vault_data_keys`,
  `sol_vault_invitations`, `client_sol_credentials` — verificadas upgrade/downgrade/re-upgrade y bootstrap
  completo `0001→0017` contra una base de datos genuinamente temporal.
- **Permisos**: 9 permisos nuevos (`Permission` pasa de 42 a 51 valores: `admin`=51, `contador`=44,
  `asistente`=22, sin ningún permiso nuevo para el asistente); 4 admin-only (`sol_vault:initialize`/
  `manage_users`/`recover`/`rotate`), 5 admin+contador (`sol_vault:unlock`, `sol_credential:read`/`create`/
  `update`/`disable`).
- **Interceptor de errores 422**: redacta el valor de cualquier campo sensible que Pydantic reflejaría en el
  cuerpo de una respuesta 422, incluso cuando el campo que dispara el error es uno distinto e inesperado
  (`extra=forbid`) dentro de la misma petición (`app/core/error_handlers.py`).
- **Frontend**: `/settings/sol-vault` (ciclo completo) y `/clients/[id]/sol-credential` (alta/actualización/
  deshabilitación por cliente), gateados por permisos reales, sin ningún botón de ver/copiar contraseña.
- ADR-0068 a ADR-0071.
- Backend: 877 pruebas, cobertura 88%, incluidas 2 conexiones PostgreSQL reales para la concurrencia de
  rotación y un barrido estático de ausencia de secretos en logs/salida/respuestas. Frontend: 126 pruebas
  Vitest (lógica pura + barrido estático de ausencia de secretos en el código fuente real de las pantallas).

### Bloque transversal — Arranque y sincronización local (ver ADR-0066)

> Cerrado tras la ronda de correcciones operativas del checkpoint — etiqueta `v0.7.1-alpha.1`.

- **Un único punto de entrada de arranque**: `scripts/start-luis-ia-contable.ps1` (acceso directo de
  escritorio o tarea de inicio de sesión de Windows). **Orden robusto y corregido**: PostgreSQL primero
  (`docker compose up -d db` + healthcheck) → `alembic upgrade head` en un contenedor efímero (nunca depende
  de que el servicio `backend` de larga duración esté saludable) → `docker compose up -d backend frontend` +
  healthchecks → sincronización local → navegador. Probado de forma reproducible partiendo de una base de
  datos atrasada en `0014`.
- **`backend/app/cli/startup_sync.py`**: descubre todos los tenants, procesa cada uno en su propia
  transacción independiente (orden determinista por UUID), reutiliza `task_generation_service.
  sync_operational_tasks` (cero lógica de dominio nueva), exclusión mutua vía `pg_try_advisory_lock` de
  PostgreSQL, integraciones externas no implementadas se reportan como `SKIPPED_NOT_IMPLEMENTED` sin ninguna
  llamada de red y sin afectar el arranque local.
- **Resultado clasificado en 4 valores** (`StartupSyncResult`, exit code documentado): `SUCCESS`/
  `SKIPPED_LOCKED` (0, abre el navegador), `PARTIAL_FAILURE` (2, abre el navegador igual con advertencia clara
  y sin ocultar qué tenant falló), `FATAL_FAILURE` (1, nunca abre el navegador).
- **Nunca invocado desde el ciclo de vida de FastAPI** (lifespan/startup/import/migración/GET) — ver
  ADR-0066.
- **Tolerancia a Docker Desktop arrancando**: sondeo de `docker info` con timeout configurable
  (`-DockerEngineTimeoutSeconds`) y mensaje "Esperando Docker Desktop..."; la tarea de inicio automático
  aplica además un retraso real al disparador (`-LogonDelaySeconds`, 30s por defecto) y
  `MultipleInstancesPolicy=IgnoreNew`.
- `scripts/stop-luis-ia-contable.ps1` (conserva volúmenes/datos), `scripts/create-desktop-shortcut.ps1`,
  `scripts/install-autostart.ps1`/`uninstall-autostart.ps1` (tarea del Programador de tareas de Windows para
  el usuario actual, sin contraseña ni privilegios elevados, idempotente).
- Logs locales redactados con retención simple: `logs/startup/startup-<fecha>.log`.
- ADR-0066.

### Fase 3, Bloque 3.2 — Comunicaciones asistidas por WhatsApp (ver ADR-0067)

> Adelantado en la secuencia de ejecución de la Fase 3 frente a Calendar/Drive (diferidos como Bloques 3.3/3.4)
> porque no depende de Google — ver reordenamiento en `docs/ROADMAP.md`. Cambios sin commitear, pendientes de
> revisión.

- **Borradores de comunicación** (`communication_drafts`, migración `0016`): ciclo de vida
  `DRAFT`→`OPEN_REQUESTED`→`MARKED_SENT` (o `DISCARDED` desde `DRAFT`/`OPEN_REQUESTED`), concurrencia
  optimista (`expected_version` + `SELECT...FOR UPDATE`), snapshot congelado de teléfono/texto al crear el
  borrador. Sin WhatsApp Business API, sin WhatsApp Cloud API, sin automatización ni scraping de WhatsApp Web,
  sin envío automatizado de ningún tipo.
- **Sin estado `OPENED_EXTERNALLY`**: el sistema no puede verificar si WhatsApp realmente abrió, se entregó o
  se leyó. Terminología honesta obligatoria en toda la UI: "Se solicitó abrir WhatsApp", "Marcado como enviado
  por ti", "Pendiente de confirmación manual" — nunca "WhatsApp abierto", "Mensaje enviado" sin calificador,
  "Entregado", "Recibido", "Leído" ni "Confirmado por WhatsApp", verificado por una prueba automatizada
  (`communicationTerminology.test.ts`) que escanea el código fuente real de las pantallas.
- **Normalización conservadora de teléfono** (`whatsapp_phone_normalization.py`): reglas de conveniencia solo
  para Perú (9 dígitos con "9" inicial, u 11 dígitos con prefijo "51"); cualquier otro caso ambiguo exige
  formato E.164 explícito (`WHATSAPP_NUMBER_REQUIRED`/`INVALID_WHATSAPP_NUMBER`).
- **Catálogo de 5 plantillas versionado en código** (`app/domain/communication_templates.py`), no en una tabla
  editable: `GENERAL_MESSAGE`, `OBLIGATION_DUE_REMINDER`, `DOCUMENT_REQUEST`, `PAYMENT_PENDING_REMINDER`,
  `TASK_FOLLOW_UP`. Allowlist explícita de 7 variables seguras; nunca inserta contraseñas, credenciales SOL,
  tokens, datos bancarios ni el contenido íntegro de un documento.
- **Validación de fuentes**: `related_task_id`/`related_document_file_id` opcionales, con verificación de que
  pertenecen al mismo cliente y tenant (`SOURCE_CLIENT_MISMATCH`/`SOURCE_NOT_FOUND`), y de que el asistente
  solo referencia tareas propias (`SOURCE_NOT_ACCESSIBLE`). Nueva UNIQUE auxiliar no destructiva sobre
  `declaration_document_files` (`uq_declaration_document_files_id_tenant_client`) para poder usar una FK
  compuesta de solo 3 columnas — reflejada tanto en la migración como en el modelo ORM.
- **Enlace `wa.me` nunca persistido**: se calcula únicamente dentro de `POST .../mark-open-requested`, con
  codificación `urllib.parse.quote` verificada contra espacios/tildes/"ñ"/saltos de línea/"?"/"&"/Unicode;
  `CommunicationDraftOut` omite `recipient_phone_e164` (solo expone `recipient_phone_display`).
- **Permiso nuevo** `communication:draft` (3 roles): `admin=42`, `contador=39`, `asistente=22`. Ownership por
  fila del asistente (solo sus propios borradores) — segunda excepción documentada del repositorio al patrón
  "permiso de rol, nunca de fila" (la primera es `operational_tasks`, ver ADR-0061).
- **8 endpoints** bajo `/api/v1/communications`: `GET /templates`, `POST /templates/{code}/preview`,
  `POST /drafts`, `GET /drafts`, `GET /drafts/{id}`, `POST /drafts/{id}/mark-open-requested`,
  `POST /drafts/{id}/mark-sent`, `POST /drafts/{id}/discard`. Sin endpoint automático de envío.
- **Frontend**: `/communications` (bandeja con filtro por estado), `/communications/new` (borrador con
  selección opcional de plantilla + previsualización), `/communications/[id]` (detalle con "Abrir en
  WhatsApp"/"Marcar como enviado"/"Descartar"). `window.open(wa_url, "_blank", "noopener,noreferrer")` con
  fallback de enlace manual si el navegador bloquea la ventana emergente; `wa_url` nunca se guarda en
  `localStorage`/`sessionStorage`.
- **Auditoría redactada**: `COMMUNICATION_DRAFT_CREATE`/`COMMUNICATION_OPEN_REQUEST`/
  `COMMUNICATION_MARK_SENT`/`COMMUNICATION_DISCARD` nunca registran el cuerpo completo, la URL `wa.me` ni el
  teléfono completo (máximo un sufijo de 4 dígitos).
- **Pruebas**: 2 archivos de constraints/migración, 2 unitarios (normalización/plantillas), matriz de permisos
  actualizada, endpoints completos (fuentes/estados/wa.me/auditoría), concurrencia real con dos conexiones
  PostgreSQL (`mark-open-requested`/`mark-sent`/`discard`), y 4 archivos frontend incluida la prueba
  anti-terminología prohibida.
- ADR-0067.

### Fase 3, Bloque 3.1 — Fundamento de integraciones y conexión OAuth Google (identidad)

> Cerrado como infraestructura Google **preparada pero desactivada por configuración**
> (`GOOGLE_LIVE_VALIDATION_DEFERRED`, ver ADR-0063) — etiqueta `v0.7.0-alpha.1`.

- **Conexión OAuth Google de solo identidad** (`openid`+`email`): migración `0015` con `google_oauth_connections`
  (ciclo de vida `ACTIVE`/`NEEDS_REAUTH`/`DISCONNECTED`, reemplazo de cuenta tras desconexión) y
  `google_oauth_authorization_attempts` (máquina de estados `PENDING`/`PROCESSING`/`SUCCEEDED`/`FAILED` con
  PKCE persistido y purgado al consumir el `state`).
- **Cifrado de credenciales de integración**: sobre AES-256-GCM versionado con AAD por fila/tabla/tenant/
  usuario/tipo de token, y fingerprint HMAC-SHA256 de la clave activa para backup/restore (ver ADR-0064).
- **Adaptadores** `OAuthProvider`/`FakeOAuthProvider`/`GoogleOAuthProvider` (vía `httpx` directo, sin SDK
  oficial de Google). El adaptador real nunca se selecciona sin `GOOGLE_OAUTH_CLIENT_ID`/
  `GOOGLE_OAUTH_CLIENT_SECRET` presentes, aunque `GOOGLE_OAUTH_PROVIDER` quedara mal configurado.
- **Permisos nuevos** `integration:connect`/`integration:disconnect` (3 roles): `admin=41`, `contador=38`,
  `asistente=21`.
- **4 endpoints** bajo `/api/v1/integrations/google`: `status` (incluye `configured: bool`), `authorize`
  (capacidad `IDENTITY` únicamente en este bloque), `callback`, `disconnect`.
- **Frontend**: `/settings/integrations` y `/settings/integrations/google` — muestra "Google no configurado
  en este entorno" y oculta el botón de conexión cuando `configured=false`, sin ofrecer nunca un botón que
  termine en error.
- **Backup/restore**: manifiesto extendido con `integration_token_key_id`/`integration_token_key_fingerprint`;
  reconciliación automática a `NEEDS_REAUTH` cuando la clave de cifrado no coincide tras un restore. Corregido
  además un problema de manejo de stderr/exit code de PowerShell 5.1 en `backup.ps1`/`restore.ps1`/
  `_backup_lib.ps1` (`Invoke-NativeStep`), pre-existente desde antes de este bloque.
- ADR-0062 a ADR-0065.
- **Validación pendiente (`GOOGLE_LIVE_VALIDATION_DEFERRED`)**: el spike de aceptación real de PKCE por Google
  y el smoke test end-to-end contra una cuenta Google real quedan diferidos — el usuario no dispone todavía de
  un proyecto Google Cloud ni de un cliente OAuth real. Obligatorio antes de habilitar los Bloques 3.2/3.3
  (Calendar/Drive) o de usar Google con usuarios reales. La ausencia de estas credenciales no afecta ninguna
  función local del sistema.

### Fase 1, Bloque 3.1 — Ciclo operativo (periodos y declaraciones)

> Documentado retroactivamente: implementado y verificado en el commit `565a3f7` (etiqueta `v0.3.0-alpha.1`), no se había registrado en este archivo hasta ahora.

- **Periodos tributarios canónicos** (`tax_periods`): mensual/anual/especial, apertura/cierre.
- **Declaraciones** (`declarations`) con snapshot versionado del cronograma resuelto y 5 tablas de historial (estado, vencimiento, asignación, presentación, reapertura), máquina de estados de flujo/presentación con regresiones auditadas, bloqueo optimista (`row_version`).
- **Generación masiva idempotente** con detección de vista previa obsoleta (`STALE` vía checksum) y clasificación por ítem, cada uno protegido por su propio `SAVEPOINT`.
- **Tablero mensual básico** (workflow, presentación, riesgo calculado en vivo vs. fecha de vencimiento).
- Cierre de declaraciones deliberadamente restringido en esta subentrega: `tax_assessments`/`declaration_document_items` todavía no existían.

### Fase 1, Bloque 3.2 — Control documental, impuestos, honorarios, cierre completo, tablero extendido, reportes

- **Control documental**: catálogo híbrido sistema/tenant, asignación por cliente con vigencia real (general o vinculada a una obligación concreta), sincronización idempotente de ítems por declaración (individual y masiva por periodo, sin backfill destructivo), integridad cruzada cliente/declaración/requisito vía FKs compuestas (ADR-0033 a ADR-0036).
- **Determinación y pagos tributarios**: determinación versionada con correcciones e historial condicional, saldo siempre contra la determinación vigente, idempotencia de pagos (`idempotency_key`), concurrencia real con `SELECT ... FOR UPDATE`, sobrepago con confirmación explícita y motivo, anulación con motivo (ADR-0038 a ADR-0043).
- **Honorarios profesionales**: acuerdos con vigencia real (`supersede`/`cancel` cierran la vigencia de verdad), cargos con estado persistido separado del riesgo de cobranza calculado en vivo, recibos con regla de importe agregado — dominio completamente independiente del tributario (ADR-0044 a ADR-0046).
- **Cierre operativo completo**: cierre normal (tres precondiciones) y cierre excepcional con pendientes, gateado por un interruptor global + rol de administrador + motivo explícito (ADR-0047).
- **Tablero y riesgo extendidos**: 3 valores nuevos de riesgo (`CLOSED_WITH_PENDING`/`PENDING_TAX`/`PENDING_DOCUMENTS`), agregados de impuestos y honorarios sin duplicar filas del tablero (ADR-0048).
- **Reportes**: exportación CSV/Excel de 10 tipos de datos, sanitizada contra *CSV formula injection*, límite de filas configurable, `openpyxl` declarado en `requirements.txt` (ADR-0049).
- **Migraciones** `0008_document_control`, `0009_tax_assessments_payments`, `0010_professional_fees` — cadena lineal desde `0007`, verificadas upgrade/downgrade/re-upgrade con datos preexistentes de 3.1.
- **Frontend**: pestañas de declaración (Documentos, Impuestos, Cierre), honorarios y requisitos documentales por cliente, tablero con nuevas columnas/filtros, página de reportes.
- ADRs 0016–0032.
- Backend: cobertura de pruebas mantenida ≥ 84 % (311+ pruebas), incluida una prueba de concurrencia con dos conexiones reales y una prueba de rollback atómico forzado.

### Fase 1, Bloque 4 — RBAC real y gestión de usuarios

- **Catálogo de permisos**: `Permission` (30 valores) + `ROLE_PERMISSIONS` declarativo (`admin`=30,
  `contador`=27, `asistente`=13), fallo cerrado ante rol/permiso desconocido; separación
  `document:item_write` (operar ítems, incluido asistente) vs. `document:manage` (configuración documental,
  solo admin/contador) (ADR-0050).
- **Gestión de usuarios**: alta con contraseña temporal obligatoria, cambio de rol, desactivación/reactivación
  con motivo y revocación de sesiones, restablecimiento administrativo de contraseña — 6 endpoints
  (`/api/v1/users/*`), protección concurrente real del último admin activo por tenant vía
  `SELECT ... FOR UPDATE` sin deadlock, migración `0012_users_role_management` con preflight de 3
  verificaciones antes de cualquier DDL (ADR-0051).
- **Aplicación de permisos**: `require_permission` sustituye a `ensure_admin_role` (eliminada) en los 15
  routers existentes; cobertura estructural verificada por prueba dedicada (allowlist cerrada, nunca por
  prefijo) y matriz funcional 200/403 de los 30 permisos (ADR-0052).
- **Frontend**: `CurrentUserProvider`/`useCurrentUser` como fuente única de permisos (`GET /auth/me`),
  pantalla `/users` de administración, pantalla obligatoria de cambio de contraseña, gateo completo de los 16
  componentes/páginas mutantes existentes (ocultar o deshabilitar, nunca solo por color).
- ADRs 0050–0052.
- Backend: 397 pruebas (87 % cobertura), incluidas 2 pruebas de concurrencia real del último admin y una
  matriz 200/403 de 30 casos.

### Fase 1, Bloque 5 — Almacenamiento real y seguro de documentos

- **Modelo y almacenamiento**: `declaration_document_files` (migración `0013`) con cadena lógica de versiones
  (`logical_file_id`/`version`), índice único parcial que impone una sola versión `ACTIVE` por cadena,
  `FileStorageBackend`/`LocalFileStorageBackend` desacoplado sobre un volumen Docker nombrado fuera de `/app`
  (ADR-0053).
- **Ciclo de vida sin restauración**: `ACTIVE`/`SUPERSEDED`/`DELETED`, transición directa `ACTIVE→DELETED`,
  `file_metadata` legado conservado sin cambios de esquema como notas descriptivas, nunca evidencia física
  (ADR-0054).
- **Consistencia BD-disco y concurrencia real**: orden estricto escritura-validación-rename-inserción,
  compensación física ante fallo de `flush()`/`commit()`, bloqueo del `file_id` exacto verificado con dos
  conexiones reales (incluido un hallazgo real: `populate_existing=True` necesario para que el bloqueo no lea
  un objeto cacheado obsoleto), reporte de integridad con 5 categorías exactas (`missing_blob`/`orphan_blob`/
  `hash_mismatch`/`retained_deleted_blob`/`temporary_part`) sin exponer nunca `storage_key` (ADR-0055).
- **Seguridad de contenido y descarga**: validación por contenido real incluida verificación estructural OOXML
  contra ZIP-bombs y macros, nombre original saneado, `Content-Disposition` seguro (RFC 6266), nuevo permiso
  `document:file_manage` (ADR-0056).
- **Nuevo permiso**: `Permission` pasa de 30 a 31 valores (`admin`=31, `contador`=28, `asistente`=13 sin
  cambio).
- **Backup/restauración conjunta**: `backup.ps1`/`restore.ps1` extendidos — backend detenido durante la
  captura, verificación del 100% de los blobs (nunca una muestra), manifiesto con SHA-256 de dump y volumen,
  snapshot de seguridad automático antes de restaurar con reversión automática si la verificación
  post-restauración falla; CLI declarativo `app/cli/document_storage_archive.py` (`tarfile`+`hashlib`, sin
  dependencia del binario `tar` del sistema).
- **Frontend**: sub-sección de archivos por cadena en cada ítem documental (`DocumentItemFiles.tsx`), subir/
  descargar/reemplazar/eliminar gateados por permiso.
- **Migración** `0013_document_file_storage` — cadena lineal desde `0012`, verificada upgrade/downgrade/
  re-upgrade.
- ADRs 0053–0056.
- Backend: 472 pruebas (88 % cobertura), incluidas pruebas de concurrencia real (reemplazo simultáneo,
  reemplazo vs. eliminación), 4 pruebas SQL directas de integridad de cadena, y verificación manual de
  extremo a extremo de backup/restauración con corrección de un error real encontrado en el proceso (`extract`
  intentaba eliminar el punto de montaje del volumen en sí, no solo su contenido).

> **Cierre de Fase 1**: commit `5cd1fe5222c68e3eb74e46062b815dadc2bc302c` en `master`, etiqueta
> `v0.5.0-alpha.1`. A partir de aquí, `docs/ROADMAP.md` renumera las fases posteriores (ver Fase 2 abajo).

### Fase 2, Bloque 1 — Gestión operativa de tareas, responsables, agenda interna y alertas

- **Modelo híbrido**: `operational_tasks` (migración `0014`) materializadas con ciclo de vida propio, más
  `task_change_history`, `task_comments`, `task_generation_runs`. 11 FK compuestas + 7 `CHECK` en
  `operational_tasks` (5 formas estructurales en vez de `source_type`/`source_id`), cadena de 2 FK verificada
  `charge → agreement → client` para honorarios, 4 constraints auxiliares no destructivas en tablas
  preexistentes, 2 `UNIQUE(id, tenant_id)` internas (ADR-0057).
- **Ciclo de vida y concurrencia**: 4 estados sin reapertura, bloqueo de fila exacta (`populate_existing`) +
  versión optimista, orden global determinista de 12 fases (inserción por `generation_key ASC`, bloqueo de
  existentes por `id ASC`) para evitar interbloqueos entre sincronizaciones concurrentes, rechazo de
  mutaciones sin cambio real (`409 TASK_NO_CHANGES`) (ADR-0058).
- **Generación automática idempotente**: 3 reglas (`DECLARATION_FILING`, `DOCUMENT_COLLECTION` — incluye
  `OBSERVED`, `FEE_COLLECTION`) vía `ON CONFLICT DO NOTHING RETURNING`, resolución automática con precedencia
  explícita, realineación de vencimiento, CLI multitenant (`app/cli/sync_operational_tasks.py`,
  `--tenant-id` obligatorio, transacción propia con commit único/rollback ante cualquier excepción) que
  invoca el mismo servicio que `POST /tasks/sync` (ADR-0059).
- **Agenda y alertas**: `app/core/timezone.py` centraliza `America/Lima`; semántica única de "vencido" sin
  variante local; filtros canónicos `time_bucket`/`assigned_to` (`me`/`unassigned`/UUID) en `GET /tasks`;
  `GET /tasks/agenda-summary` y `GET /tasks/alerts-summary` derivados sin tabla propia y sin doble conteo de
  honorarios (ADR-0060).
- **Permisos**: 8 permisos nuevos (`Permission` pasa de 31 a 39 valores: `admin`=39, `contador`=36,
  `asistente`=19), `task:sync` concedido a los 3 roles; autorización por fila del asistente — única
  excepción del repositorio al patrón "permiso de rol, nunca de fila" — incluida la carrera
  comentario-vs-reasignación resuelta con el mismo bloqueo de fila (ADR-0061).
- **Tareas manuales con cliente opcional**: `POST /tasks` acepta `client_id` opcional e inmutable (nunca en
  `PATCH`), validado contra el mismo tenant; `/tasks/new` incluye selector "Sin cliente"/clientes del tenant.
- **Frontend**: `/tasks` (lista + filtros canónicos + sincronización manual), `/tasks/[id]` (contenido,
  historial, comentarios, acciones con botones deshabilitados —no solo ocultos— para el asistente sobre
  tareas ajenas), `/tasks/new`, `/agenda`, extensión aditiva de `/dashboard` con `task_summary`.
- **Reportes**: 11º tipo (`TASKS`), cliente siempre resuelto de forma denormalizada, incluida la cadena de
  honorarios.
- **Migración** `0014_operational_tasks` — cadena lineal desde `0013`, verificada upgrade/downgrade/
  re-upgrade.
- ADRs 0057–0061.
- Backend: suite completa verificada tras cada dominio (551+ pruebas), incluidas pruebas de concurrencia
  real con dos conexiones (completar/cancelar/reasignar/reprogramar concurrentes, carrera
  comentario-vs-reasignación en ambos órdenes, inserción concurrente de `generation_key` sin interbloqueo) y
  pruebas con reloj congelado para las fronteras exactas de los baldes de agenda.

## [0.2.0] - 2026-07-22 - Fase 1 bloque 2

### Añadido
- **Endurecimiento de autenticación y sesiones**: bloqueo temporal por intentos fallidos con backoff progresivo y desbloqueo administrativo local (ADR-0009), mitigación de enumeración por tiempo mediante hash ficticio precalentado, protección CSRF double-submit cookie (ADR-0010), expiración de sesión por inactividad y absoluta, rotación de sesión en login, límite de sesiones concurrentes, vista de sesiones activas y cierre de otras sesiones, revocación al cambiar contraseña, UI de advertencia de inactividad con cuenta regresiva.
- **Motor de cronogramas tributarios versionado**: fuentes, versiones con máquina de estados (`DRAFT`→`IMPORTED`→`PENDING_REVIEW`→`APPROVED`→`SUPERSEDED`, más `REJECTED`/`CANCELLED`), entradas y excepciones con últimos dígitos normalizados (ADR-0014), historial de aprobación, importación de CSV con validación/diff/vista previa/confirmación (ADR-0012), reemplazo automático de versiones del mismo alcance exacto protegido por un índice único parcial (ADR-0011), resolución de vencimientos con especificidad determinista por tupla (ADR-0015), vista previa de vencimiento por cliente/obligación/periodo con aviso de datos ficticios.
- **Categorías tributarias del cliente**: catálogo abierto y asignación con vigencia histórica (restricción `EXCLUDE USING gist`), desacoplado de `clients.tax_regime`; `is_good_taxpayer` ahora se calcula dinámicamente (ADR-0013).
- Reloj inyectable (`app/core/clock.py`, ADR-0008) para pruebas de expiración/bloqueo sin `sleep`.
- Migraciones `0002_auth_hardening`, `0003_tax_categories`, `0004_tax_schedule_engine`, todas verificadas: upgrade desde `0001` con datos preexistentes, creación desde una base nueva sin `pgcrypto` preinstalada, downgrade × 3 y re-upgrade.
- Frontend: `/tax-categories` (catálogo y asignación por contribuyente) y `/tax-schedules` (fuentes, importación de CSV, revisión/aprobación, comparación de versiones, vista previa de vencimiento).
- ADRs 0008–0015.
- 55 pruebas automatizadas nuevas de backend (131 en total), 89 % de cobertura; `ruff`, `tsc --noEmit`, `eslint` y `next build` sin errores.

### Verificado manualmente en navegador
- Login → bloqueo tras intentos fallidos → creación de categoría → asignación a contribuyente → creación de fuente de cronograma → importación de CSV ficticio → confirmación → envío a revisión → aprobación → vista previa de vencimiento con aviso de datos de demostración.

### Notas de implementación
- `clients.is_good_taxpayer` (columna del Bloque 1) queda congelada: ya no se lee ni se escribe desde código nuevo (ver ADR-0013).
- Se eliminó `tenant_repository.get_default()` (y el archivo completo) al retirarse el fallback de "tenant por defecto" del login.

## [0.1.0] - 2026-07-22 - Fase 0 + Fase 1 bloque 1

### Añadido
- Estructura de repositorio, documentación base (docs/) y ADRs 0001–0007.
- Backend FastAPI (Python 3.12, SQLAlchemy 2.0 síncrono, Alembic) con autenticación por cookie de sesión, CRUD de contribuyentes, validación de RUC (dígito verificador), catálogo de obligaciones y asignación por cliente, auditoría básica.
- Proveedor de consulta de RUC simulado (`FakeRucLookupProvider`) y stub del proveedor real (`SunatRucLookupProvider`, lanza `NotImplementedError`).
- Modelos SQLAlchemy y migración inicial de Alembic (`tenants`, `users`, `sessions`, `audit_logs`, `clients`, `obligation_catalog`, `client_obligations`).
- Frontend Next.js 16 (App Router, Turbopack) con login, listado/alta/edición de contribuyentes, cambio de estado de servicio y gestión de obligaciones asignadas. PWA con manifest y service worker manual (ver ADR-0006).
- Docker Compose (`db`, `backend`, `frontend`, los tres con healthcheck) y scripts PowerShell: `install`, `start`, `stop`, `restart`, `migrate`, `seed`, `backup`, `restore`, `test`, `reset-dev`, `logs`.
- 44 pruebas automatizadas de backend (unitarias, integración y arranque) en verde; `ruff`, `tsc --noEmit` y `eslint` sin errores.

### Verificado manualmente en navegador
- Login → listado de contribuyentes → alta con consulta de RUC simulada → cambio de estado de servicio → asignación/desactivación/remoción de obligaciones → logout → redirección a `/login` en rutas protegidas.
- `backup.ps1` → `restore.ps1` → `reset-dev.ps1` contra datos ficticios.

### Notas de implementación
- `create-next-app` instaló Next.js 16.2.11, más reciente que lo previsto en el ADR original; Turbopack es el compilador por defecto desde esta versión. Se actualizó ADR-0006 (PWA): Serwist es incompatible con Turbopack, se usa un service worker escrito a mano.
- `middleware.ts` fue renombrado a `proxy.ts` en Next.js 16 (ver `docs/ARCHITECTURE.md`).

