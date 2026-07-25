# ADR-0059: Generación automática idempotente de tareas (3 reglas + resolución con precedencia)

## Estado
Aceptado

## Contexto
No existe ningún scheduler 24/7 en este repositorio (sin Celery, sin APScheduler, sin cron interno) y este
bloque no lo introduce — debe funcionar local-first sin servidor encendido a medianoche. Una PC apagada varios
días no debe perder tareas: la generación siempre materializa contra el estado *actual* de la fuente en el
momento en que se ejecuta, sin importar cuánto tiempo pasó desde la última ejecución.

## Decisión

### `ON CONFLICT DO NOTHING RETURNING`, nunca capturar `IntegrityError`
Un `INSERT` que viola una restricción única y lanza `IntegrityError` deja la transacción PostgreSQL abortada
hasta un `ROLLBACK` explícito — capturar esa excepción dentro de la transacción abierta del router habría
inutilizado la sesión para el resto de la ejecución. En su lugar, cada candidato se inserta vía
`INSERT ... ON CONFLICT (tenant_id, generation_key) WHERE generation_key IS NOT NULL DO NOTHING RETURNING
id`: nunca lanza excepción ante el conflicto. El predicado `WHERE` del `ON CONFLICT` (`index_where` en
SQLAlchemy) coincide literalmente con el del índice único parcial real
`uq_operational_tasks_tenant_generation_key` — sin ese predicado, PostgreSQL no podría inferir el índice
parcial como árbitro del conflicto. `id` devuelto ⇒ `created` (se escribe la fila `CREATED` de historial con
ese id real); `None` ⇒ `already_exists`.

### 3 reglas hardcodeadas como funciones puras, sin tabla de configuración
`DECLARATION_FILING`, `DOCUMENT_COLLECTION`, `FEE_COLLECTION` — mismo idioma que `declaration_risk.py`
(funciones puras, sin persistencia de reglas). `DOCUMENT_COLLECTION` genera para
`status IN ('REQUESTED','PARTIAL','OBSERVED')` de un ítem cuya declaración no esté en estado terminal:
`OBSERVED` se incluye junto a `REQUESTED`/`PARTIAL` porque el mapeo de resolución automática ya mantiene
`OBSERVED` como estado activo (paso 6) — una primera sincronización que encuentra un ítem ya `OBSERVED` debe
generar la tarea igual, sin exigir que haya pasado antes por `REQUESTED`/`PARTIAL`.

### Resolución automática con mapeo exhaustivo y precedencia explícita
Cada regla tiene un orden de precedencia documentado sobre los valores reales de los enums del dominio (nunca
un recálculo paralelo de riesgo). Para `DOCUMENT_COLLECTION`, el cumplimiento del ítem se evalúa **primero**,
antes que el estado terminal de la declaración padre: un ítem ya `RECEIVED`/`ACCEPTED` se considera trabajo
completado incluso si la declaración se cerró/canceló después. Para `FEE_COLLECTION`, la resolución se basa
únicamente en los 2 estados terminales reales del cargo (`PAID`/`CANCELLED`) — nunca en un recálculo paralelo
de `professional_fee_risk.compute_fee_collection_risk` (que determina la condición de *generación*, no de
*resolución*).

### Realineación de vencimiento vs. resolución — distinción explícita
Cuando la fuente cambia pero sigue requiriendo trabajo, se actualiza `source_due_at` (y `due_at`, salvo que
`due_is_overridden=true`) sin tocar el estado de la tarea. Solo cuando la fuente deja de requerir trabajo se
aplica resolución automática (`COMPLETED`/`CANCELLED`). Nunca se cancela/completa una tarea solo porque cambió
su vencimiento.

### Disparadores explícitos vía `POST`, incluido `DASHBOARD_OPEN`
`TaskSyncTrigger` acepta `MANUAL`/`LOGIN`/`AGENDA_OPEN`/`DASHBOARD_OPEN` — nunca `CLI`, excluido por diseño de
tipo (`Literal`). Si la sincronización automática al abrir agenda/dashboard falla, la vista igual se muestra
con lo ya existente más un botón de reintento manual.

### El CLI es inalcanzable por HTTP y explícitamente multitenant
`backend/app/cli/sync_operational_tasks.py` exige `--tenant-id` — nunca elige un tenant por defecto ni
sincroniza todos los tenants. Invoca directamente el mismo servicio compartido
(`task_generation_service.sync_operational_tasks`) que usa `POST /tasks/sync`, con `trigger_source="CLI"`,
`triggered_by=None` — cero lógica de sincronización duplicada entre el CLI y el router HTTP (ver ADR-0058
para el límite transaccional propio del CLI).

## Alternativas consideradas
- **Reglas de generación configurables en base de datos**: rechazadas — sin precedente de reglas de negocio
  configurables en todo el repositorio; 3 reglas hardcodeadas como funciones puras siguen el mismo idioma que
  `declaration_risk.py`/`professional_fee_risk.py`.
- **Capturar `IntegrityError` de la unicidad de `generation_key`**: rechazada por el riesgo de abortar la
  transacción exterior completa — ver Decisión.
- **Generación con límite inferior de vencimiento** (no generar declaraciones ya muy vencidas): rechazada —
  rompería la garantía de "PC apagada varios días no pierde la tarea al encenderla".

## Consecuencias
- Orden global determinista de 12 fases (ver ADR-0058) obligatorio tanto para `POST /tasks/sync` como para el
  CLI — ambos invocan la misma función de servicio.
- Pruebas parametrizadas recorren los 7 valores de `DocumentItemStatus` y los 6 de
  `ProfessionalFeeChargeStatus`, más las combinaciones relevantes de `filing_status`/`workflow_status`.
