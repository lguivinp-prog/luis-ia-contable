# ADR-0058: Ciclo de vida de tareas operativas y concurrencia (bloqueo de fila + versión optimista)

## Estado
Aceptado

## Contexto
El repositorio ya tiene dos patrones de concurrencia consolidados: bloqueo de fila exacta con
`populate_existing=True` (`lock_exact_active_file`, Bloque 5 — encontró y corrigió un bug real de
identity-map caching sin esa opción) y versión optimista por fila (`Declaration.row_version`). Las tareas
operativas combinan ambos, y además necesitan un orden determinista adicional porque, a diferencia de los
bloques anteriores, la sincronización automática (ADR-0059) puede mutar muchas filas en una sola ejecución
concurrente con otra ejecución equivalente.

## Decisión

### 4 estados sin reapertura
`PENDING`/`IN_PROGRESS`/`COMPLETED`/`CANCELLED`. `COMPLETED` y `CANCELLED` son terminales — sin reapertura, sin
reactivación; si el trabajo debe repetirse, se crea una tarea nueva. Se descartaron `BLOCKED`/`DEFERRED`: un
bloqueo por documentos ya es visible vía el `document_status` de la declaración vinculada, no necesita su
propio estado de tarea.

### Bloqueo de fila exacta + versión optimista para toda acción humana
`lock_exact_task` (`SELECT ... FOR UPDATE .execution_options(populate_existing=True)`) seguido de comprobar
`expected_version == task.version` bajo el bloqueo. Toda respuesta de una acción mutante devuelve la `version`
resultante — el cliente siempre puede reintentar con el valor correcto tras un 409.

### La sincronización incrementa `version` sin `expected_version`
La resolución/realineación automática (ADR-0059) también bloquea la fila exacta y también incrementa
`version`, pero **nunca** recibe ni compara un `expected_version` — no hay un cliente humano esperando una
versión concreta. Esto es intencional: es exactamente lo que hace que una acción humana concurrente basada en
una versión anterior reciba `409 TASK_VERSION_CONFLICT` en vez de sobrescribir silenciosamente el resultado de
la sincronización (verificado por pruebas de concurrencia real de dos conexiones, ver Sección 16 del plan).

### Orden determinista de bloqueo para evitar interbloqueos entre sincronizaciones concurrentes
Dos sincronizaciones concurrentes pueden interbloquearse de dos formas distintas si no se ordenan: (1) al
bloquear filas existentes en órdenes opuestos, y (2) al insertar varias `generation_key` nuevas en el índice
único parcial (`uq_operational_tasks_tenant_generation_key`, `WHERE generation_key IS NOT NULL`) cuando cada
ejecución las descubre en un orden distinto. La sincronización resuelve ambas en fases
estrictas nunca intercaladas: primero descubre y ordena **todos** los candidatos nuevos por `generation_key
ASC` antes de insertar (uno por uno, vía `ON CONFLICT DO NOTHING RETURNING`, que nunca aborta la transacción
por conflicto — ver ADR-0059), y solo después obtiene, deduplica y ordena por `id ASC` las tareas existentes
que requieren mutación, bloqueándolas en ese orden exacto. Verificado con pruebas reales de dos conexiones
(`threading.Event`, `Thread.join(timeout=...)`) para ambas fases.

### Rechazo de mutaciones humanas sin cambio real (`409 TASK_NO_CHANGES`)
Comprobado **después** de `expected_version` (nunca antes): un `PATCH` cuyos campos enviados coinciden todos
con los valores actuales, un `reassign` al mismo `assigned_to` (incluido `NULL → NULL`), o un `reschedule` sin
cambio de `due_at`/`due_has_time`/`starts_at` ni activación nueva de `due_is_overridden`, se rechaza sin
incrementar `version`, sin fila de historial, sin auditoría. Única excepción: la **primera** reprogramación de
una tarea generada que activa `due_is_overridden` de `false` a `true` siempre cuenta como cambio real, incluso
si el `due_at` enviado coincide casualmente con `source_due_at` — el cambio real ahí es la marca de
override, no la fecha. Los comentarios nunca son un no-op: cada `POST .../comments` exitoso siempre crea una
fila append-only.

## Alternativas consideradas
- **Reapertura de tareas terminales**: rechazada — mismo principio que declaraciones/honorarios: un estado
  terminal representa un hecho consumado; el trabajo repetido es una tarea nueva, con su propio historial.
- **`expected_version` también para la sincronización**: rechazada — no existe un cliente humano con una
  versión esperada; forzarlo habría requerido que la sincronización re-consultara la versión antes de cada
  mutación, sin aportar ninguna garantía adicional sobre el bloqueo de fila + orden determinista ya presente.
- **Aceptar mutaciones sin cambio real silenciosamente**: rechazada tras detectar que generaría ruido de
  auditoría/historial sin valor informativo, y ocultaría a un usuario que su intento de cambio no tuvo efecto.

## Consecuencias
- Todo endpoint mutante de tarea sigue el mismo flujo de 7 pasos: bloquear, comprobar versión, aplicar cambio
  de dominio, incrementar versión, registrar historial + auditoría, `flush()`, `commit()` del router.
- 11 pruebas de concurrencia real con dos conexiones cubren: completar-vs-completar, completar-vs-cancelar,
  reasignar-vs-completar, reprogramar-vs-reprogramar, reasignar-vs-reprogramar, comentario-vs-reasignación
  (ambos órdenes), realineación-vs-reprogramación humana, resolución automática-vs-acción manual, e inserción/
  actualización concurrente sin interbloqueo durante la sincronización.
