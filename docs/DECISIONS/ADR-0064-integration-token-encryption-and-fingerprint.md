# ADR-0064: Cifrado de credenciales de integración — sobre versionado, AAD y fingerprint de clave

## Estado
Aceptado

## Contexto
Fase 3 es la primera vez que el repositorio persiste secretos de un proveedor externo (`access_token`/
`refresh_token`/`code_verifier` de Google). La bóveda SOL (Fase 5) todavía no está diseñada, y reutilizarla
apresuradamente acoplaría esta fase a un diseño inexistente; crear un segundo sistema de cifrado incompatible
tampoco es aceptable. El backup/restore existente (Bloque 5) ya maneja un dump completo de PostgreSQL — hay
que decidir qué pasa con estos secretos cifrados cuando se restaura en un entorno con una clave distinta.

## Decisión

### Sobre versionado AES-256-GCM con AAD obligatorio
Cada secreto (`access_token_encrypted`, `refresh_token_encrypted`, `code_verifier_encrypted`) se cifra como
`format_version (1 byte) + nonce (12 bytes) + ciphertext+tag` (`app/core/integration_crypto.py`). El AAD
(datos autenticados adicionales, no cifrados pero verificados) ata cada ciphertext a: nombre de la app, tabla,
identificador de fila, `tenant_id`, `user_id`, tipo de token (`ACCESS`/`REFRESH`/`PKCE_VERIFIER`) y `key_id`.
Mover un ciphertext de una fila/tabla/tenant/usuario/tipo a otro hace fallar la autenticación de GCM de
inmediato (`IntegrationDecryptionError`), verificado con una prueba dedicada.

### Una sola clave activa en v1, sin rotación automática
`INTEGRATION_TOKEN_ENCRYPTION_KEY` (32 bytes en base64) + `INTEGRATION_TOKEN_KEY_ID` (etiqueta no secreta).
Cambiar la clave mientras existen conexiones activas no está soportado — todas las filas cifradas con la
clave anterior quedarían indescifrables. Una rotación real requeriría un *key ring* (clave anterior + nueva
coexistiendo, recifrado explícito) — extensión futura no diseñada, nunca presentada como ya soportada. Si
Google está configurado pero la clave falta o es inválida, los endpoints de integración fallan cerrado
(`INTEGRATION_MISCONFIGURED`) sin persistir ningún token; el resto del sistema contable no se ve afectado.

### Fingerprint de clave para detectar un `key_id` reciclado con un valor distinto
`compute_key_fingerprint()` deriva `HMAC-SHA256(clave, "luisiacontable:integration-token-key:v1")` — un valor
no secreto (de un solo sentido) que detecta el escenario que el `key_id` por sí solo no puede: una
reinstalación que reutiliza la misma etiqueta `key_id` con un valor de clave real distinto en
`INTEGRATION_TOKEN_ENCRYPTION_KEY`. El manifiesto de backup (`scripts/backup.ps1`, vía
`app/cli/integration_key_info.py`) guarda `integration_token_key_id` + `integration_token_key_fingerprint` —
nunca la clave. `scripts/restore.ps1` recalcula el fingerprint del entorno de destino tras restaurar la base
de datos; si `key_id` **y** fingerprint coinciden con el manifiesto, las conexiones se conservan tal cual; si
cualquiera de los dos difiere (o el manifiesto no incluye el fingerprint, backups anteriores a esta
capacidad), se purgan los tokens de toda conexión `ACTIVE` y se marca `NEEDS_REAUTH` con
`reauth_reason='ENCRYPTION_KEY_CHANGED'` mediante `Invoke-GoogleIntegrationKeyReconciliation`.

## Alternativas consideradas
- **Reutilizar el diseño de la bóveda SOL (Fase 5)**: rechazada — esa fase no está diseñada aún; acoplarse a
  ella ahora congelaría decisiones que todavía no existen.
- **`key_id` únicamente, sin fingerprint**: rechazada explícitamente — no detecta una reinstalación que
  reutiliza la misma etiqueta con una clave real distinta, el escenario más peligroso (descifrado con la
  clave equivocada sin que nada lo señale de antemano más allá del fallo de autenticación de GCM en tiempo de
  uso, no en tiempo de restore).
- **Rotación de clave automática en v1**: rechazada — añade complejidad real (key ring, recifrado masivo) sin
  necesidad actual; se documenta como extensión futura explícita.

## Consecuencias
- Cualquier tabla futura con secretos de integración (Drive, WhatsApp si algún día requiere credenciales)
  reutiliza `EncryptionContext`/`encrypt_secret`/`decrypt_secret` tal cual, con su propio `token_type`.
- Las 4 pruebas de fingerprint (mismo key_id+clave / key_id distinto / mismo key_id+clave distinta /
  manifiesto sin fingerprint) son parte obligatoria de la suite de este bloque.
