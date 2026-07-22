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
- Bloqueo por intentos fallidos y bloqueo por inactividad: **diseñado, pendiente de implementar en el siguiente bloque de Fase 1** (requiere UI de estado de bloqueo).

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
