# ADR-0063: OAuth Google — intentos persistidos, PKCE y autorización incremental (Bloque 3.1)

## Estado
Aceptado (con una validación técnica pendiente explícita — ver "Resultado del spike PKCE" y
"GOOGLE_LIVE_VALIDATION_DEFERRED")

## Contexto
Un flujo OAuth clásico protege el `state`/CSRF con una cookie de sesión firmada o un valor efímero en memoria.
Esto no basta aquí: el callback de Google es una redirección de navegador completa (no un `fetch`), el
`code_verifier` de PKCE debe sobrevivir entre la petición que inicia la autorización y la que la completa, y
el intercambio del código con Google es una llamada HTTP lenta que nunca debe ejecutarse mientras se sostiene
una transacción de escritura abierta.

## Decisión

### `state` hasheado, persistido, de un solo uso
El valor `state` (≥256 bits, `secrets.token_urlsafe`) nunca se guarda en claro — solo `SHA-256(state)` en
`google_oauth_authorization_attempts.state_hash` (`UNIQUE`, formato validado por
`ck_google_oauth_attempts_state_hash_format`). El `code_verifier` de PKCE se cifra (ADR-0064) mientras el
intento está `PENDING` y se purga de la fila en el instante en que el `state` se consume.

### Máquina de estados de 4 valores: `PENDING → PROCESSING → SUCCEEDED|FAILED`
`PENDING` (verifier presente) → `PROCESSING` (verifier purgado, `consumed_at` fijado, transacción corta
comiteada) → `SUCCEEDED`/`FAILED`. `ck_google_oauth_attempts_status_coherence` fija qué columnas son NULL/NOT
NULL en cada estado — en particular, `FAILED` admite `consumed_at` NULL (Google rechazó antes de aceptar el
`state` para procesamiento, p. ej. `AUTHORIZATION_DENIED` recibido directamente) o NOT NULL (el fallo ocurrió
después de haber pasado por `PROCESSING`, p. ej. `TOKEN_EXCHANGE_FAILED`/`USERINFO_FAILED`/
`REFRESH_TOKEN_REQUIRED`/`GOOGLE_ACCOUNT_MISMATCH`). Un segundo callback con el mismo `state` encuentra un
estado distinto de `PENDING` y devuelve **siempre** `INVALID_STATE`, sin excepción y sin ninguna segunda
llamada a Google. Un intento con `expires_at` vencido (`STATE_EXPIRED`, TTL de 10 minutos) se purga y marca
`FAILED` en el mismo movimiento en que se descubre, nunca queda indefinidamente `PENDING` con un verifier
cifrado sin consumir.

### Autorización incremental restringida por bloque
`POST /integrations/google/authorize` admite `capability: Literal["IDENTITY"]` en este bloque — el esquema
Pydantic literalmente no acepta `CALENDAR`/`DRIVE` todavía (422), y el frontend no ofrece esas opciones. Cada
bloque futuro (3.2/3.3) amplía el `Literal` únicamente cuando esa capacidad esté realmente implementada.
`include_granted_scopes=true` se envía siempre; la allowlist real de scopes por capacidad vive en el backend
(`SCOPES_BY_CAPABILITY`), nunca en el payload del cliente.

### Reglas de `refresh_token`/scopes
Primera conexión sin `refresh_token` en la respuesta → `REFRESH_TOKEN_REQUIRED`, ninguna conexión `ACTIVE` se
crea. Reconexión desde `NEEDS_REAUTH` sin `refresh_token` nuevo (la fila ya tiene `refresh_token_encrypted`
NULL por la coherencia de estado) → mismo error, la conexión permanece `NEEDS_REAUTH`. Autorización
incremental sobre `ACTIVE` sin `refresh_token` en la respuesta → se conserva el cifrado existente, nunca se
reemplaza por `NULL`. Scopes ausentes en la respuesta (típico de un refresco de token) → se conservan los
`scopes_granted` previos; scopes presentes → se almacena exactamente el conjunto devuelto por Google.

## Resultado del spike PKCE
**No ejecutado en esta ronda de implementación.** El spike descrito en la Sección 27 del plan v3 requiere un
cliente OAuth de Google Cloud real (tipo Web, con `http://localhost:<puerto>/...` registrado como redirect
URI) y completar un consentimiento interactivo con una cuenta Google real — ninguno de los dos estuvo
disponible en esta sesión de implementación autónoma. `GoogleOAuthProvider` implementa la Opción A del plan
(PKCE `code_challenge_method=S256` enviado junto con `client_secret` en el intercambio de token), pero **su
aceptación real por Google no ha sido verificada empíricamente**. Antes de cualquier uso en producción, debe
ejecutarse el spike descrito en el plan v3 y documentarse el resultado real aquí, reemplazando este párrafo.
Si el flujo con PKCE no es aceptado por Google para un cliente de tipo Web, la alternativa ya prevista es usar
el flujo *Web Server* estándar solo con `client_secret` + `state` de un solo uso (sin PKCE), sin necesidad de
rediseñar la máquina de estados de intentos.

## GOOGLE_LIVE_VALIDATION_DEFERRED

**Motivo:** el usuario no dispone todavía de un proyecto Google Cloud ni de un cliente OAuth real. Se decide
cerrar el Bloque 3.1 como infraestructura Google **preparada pero desactivada por configuración**, en vez de
mantener el checkpoint bloqueado indefinidamente a la espera de credenciales externas que están fuera del
control del desarrollo.

**Qué está implementado y verificado (sin depender de Google real):** la máquina de estados de intentos de 4
valores, el ciclo de vida de conexión de 3 estados, el cifrado con sobre versionado + AAD + fingerprint de
clave, la persistencia real de `google_oauth_connections`/`google_oauth_authorization_attempts`, los 4
endpoints HTTP, la matriz de permisos, el backup/restore real con purga condicionada por `key_id`+fingerprint,
y 21+ pruebas de servicio más las de API — todo contra `FakeOAuthProvider`, sin ninguna llamada de red.

**Qué queda diferido explícitamente:** el spike de aceptación real de PKCE por un cliente OAuth de tipo Web de
Google (Sección 27 del plan v3, ver "Resultado del spike PKCE" arriba) y el smoke test end-to-end contra una
cuenta Google real. **No se afirma en ningún punto de este documento ni del código que la conexión Google real
haya sido validada.**

**Cómo se desactiva el proveedor real sin credenciales:** `app.core.dependencies.get_oauth_provider()` nunca
construye `GoogleOAuthProvider` (el adaptador que hace llamadas HTTP reales a `accounts.google.com`/
`oauth2.googleapis.com`) a menos que `GOOGLE_OAUTH_CLIENT_ID` y `GOOGLE_OAUTH_CLIENT_SECRET` estén ambos
presentes — incluso si `GOOGLE_OAUTH_PROVIDER=google` quedara mal configurado por error. `GET /integrations/
google/status` expone un campo público `configured: bool` (`app.services.google_oauth_service.
is_google_oauth_configured()`), calculado únicamente a partir de la presencia de esas dos variables, sin tocar
la base de datos de cifrado ni realizar ninguna llamada de red. Mientras `configured=false`: el endpoint de
estado sigue respondiendo `200` con normalidad (nunca `500`), no se emite ninguna advertencia repetitiva en
logs, no se intenta ninguna conexión de red, y la interfaz de `/settings/integrations/google` muestra
"Google no configurado en este entorno" en vez de ofrecer un botón que termine en un error `409`. El resto del
sistema (tareas operativas, declaraciones, honorarios, documentos, agenda) continúa operando con total
normalidad — la integración Google es estrictamente aditiva y opcional.

**Cuándo se vuelve obligatoria la validación real:** antes de habilitar el Bloque 3.2 (Calendar) o el Bloque
3.3 (Drive), o en cualquier momento en que se decida habilitar Google en un entorno con usuarios reales, debe
ejecutarse el spike de PKCE y el smoke test descritos en la Sección 27/28 del plan v3, y el resultado debe
reemplazar el párrafo "Resultado del spike PKCE" de este documento. Habilitar Calendar/Drive sin esa
validación repetiría, sin haberla resuelto nunca, la única incertidumbre técnica real que queda abierta en
este bloque.

## Alternativas consideradas
- **`state` firmado sin tabla (HMAC con `session_secret`)**: rechazada — no permite invalidación real de un
  segundo uso ni persistir el `code_verifier` de forma auditable; una tabla dedicada con bloqueo de fila
  resuelve ambos problemas de forma verificable.
- **3 estados (`PENDING`/`CONSUMED`/`FAILED`)**: rechazada — no distinguía "el `state` ya fue aceptado y su
  verifier purgado" de "la conexión ya se persistió exitosamente", lo que dificultaba decidir con precisión
  cuándo `consumed_at` debía ser NULL en un `FAILED`.
- **Pedir todos los scopes (identidad + Calendar + Drive) en la primera conexión**: rechazada — viola el
  principio de autorización incremental de Google y expone permisos no usados desde el primer día.

## Consecuencias
- Los Bloques 3.2/3.3 solo necesitan ampliar `SCOPES_BY_CAPABILITY`/el `Literal` de `capability` — la máquina
  de estados del intento y el flujo de callback no cambian.
- Toda prueba automática usa `FakeOAuthProvider`; ninguna prueba de la suite normal depende de la validación
  real de PKCE pendiente.
