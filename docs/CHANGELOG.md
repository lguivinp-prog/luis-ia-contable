# Changelog — Luis IA Contable

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/).

## [Unreleased]

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
