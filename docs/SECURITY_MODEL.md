# Modelo de seguridad — Luis IA Contable

## Principios

1. **Control humano sobre toda actuación sensible.** El sistema nunca presenta declaraciones, realiza pagos, solicita fraccionamientos, modifica datos del RUC, ni responde notificaciones automáticamente.
2. **Ningún secreto en texto plano** — en código, JSON, logs, capturas, prompts de IA, mensajes de error o backups sin cifrar.
3. **Separación entre sesión de aplicación y bóveda de credenciales.** El usuario puede consultar información general sin desbloquear la bóveda; necesita desbloquearla explícitamente para acceder a credenciales SOL (Fase 4).
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

## Bóveda de credenciales SOL (diseño — Fase 4, no implementado)

Arquitectura prevista:
1. Clave de cifrado de datos (DEK) aleatoria por instalación.
2. Cada secreto se cifra con AES-256-GCM (cifrado autenticado, nonce único por operación).
3. La DEK se protege con una clave derivada de la contraseña maestra mediante Argon2id.
4. Protección adicional opcional ligada al equipo mediante Windows DPAPI.
5. Versión de esquema criptográfico almacenada junto al secreto, para permitir rotación.
6. Los secretos se limpian de memoria tan pronto como sea razonablemente posible.
7. Ninguna credencial SOL se envía al frontend salvo en una operación estrictamente controlada y auditada.

En esta entrega, las columnas `sol_username_encrypted`, `sol_password_encrypted`, `sol_cipher_version` existen en el modelo `clients` pero no tienen lógica de lectura/escritura: cualquier intento lanza `NotImplementedError`.

## Acceso desde la red local (LAN)

- **Deshabilitado por defecto.** Los puertos de `docker-compose.yml` se exponen solo en `localhost` durante esta fase.
- Para habilitarlo en el futuro se requiere: autenticación reforzada, HTTPS local o canal privado equivalente, restricción de firewall al perfil de red privada, ausencia de reenvío de puertos del router, bloqueo por intentos fallidos, sesiones con vencimiento corto, y opción de desactivación inmediata.
- La base de datos **nunca** se expone directamente a la red; solo el backend accede a Postgres.

## RBAC real: catálogo de permisos y aplicación en routers (Bloque 4, ADR-0050/ADR-0051/ADR-0052)

El sistema implementa RBAC real sobre los tres roles fijos (`admin`/`contador`/`asistente`, `CHECK
ck_users_role`). `Permission(str, enum.Enum)` (`backend/app/core/permissions.py`) declara 30 permisos
explícitos; `ROLE_PERMISSIONS` es el único mapa rol→permisos, sin tabla relacional (`admin`=30,
`contador`=27, `asistente`=13). `has_permission`/`check_permission` fallan **cerrado**: un rol o permiso no
reconocido nunca autoriza por defecto. `require_permission(permission)` es la dependencia que se aplica en
la firma de cada endpoint mutante o de lectura de los 15 routers existentes; reemplaza por completo a la
antigua `ensure_admin_role` (eliminada), incluido su único uso previo (cierre excepcional de una declaración,
ahora gateado por `Permission.DECLARATION_CLOSE_OVERRIDE`).

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
prefijo). `test_permission_matrix_api.py` confirma 200/403 real para los 30 permisos.

**Gateo frontend como UX, no como control de acceso**: `hasPermission(user, permission)`
(`frontend/src/lib/permissions.ts`) consume `permissions: list[str]` calculado por el backend en
`GET /auth/me` — no existe copia del mapa de roles en TypeScript. Oculta o deshabilita controles mutantes en
la interfaz para reducir fricción, pero cada endpoint sigue protegido en el backend independientemente del
estado visual del cliente.

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
