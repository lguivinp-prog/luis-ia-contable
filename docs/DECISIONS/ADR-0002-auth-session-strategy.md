# ADR-0002: Cookie de sesión server-side en lugar de JWT

## Estado
Aceptado

## Contexto
Se necesita un mecanismo de autenticación para el MVP local-first de un único usuario administrador, con posibilidad de evolucionar a multiusuario en el futuro (Fase 6).

## Decisión
Se usa un token opaco de sesión (`secrets.token_urlsafe(32)`) entregado en una cookie `HttpOnly`, `SameSite=Strict`, `Secure` activable por entorno. En la base de datos (`sessions`) solo se guarda el hash SHA-256 del token, nunca el valor crudo.

## Alternativas consideradas
- **JWT autofirmado**: rechazado. Su ventaja principal (validación stateless entre múltiples instancias/servicios) no aplica aquí: hay una sola instancia de backend y un único usuario. Revocar un JWT antes de su expiración requiere una lista de revocación, lo que termina siendo tan complejo como una tabla de sesiones pero con más superficie de error (confusión de algoritmo, gestión de `SECRET_KEY`, tamaño de payload, necesidad de refresh tokens con rotación).
- El problema típico que JWT resuelve (cookies cross-origin) se evita aquí con `next.config.js` → `rewrites()`, que hace que todas las llamadas del frontend al backend sean same-origin.

## Consecuencias
- Es trivial auditar y revocar sesiones activas (`revoked_at` en `sessions`), correlacionado con `audit_logs`.
- Si en el futuro se necesita escalar a múltiples instancias de backend, la tabla `sessions` sigue funcionando (es la fuente de verdad compartida vía la base de datos), sin cambios de arquitectura.
