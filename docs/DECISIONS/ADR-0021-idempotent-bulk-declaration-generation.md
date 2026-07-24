# ADR-0021: Generación masiva idempotente de declaraciones

## Estado
Aceptado

## Contexto
Generar declaraciones masivamente para todos los clientes con una obligación activa en un periodo tributario es una operación de alto impacto: si se ejecuta dos veces por error, no debe duplicar declaraciones; si los datos subyacentes (clientes, obligaciones asignadas, cronograma aprobado) cambian entre que un usuario revisa una vista previa y confirma, la confirmación no debe generar declaraciones sobre datos obsoletos sin que el usuario lo sepa; y si un ítem individual falla durante la confirmación (una violación de restricción, un dato inconsistente), el resto del lote no debe perderse.

## Decisión
`declaration_generation_runs` (`app/models/declaration_generation.py`) modela el flujo completo como una máquina de tres fases:

1. **`preview()`**: analiza clientes/obligaciones activas del periodo, clasifica cada combinación posible (se crearía / ya existe / error) y **persiste esa clasificación completa** en `declaration_generation_run_items`, junto con un `preview_checksum` calculado sobre el contenido exacto de esos ítems (`_compute_checksum`). El run queda en estado `PENDING_CONFIRMATION` (o equivalente).
2. **`confirm()`**: antes de crear cualquier declaración, recalcula el checksum sobre el estado actual de los datos y lo compara contra `preview_checksum`. Si difieren, el run pasa a `STALE` **sin crear ninguna declaración** — el usuario debe pedir un `refresh_preview()` explícito y confirmar de nuevo. Si coinciden, cada ítem se procesa dentro de su propio `SAVEPOINT` (`db.begin_nested()`): si `create_declaration` falla para un ítem puntual, ese `SAVEPOINT` se revierte (`savepoint.rollback()`) y el run continúa con el resto — un ítem fallido nunca aborta el lote completo.
3. **`refresh_preview()`**: recalcula la clasificación y el checksum desde cero para un run `STALE`, permitiendo reintentar la confirmación sobre datos actuales.

La fase de **prevalidación global** ocurre íntegramente antes de abrir el primer `SAVEPOINT` de inserción — el chequeo de staleness es atómico respecto al resto del proceso, no se intercala ítem por ítem.

## Alternativas consideradas
- **Confirmar directamente sin persistir la clasificación del preview**: rechazado — obligaría a volver a resolver todo en el momento de confirmar, sin poder detectar de forma confiable qué cambió exactamente respecto a lo que el usuario realmente vio y aprobó (ver ADR-0027, contenido determinista del snapshot).
- **Una sola transacción para todo el lote, sin `SAVEPOINT` por ítem**: rechazado — un solo ítem con datos inconsistentes revertiría el lote completo, obligando a reintentar generación para clientes que sí eran válidos.
- **Bloquear la confirmación completa ante cualquier cambio de datos, sin distinguir STALE de error**: el diseño elegido sí distingue: staleness detectado por checksum (todo el run se marca `STALE`, nada se crea) es distinto de un error puntual de un ítem durante `confirm()` (ese ítem se revierte, el resto continúa).

## Consecuencias
- `test_declaration_generation.py` cubre: preview → confirm exitoso; preview → cambio de datos → confirm detecta `STALE` sin crear declaraciones; `refresh_preview` sobre un run `STALE`; un ítem con error durante `confirm()` no aborta los demás ítems del run.
- La sincronización administrativa masiva de requisitos documentales por periodo (Bloque 3.2, `POST /api/v1/tax-periods/{id}/documents/sync-requirements`) reutiliza el mismo patrón de `SAVEPOINT` por declaración, por la misma razón: un fallo puntual no debe bloquear el resto del periodo.

## Compatibilidad
Sin cambios de comportamiento en Bloque 3.2. `declaration_document_service.sync_requirements_for_declaration` se invoca automáticamente dentro de la misma transacción de `create_declaration` cuando `sync_documents=True` (default), pero eso no altera la mecánica de `SAVEPOINT`/staleness descrita aquí.

## Referencias
- Modelo: `app/models/declaration_generation.py`
- Servicio: `app/services/declaration_generation_service.py`
- Router: `app/routers/declaration_generation.py`
- Pruebas: `backend/tests/integration/test_declaration_generation.py`
