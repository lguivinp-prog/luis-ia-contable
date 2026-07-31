# ADR-0062: Propiedad y ciclo de vida de la conexión Google (Bloque 3.1)

## Estado
Aceptado

## Contexto
Fase 3 introduce la primera integración con un proveedor externo (Google). Luis usa la aplicación de forma
personal hoy, pero el sistema ya es multiusuario/multitenant (`admin`/`contador`/`asistente`). Un calendario o
identidad personal de Google no debe quedar accesible automáticamente a todo el tenant, y la conexión debe
sobrevivir a reautorizaciones, expiraciones y cambios de cuenta sin perder trazabilidad ni violar el
aislamiento multitenant ya establecido por el patrón de FK compuestas (`(id, tenant_id[, user_id])`).

## Decisión

### Cuenta Google individual por usuario, una fila reutilizada
`google_oauth_connections` tiene `UNIQUE(tenant_id, user_id)` (una conexión por usuario) y
`UNIQUE(tenant_id, google_account_id)` (una cuenta Google no puede conectarse dos veces en el mismo tenant).
Ningún usuario ve ni opera la conexión de otro, ni siquiera `admin`, en esta versión. La misma fila se
reutiliza a través de reconexiones y de reemplazos de cuenta — nunca se crea una fila nueva para el mismo
usuario, preservando `first_connected_at` como la fecha de la primera integración Google de ese usuario en
general, no de una cuenta Google específica.

### 3 estados persistidos, sin `REVOKED` separado
`ACTIVE`/`NEEDS_REAUTH`/`DISCONNECTED`. Una revocación detectada del lado de Google se representa como
`NEEDS_REAUTH` con `reauth_reason='TOKEN_REVOKED'` — no se introduce un cuarto estado solo para distinguir el
origen de la necesidad de reautenticación. `ck_google_oauth_connections_status_coherence` fija exactamente qué
columnas son NULL/NOT NULL en cada estado (tokens, expiración, motivos de reautenticación, metadatos de
desconexión), siguiendo el mismo patrón de `CHECK` de coherencia por estado ya usado en
`declaration_document_files` (ADR-0053) y `operational_tasks` (ADR-0057).

### Coherencia de cuenta Google — `google_account_id` condicionalmente inmutable
Mientras la conexión no está `DISCONNECTED`, cualquier autorización (incremental o de reconexión) debe
devolver el mismo `sub` (Google) ya almacenado; un `sub` distinto se rechaza con `409
GOOGLE_ACCOUNT_MISMATCH` sin modificar la conexión existente. Solo tras `DISCONNECTED` se permite conectar una
cuenta distinta: se actualiza `google_account_id`/`google_account_email`, se reemplazan los tokens, se
reinician `scopes_granted` a los de la nueva autorización, se limpian los campos de desconexión, se conserva
`first_connected_at`, se incrementa `version`, y se audita `INTEGRATION_GOOGLE_ACCOUNT_REPLACED`. Esta regla
no puede expresarse como un `CHECK` de PostgreSQL (no compara el valor anterior de la fila durante un
`UPDATE`) — se aplica en `google_oauth_service._apply_successful_authorization`/el chequeo de mismatch en
`process_callback`, cubierta por una prueba dedicada.

### Desactivación de usuario nunca llama a Google
`google_oauth_service.disconnect_for_user_deactivation`, invocada desde `user_service.deactivate_user`, solo
limpia tokens locales y marca `DISCONNECTED`/`SYSTEM` — nunca revoca en Google dentro de esa transacción. Los
recursos ya creados del lado de Google permanecen intactos.

## Alternativas consideradas
- **Cuenta Google compartida por tenant**: rechazada para v1 — un calendario/identidad de estudio compartido
  es una evolución futura razonable, pero complica la primera versión sin aportar valor inmediato dado el uso
  personal actual de Luis.
- **Permitir múltiples cuentas Google por usuario**: rechazada — sin caso de uso real hoy, añade superficie de
  ambigüedad (¿cuál sincroniza qué?) sin beneficio claro.
- **Un cuarto estado `REVOKED` persistido**: rechazado — `reauth_reason` ya distingue el motivo sin necesitar
  una rama adicional en el `CHECK` de coherencia ni en la lógica de la aplicación.

## Consecuencias
- Los Bloques 3.2/3.3 (Calendar/Drive) reutilizan la misma conexión vía autorización incremental — ninguno
  crea una tabla de conexión propia.
- Cualquier lógica futura de "cuenta compartida de estudio" requerirá su propio ADR y una migración de datos
  explícita — este documento no la anticipa ni la bloquea.
