# ADR-0065: Excepción documentada a ADR-0037 para llamadas externas lentas (patrón preparar → llamar → persistir)

## Estado
Aceptado

## Contexto
ADR-0037 establece que ningún servicio de dominio llama `db.commit()`/`db.rollback()` — el router controla
toda la transacción de un endpoint. Esa regla asume implícitamente que todo el trabajo de un endpoint es
rápido y local (PostgreSQL). El callback OAuth de Google rompe esa asunción: entre "el `state` se validó" y
"la conexión queda persistida" hay una llamada HTTP externa lenta (intercambio de código + `userinfo`) que
nunca debe ejecutarse mientras se sostiene abierta una transacción de escritura sobre
`google_oauth_authorization_attempts`/`google_oauth_connections`.

## Decisión
`google_oauth_service.process_callback` es la única función de este bloque que gestiona sus propias
transacciones cortas, en 3 fases explícitas:

1. **Preparar**: bloquear el intento (`SELECT ... FOR UPDATE`), validar, descifrar el `code_verifier` en
   memoria, purgar la fila (`PENDING → PROCESSING`), `commit()` — transacción corta que se cierra de
   inmediato.
2. **Llamar al proveedor**: `exchange_code` + `get_user_info`, completamente **fuera** de cualquier
   transacción de base de datos abierta.
3. **Persistir resultado**: nueva transacción que valida la cuenta (mismatch/refresh token), persiste la
   conexión y transiciona el intento a `SUCCEEDED`/`FAILED`, con su propio `commit()`.

El router (`app/routers/google_integrations.py::callback`) **nunca** vuelve a llamar `db.commit()` después de
invocar este servicio — la función ya deja la base de datos en su estado final correcto por sí misma. Todo el
resto de los servicios de este bloque (`start_authorization`, `disconnect`,
`disconnect_for_user_deactivation`) siguen el patrón estándar de ADR-0037 sin excepción: no hacen ninguna
llamada HTTP externa mientras sostienen una transacción de escritura (la revocación de mejor esfuerzo en
`disconnect` ocurre explícitamente **antes** de bloquear la fila para actualizarla).

## Alternativas consideradas
- **Generalizar la excepción a todo servicio que use un adaptador externo**: rechazada — solo el callback OAuth
  tiene el problema real (una operación de "preparar" que debe sobrevivir entre dos peticiones HTTP distintas
  del navegador); el resto de operaciones de este bloque caben en una sola transacción sin necesidad de romper
  ADR-0037.
- **Mantener una única transacción larga durante todo el callback**: rechazada explícitamente por el plan v3 —
  sostener un `SELECT ... FOR UPDATE` mientras se espera una llamada HTTP a Google bloquearía cualquier otro
  intento de ese mismo usuario y arriesga timeouts de conexión de base de datos bajo latencia real de red.

## Consecuencias
- Cualquier bloque futuro que necesite el mismo patrón (por ejemplo, la sincronización de Calendar en el
  Bloque 3.2, o la exportación a Drive en el 3.3) debe citar este ADR explícitamente, no asumir la excepción
  por generalización automática.
- Las pruebas de este bloque verifican explícitamente que un fallo en cualquiera de las 3 fases deja la base
  de datos en un estado coherente y recuperable (el intento queda `FAILED` con el `error_code` correcto, nunca
  en un estado intermedio ambiguo).
