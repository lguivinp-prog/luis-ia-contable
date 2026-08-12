# Modelo de seguridad — Luis IA Contable

## Principios

1. **Control humano sobre toda actuación sensible.** El sistema nunca presenta declaraciones, realiza pagos, solicita fraccionamientos, modifica datos del RUC, ni responde notificaciones automáticamente.
2. **Ningún secreto en texto plano** — en código, JSON, logs, capturas, prompts de IA, mensajes de error o backups sin cifrar.
3. **Separación entre sesión de aplicación y bóveda de credenciales.** El usuario puede consultar información general sin desbloquear la bóveda; necesita desbloquearla explícitamente para operar sobre credenciales SOL (Fase 5, Bloque 5.1 — implementado, ver más abajo).
4. **Aislamiento por tenant** en cada consulta, incluso con un solo tenant activo hoy.

## Autenticación (implementado en esta entrega)

- Contraseña de usuario: hash Argon2id (`argon2-cffi`), parámetros de coste conservadores para hardware de escritorio (ver `backend/app/core/security.py`).
- Sesión: token opaco aleatorio (`secrets.token_urlsafe(32)`) entregado en cookie `HttpOnly`, `SameSite=Strict`, `Secure` activable por entorno (`SESSION_COOKIE_SECURE`). Solo se persiste el hash SHA-256 del token en la tabla `sessions`; el valor crudo nunca se guarda.
- Expiración configurable (`SESSION_TTL_HOURS`), revocación explícita al hacer logout (`revoked_at`).
- Expiración por inactividad (`SESSION_INACTIVITY_TIMEOUT_MINUTES`, actualiza `last_seen_at` en cada request) y expiración absoluta (`expires_at`); cualquiera de las dos revoca la sesión y registra un `security_events` distinto (`SESSION_EXPIRED_INACTIVITY` / `SESSION_EXPIRED_ABSOLUTE`).
- Rotación del ID de sesión en cada login; revocación de todas las demás sesiones al cambiar la contraseña (`SESSION_REVOKED_PASSWORD_CHANGE`); límite configurable de sesiones concurrentes por usuario (`SESSION_MAX_CONCURRENT`, revoca la más antigua al excederse — `SESSION_REVOKED_CONCURRENT_LIMIT`); vista de sesiones activas y acción "cerrar otras sesiones" (`GET /auth/sessions`, `POST /auth/sessions/revoke-others`).
- Frontend: `InactivityGuard` muestra una advertencia con cuenta regresiva visible antes del cierre por inactividad, con opción "Continuar sesión" (`POST /auth/keep-alive`, protegido por CSRF) y cierre automático si no hay respuesta. Nada sensible se guarda en `localStorage`.

### Bloqueo por intentos fallidos (ver ADR-0009)

- Umbral, ventana, duración de bloqueo y máximo de backoff configurables por variables de entorno no secretas (`LOGIN_MAX_ATTEMPTS`, `LOGIN_ATTEMPT_WINDOW_MINUTES`, `LOGIN_LOCKOUT_DURATION_MINUTES`, `LOGIN_LOCKOUT_MAX_MINUTES`). Backoff progresivo (duplica hasta el máximo configurado); nunca bloqueo permanente.
- El identificador de login se normaliza (NFC + minúsculas + `strip`, `app/core/identifier.py`) antes de contar intentos, para que variaciones de mayúsculas/espacios no permitan eludir el bloqueo.
- **Mitigación de enumeración por tiempo**: si el identificador no corresponde a ninguna cuenta (o la cuenta está inactiva), se ejecuta de todos modos una verificación Argon2id contra un hash ficticio precalentado (`get_dummy_password_hash()`), con el mismo costo que una contraseña incorrecta real.
- **Separación definitiva `security_events` / `audit_logs`**: ningún fallo de login (exista o no la cuenta) escribe en `audit_logs`; ambos casos generan exactamente la misma huella (`login_attempts` + `security_events`), con el mismo código HTTP, mensaje y forma de respuesta. `security_events.tenant_id`/`user_id` pueden rellenarse internamente cuando se identificó una cuenta real, pero **nunca se exponen en la respuesta HTTP**. `audit_logs` queda reservado para acciones dentro de una sesión ya autenticada. Ver ADR-0009 para el detalle completo.
- Solicitudes recibidas mientras la cuenta está bloqueada responden `429` + `Retry-After`, sin insertar nuevas filas en `login_attempts` ni modificar la duración del bloqueo.
- Desbloqueo administrativo local: `backend/app/cli/unlock_login.py` / `scripts/unlock-login.ps1`; nunca borra el historial de intentos, solo levanta el bloqueo vigente.

### CSRF (ver ADR-0010)

- Double-submit cookie: `csrf_token` (no `HttpOnly`) + header `X-CSRF-Token`, verificado con `hmac.compare_digest` (tiempo constante) contra el hash guardado en `sessions.csrf_token_hash`.
- Rota en cada login, se borra en logout, se invalida automáticamente al revocarse la sesión.
- Toda mutación (`POST`/`PUT`/`PATCH`/`DELETE`) la exige; la API nunca depende únicamente del middleware del frontend para esta protección.

## Bóveda de credenciales SOL (Fase 5, Bloque 5.1 — implementado, ver ADR-0068 a ADR-0071)

Jerarquía de tres niveles: contraseña personal del usuario → (Argon2id) → KEK del slot → (AES-256-GCM) →
`vault_root_key` (envuelta, una copia por slot: `USER` o `RECOVERY`) → (AES-256-GCM) → `credential_dek`
(envuelta bajo `vault_root_key`, versionada) → (AES-256-GCM, por campo) → `sol_username`/`sol_password`.
`vault_root_key` nunca cifra directamente una credencial y nunca rota en operación ordinaria; solo
`credential_dek` rota (ADR-0070). Cada capa usa AES-256-GCM con sobre versionado (`format_version` + nonce de
12 bytes nuevo en cada operación + ciphertext+tag) y AAD obligatorio construido de forma determinista y
centralizada, atando cada secreto a su fila/tabla/tenant/identificador — mover un ciphertext a otro contexto
falla la autenticación GCM de inmediato. Argon2id vía `argon2.low_level.hash_secret_raw` (nunca
`PasswordHasher`), parámetros productivos fijos: `memory_cost_kib=393216` (384 MiB), `time_cost=2`,
`parallelism=1`, `hash_len=32`, `kdf_version=1`, persistidos por slot (un cambio futuro de parámetros no
invalida slots existentes). `Argon2ConcurrencyGate` (`app/core/sol_vault_argon2_gate.py`) limita a una sola
derivación Argon2id simultánea por proceso (semáforo, espera acotada, liberación garantizada) — evita agotar
memoria bajo uso concurrente real. No se usa Windows DPAPI: se reutiliza el mismo patrón de sobre + AAD ya
productivo en `app/core/integration_crypto.py`, portable y sin atarse a una API específica del sistema
operativo.

**Single-worker backend obligatorio en v1**: `enforce_single_worker_gate` se ejecuta a nivel de módulo en
`app/main.py`, antes de construir la aplicación — más de un worker hace fallar el arranque completo del
proceso, porque el `VaultUnlockRegistry` (ver más abajo) es memoria de un único proceso.

**`VaultUnlockRegistry` — ninguna clave desenvuelta sobrevive fuera de la memoria de un único proceso**
(`app/core/sol_vault_registry.py`): registro puramente en memoria, nunca persistido en PostgreSQL, Redis ni
archivo alguno, indexado por `(tenant_id, vault_id, user_id, app_session_id)` con `app_session_id` siempre el
`id` real de una fila de `sessions`. `resolve()` revalida en vivo, en cada uso, contra la sesión real
subyacente (no revocada, no vencida, tenant/usuario correctos) y contra la propia autorización de desbloqueo
(vigencia, operación permitida, alcance de cliente si aplica) — cualquier fallo purga la entrada de inmediato.
`vault_root_key` se guarda en un `bytearray` (mutable) para poder sobrescribirse con ceros al purgar
(`zeroize()`) — esto **no es una garantía criptográfica de borrado físico de memoria** en CPython (copias
transitorias intermedias pueden no sobrescribirse); se documenta el límite real en vez de afirmar un borrado
perfecto (ver ADR-0071 para el detalle). El estado `LOCKED` nunca se persiste como columna — es la ausencia
de una entrada viva en este registro para la combinación exacta de tenant/bóveda/usuario/sesión.
**Reiniciar el backend bloquea automáticamente todas las bóvedas de todos los usuarios, sin excepción** — el
registro nace vacío en cada arranque del proceso, por diseño, nunca como limitación a resolver.

Recuperación en dos pasos (inicializar/reemplazar → confirmar), con el código de recuperación mostrado
**una sola vez** en la respuesta HTTP inmediatamente posterior a generarlo — ningún endpoint puede volver a
mostrarlo. `recovery_confirmed_at` vive exclusivamente en la fila del slot `RECOVERY`, nunca en `sol_vaults`
(fe de erratas del spike, corregida en el diseño productivo). Invitaciones de dos actores con asociación
efímera invitador→invitado que también vive solo en memoria de proceso, nunca en base de datos (ver
ADR-0069). Toda credencial SOL nueva o actualizada aterriza siempre en `NEEDS_VALIDATION` — este bloque
**no** implementa Playwright, acceso a SUNAT, Buzón SOL ni sesiones de acceso SOL reales; la validación real
contra SUNAT queda fuera de alcance (Bloque 5.2, no diseñado todavía). Ningún endpoint de este bloque
devuelve una contraseña SOL en claro, un ciphertext, una sal, una clave envuelta, ni el handle interno del
registro — verificado por pruebas dedicadas de redacción y por un barrido estático del código fuente
(backend y frontend) que falla si alguno de esos valores aparece fuera de su uso legítimo interno.

## Estado de la integración Google (`GOOGLE_LIVE_VALIDATION_DEFERRED`, ver ADR-0063)

El Bloque 3.1 está implementado y verificado en su totalidad contra `FakeOAuthProvider` (sin ninguna llamada
de red), pero **no se afirma que la conexión Google real haya sido validada**: el spike de aceptación de PKCE
por un cliente OAuth de Google real y el smoke test end-to-end con una cuenta Google real quedan diferidos —
el usuario no dispone todavía de un proyecto Google Cloud. Mientras `GOOGLE_OAUTH_CLIENT_ID`/
`GOOGLE_OAUTH_CLIENT_SECRET` estén vacíos, `app.core.dependencies.get_oauth_provider()` nunca construye el
adaptador real, incluso si `GOOGLE_OAUTH_PROVIDER` quedara mal configurado. `GET /integrations/google/status`
expone `configured: bool` calculado solo a partir de esas dos variables (sin tocar la clave de cifrado ni la
red); mientras sea `false`, la interfaz muestra "Google no configurado en este entorno" en vez de ofrecer un
botón de conexión que termine en un `409`. La ausencia de estas credenciales no afecta ninguna función local
del sistema (tareas, declaraciones, honorarios, documentos, agenda). Esta validación real será obligatoria
antes de habilitar los Bloques 3.2 (Calendar) o 3.3 (Drive).

## Orquestador de arranque local (bloque transversal, ver ADR-0066)

`scripts/start-luis-ia-contable.ps1`/`stop-luis-ia-contable.ps1` y la tarea opcional de inicio automático de
Windows (`scripts/install-autostart.ps1`) se ejecutan siempre en el contexto del usuario actual de Windows,
nunca con privilegios elevados por diseño (`RunLevel Limited`), y nunca almacenan la contraseña de esa cuenta
de Windows (`LogonType Interactive`, la tarea solo corre mientras el usuario tiene sesión iniciada). El
`ExecutionPolicy Bypass` del acceso directo de escritorio se aplica únicamente al proceso invocado por ese
acceso directo, nunca cambia la política de ejecución de PowerShell del sistema.

La sincronización de arranque (`backend/app/cli/startup_sync.py`) nunca realiza ninguna llamada de red — toda
integración externa (Google Calendar/Drive, SUNAT SOL, Gmail, WhatsApp) se reporta como no implementada/no
configurada sin contactar ningún servicio, y nunca bloquea el uso local del sistema. Los logs locales
(`logs/startup/startup-<fecha>.log`) están redactados por diseño: nunca registran contraseñas, tokens,
`client_secret`, claves de cifrado, el contenido de un archivo `.env`, contenido de documentos, ni números
telefónicos completos — solo estado de servicios, migraciones aplicadas, identificadores de tenant
(prefijo corto de UUID), conteos de tareas y errores normalizados. Se aplica retención simple (máximo 14
archivos), nunca crecimiento indefinido.

Exclusión mutua entre dos inicios simultáneos: `pg_try_advisory_lock` de PostgreSQL con una clave de dominio
fija, nunca un mecanismo del sistema operativo — se libera siempre, incluso ante una excepción o un apagado
abrupto (el servidor libera los advisory locks de sesión al cerrarse esa conexión).

## Cifrado de credenciales de integración (Fase 3, Bloque 3.1 — implementado, ver ADR-0064)

Independiente de la bóveda SOL (Fase 5, Bloque 5.1, ver arriba — cada una usa su propio esquema de sobre y su
propia clave de cifrado, sin compartir material criptográfico entre ambas): `access_token`/`refresh_token`/
`code_verifier` de la
integración OAuth Google se cifran con AES-256-GCM en un sobre versionado (`format_version` + nonce de 12
bytes + ciphertext+tag), con AAD obligatorio que ata cada secreto a su fila/tabla/tenant/usuario/tipo de
token (`backend/app/core/integration_crypto.py`). Una sola clave activa por instalación
(`INTEGRATION_TOKEN_ENCRYPTION_KEY` + `INTEGRATION_TOKEN_KEY_ID`), sin rotación automática en v1. Si Google
está configurado pero la clave falta o es inválida, la integración falla cerrada
(`INTEGRATION_MISCONFIGURED`) sin persistir ningún token; el resto del sistema no se ve afectado. Un
fingerprint HMAC-SHA256 no secreto de la clave activa se guarda en el manifiesto de backup para detectar, al
restaurar, si el entorno de destino tiene una clave real distinta aunque reutilice la misma etiqueta
`key_id` — en ese caso las conexiones Google se purgan y pasan a `NEEDS_REAUTH`.

### Riesgo operativo — modo Testing de Google Cloud
Un proyecto OAuth de Google Cloud en estado "Testing" (no verificado, no "Production") tiene reglas distintas
según el scope: `openid`/`email` (Bloque 3.1, identidad) son scopes básicos, no sujetos a la expiración de 7
días de `refresh_token` que sí aplica a scopes sensibles como Calendar/Drive (Bloques 3.2/3.3) mientras el
proyecto no esté verificado. Un smoke test inicial exitoso al ampliar a Calendar/Drive no demuestra que la
conexión persista más allá de una semana si el proyecto sigue en Testing — debe documentarse en el manual de
configuración antes de operar con esos bloques.

## Acceso desde la red local (LAN)

- **Deshabilitado por defecto.** Los puertos de `docker-compose.yml` se exponen solo en `localhost` durante esta fase.
- Para habilitarlo en el futuro se requiere: autenticación reforzada, HTTPS local o canal privado equivalente, restricción de firewall al perfil de red privada, ausencia de reenvío de puertos del router, bloqueo por intentos fallidos, sesiones con vencimiento corto, y opción de desactivación inmediata.
- La base de datos **nunca** se expone directamente a la red; solo el backend accede a Postgres.

## RBAC real: catálogo de permisos y aplicación en routers (Bloque 4, ADR-0050/ADR-0051/ADR-0052)

El sistema implementa RBAC real sobre los tres roles fijos (`admin`/`contador`/`asistente`, `CHECK
ck_users_role`). `Permission(str, enum.Enum)` (`backend/app/core/permissions.py`) declara 51 permisos
explícitos (30 desde Bloque 4, `document:file_manage` desde Bloque 5, 8 permisos de tareas operativas desde
Fase 2/Bloque 1, `integration:connect`/`integration:disconnect` desde Fase 3/Bloque 3.1, `communication:draft`
desde Fase 3/Bloque 3.2, 9 permisos de bóveda SOL/credenciales desde Fase 5/Bloque 5.1 (ver más abajo));
`ROLE_PERMISSIONS` es el único mapa rol→permisos, sin tabla relacional (`admin`=51, `contador`=44,
`asistente`=22). `has_permission`/`check_permission` fallan **cerrado**: un rol o
permiso no reconocido nunca autoriza por defecto. `require_permission(permission)` es la dependencia que se
aplica en la firma de cada endpoint mutante o de lectura de todos los routers existentes; reemplaza por
completo a la antigua `ensure_admin_role` (eliminada), incluido su único uso previo (cierre excepcional de una
declaración, ahora gateado por `Permission.DECLARATION_CLOSE_OVERRIDE`).

**Contraseña temporal**: el alta de usuario y el restablecimiento administrativo de contraseña fijan
`must_change_password=true`; la dependencia `require_password_ready` (prerequisito de `require_permission`)
bloquea con 403 `PASSWORD_CHANGE_REQUIRED` toda ruta de negocio hasta que el usuario complete el cambio vía
`POST /auth/change-password` (único endpoint que limpia el flag).

**Protección concurrente del último admin**: `deactivate_user`/`change_role` usan una única consulta
`SELECT ... FOR UPDATE ... ORDER BY id` (`user_repository.lock_target_and_active_admins`) para bloquear a la
vez la fila objetivo y todas las filas admin activas del tenant en un orden determinista, evitando deadlock
bajo operaciones concurrentes simétricas; el predicado puro `removes_last_active_admin` decide el rechazo.
Un usuario no puede desactivarse ni cambiar su propio rol.

**Cobertura estructural verificada**: `test_route_permission_coverage.py` recorre `app.routes` y falla si
cualquier endpoint bajo `/api/v1/` no declara `require_permission` y no está en la allowlist cerrada
`EXEMPT_ROUTES` (los 8 endpoints de autoservicio de `auth.py` + 2 de `health.py` — nunca una exención por
prefijo). `test_permission_matrix_api.py` confirma 200/403 real para los 51 permisos.

**Gateo frontend como UX, no como control de acceso**: `hasPermission(user, permission)`
(`frontend/src/lib/permissions.ts`) consume `permissions: list[str]` calculado por el backend en
`GET /auth/me` — no existe copia del mapa de roles en TypeScript. Oculta o deshabilita controles mutantes en
la interfaz para reducir fricción, pero cada endpoint sigue protegido en el backend independientemente del
estado visual del cliente.

## Gestión operativa de tareas: autorización por fila (Fase 2, Bloque 1, ADR-0057 a ADR-0061)

8 permisos nuevos (`task:read/create/update/complete/assign/cancel/sync`, `alert:read`); `task:sync` se
concede a los 3 roles (cualquiera puede disparar/consultar una sincronización idempotente); `task:assign`/
`task:cancel` quedan reservados a `admin`/`contador`.

**Única excepción del repositorio al patrón "permiso de rol, nunca de fila"**: `task:read` sigue siendo
permiso de rol puro (lectura total del tenant para los 3 roles), pero `task:update`/`task:complete` para el
rol `asistente` exigen además, bajo `lock_exact_task` con `populate_existing=True`, que
`task.assigned_to == current_user.id` con el valor recién releído — nunca un valor leído antes del bloqueo.
Operar una tarea ajena o sin responsable devuelve 403 (nunca 404, ya que la tarea es legible vía `task:read`;
ni 409). El frontend refleja esto deshabilitando (nunca solo ocultando) los controles mutantes sobre tareas
ajenas, con un mensaje explicativo. `POST .../comments` adquiere el mismo bloqueo de fila aunque no mute la
tarea, precisamente para resolver de forma determinista la carrera entre un comentario del asistente y una
reasignación concurrente por un admin/contador (verificado con una prueba de concurrencia real que fuerza
ambos órdenes). Ver ADR-0061 para el detalle completo y su justificación acotada.

## Comunicaciones asistidas por WhatsApp: ownership y redacción (Fase 3, Bloque 3.2, ADR-0067)

1 permiso nuevo (`communication:draft`), concedido a los 3 roles por igual — la restricción real no es de
permiso sino de fila: `admin`/`contador` ven y operan todos los borradores del tenant; el rol `asistente` solo
los que él mismo creó (`communication_drafts.created_by == current_user.id`). Operar un borrador ajeno
devuelve 403 `DRAFT_NOT_OWNED` (nunca 404, el borrador es legible por otros roles del mismo tenant). Esta es
la **segunda** excepción del repositorio al patrón "permiso de rol, nunca de fila" (la primera es la de
`operational_tasks`, ver ADR-0061) — cada una documentada en su propio ADR, sin generalizar el precedente
automáticamente a bloques futuros.

**Sin llamadas a proveedores externos, sin excepción de ADR-0037**: a diferencia del callback OAuth de Google
(ADR-0065), construir un enlace `wa.me` es una operación puramente local — no hay ninguna llamada HTTP lenta
que justifique una excepción al patrón estándar de una sola transacción por endpoint.

**Redacción estricta en auditoría y logs**: ninguna entrada de `AuditLog` para
`COMMUNICATION_DRAFT_CREATE`/`COMMUNICATION_OPEN_REQUEST`/`COMMUNICATION_MARK_SENT`/`COMMUNICATION_DISCARD`
incluye el cuerpo completo del mensaje, la URL `wa.me` completa (que embebería ese mismo cuerpo) ni el
teléfono completo — como máximo un sufijo `***####` de 4 dígitos. `backend/app/core/logging.py` extiende
`_SENSITIVE_FIELD_NAMES` con `message_body`, `recipient_phone`, `recipient_phone_e164`, `wa_url`.
`CommunicationDraftOut` (la forma expuesta por GET/list) omite deliberadamente `recipient_phone_e164` — solo
expone `recipient_phone_display`; el E.164 canónico que alimenta la URL nunca sale de un endpoint `GET`.

**El backend nunca abre WhatsApp ni confirma envío**: `POST .../mark-open-requested` solo calcula y devuelve
el enlace `wa.me` — abrirlo es una acción exclusiva del navegador del usuario (`window.open`, nunca
`window.location.href`, para no abandonar la aplicación). `POST .../mark-sent` es una autodeclaración humana,
nunca una verificación real de entrega. Ver ADR-0067 para la justificación completa de por qué no existe un
estado que implique confirmación de WhatsApp, y `communicationTerminology.test.ts` para la prueba automatizada
que impide reintroducir esa terminología en el frontend.

## Almacenamiento real de documentos (Bloque 5, ADR-0053 a ADR-0056)

**Validación de contenido real, nunca por extensión ni `Content-Type` declarado**: PDF/JPEG/PNG se detectan
por firma real de bytes (librería `filetype`); DOCX/XLSX requieren verificación estructural completa del ZIP
(límites de directorio central antes de descomprimir nada — máx. 2000 entradas, 200 MB descomprimidos, 100 MB
por entrada, ratio de compresión 100:1 — defensa contra ZIP-bombs; clasificación exclusiva por prefijo
`word/`/`xl/`; rechazo de variantes macro-habilitadas). Allowlist cerrada: PDF, JPEG, PNG, XLSX, DOCX —
cualquier otro contenido se rechaza con 422.

**Nombre interno no predecible, nombre original solo de presentación**: `storage_key` siempre se genera del
lado del servidor (`uuid4()` + extensión derivada del contenido real); `original_filename` se sanea (quita
ruta, rechaza control/CRLF, trunca a 255) antes de insertar, y nunca se usa para construir la clave física.
`Content-Disposition` de cada descarga incluye codificación RFC 6266 para nombres no ASCII, más
`X-Content-Type-Options: nosniff`.

**Aislamiento de tenant y prevención de path traversal**: `storage_key` prefijado por `tenant_id`; toda
consulta filtra por `tenant_id`; descarga revalida `file.tenant_id == current_user.tenant_id` como defensa en
profundidad además del filtro de la consulta. `LocalFileStorageBackend._resolve(key)` verifica
`Path.resolve().is_relative_to(root)` antes de cualquier operación de archivo. Ninguna respuesta de la API
expone `storage_key` ni ninguna ruta física (verificado explícitamente en pruebas); el reporte de integridad
usa `blob_fingerprint = SHA256(clave_relativa)` para blobs sin fila en BD.

**Permiso `document:file_manage`** (admin/contador, no asistente): gatea reemplazar y eliminar una versión —
nombrado deliberadamente para cubrir ambas operaciones, no solo "delete". La carga inicial reutiliza
`document:item_write` (asistente incluido); la descarga y el listado reutilizan `document:read` (los 3
roles); el reporte de integridad usa `document:manage`.

**Descarga siempre autenticada y en streaming**: nunca una ruta estática pública; `GET
.../files/{file_id}/download` requiere `document:read` y transmite el archivo en bloques de 1 MiB sin
cargarlo completo en memoria.

**Auditoría**: 4 acciones nuevas en `AuditAction` (`DOCUMENT_ITEM_FILE_UPLOAD`/`DOWNLOAD`/`REPLACE`/`DELETE`).

**Backup/restauración con verificación completa**: `backup.ps1`/`restore.ps1` detienen el backend durante la
captura y recalculan el SHA-256 del 100% de los blobs registrados (nunca una muestra) antes de dejar
`manifest.json`; `restore.ps1` toma un snapshot de seguridad previo y revierte automáticamente si la
verificación post-restauración falla. Ver ADR-0055 para el detalle completo, incluido un error real
encontrado y corregido durante la verificación manual (`extract` intentaba eliminar el punto de montaje del
volumen en sí, no solo su contenido).

## Exportación de reportes: sanitización CSV y límites (Bloque 3.2, ADR-0049)

`POST /api/v1/reports/export` sanea cada celda de un CSV con `sanitize_csv_cell` (`app/services/csv_utils.py`) para neutralizar *CSV formula injection* (celdas que empiecen con `=`, `+`, `-`, `@`, tab o retorno de carro reciben un apóstrofe antepuesto, evitando que Excel/Sheets las interprete como fórmula al abrir el archivo). El número de filas exportables está acotado por `settings.report_export_max_rows` (configurable, por defecto 20 000) para evitar exportaciones descontroladamente grandes. Tanto CSV como Excel se generan enteramente en memoria (`io.StringIO`/`io.BytesIO`), nunca como archivo temporal en disco. Cada exportación se audita (`REPORT_EXPORT`, con tipo de reporte, formato, conteo de filas y si se truncó).

## Auditoría

Toda acción sensible genera una fila en `audit_logs` (usuario, acción, entidad afectada, IP, user agent, timestamp). Los metadatos nunca incluyen el valor de un secreto. Ver [DATA_MODEL.md](DATA_MODEL.md) para la lista de acciones auditadas en esta entrega.

## Protecciones transversales (implementadas o previstas en el backend)

- Validación de entrada con Pydantic en todos los endpoints.
- Consultas parametrizadas vía SQLAlchemy (sin concatenación de SQL).
- Cookies `HttpOnly` + `SameSite=Strict`.
- CORS restringido (en esta fase, innecesario gracias al proxy same-origin de Next.js; se documentará explícitamente si se relaja en el futuro).
- Redacción de secretos en logs (nunca se loguea `password`, `token`, ni las columnas `sol_*`).
- Rate limiting y cabeceras de seguridad HTTP: **pendientes**, se añaden antes de habilitar acceso LAN (Fase 2+).

## Datos de prueba

Todos los datos de desarrollo (usuarios, contribuyentes, RUCs) son ficticios y se generan mediante `backend/app/seeds/`. Nunca se usan RUCs, razones sociales ni datos personales reales en el repositorio, en tests, ni en documentación.
