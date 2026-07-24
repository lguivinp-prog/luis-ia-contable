# ADR-0017: Semántica de 3 campos y snapshot histórico versionado del vencimiento

## Estado
Aceptado

## Contexto
El vencimiento de una declaración cambia con el tiempo: una prórroga administrativa, una corrección del cronograma tributario, o una nueva versión aprobada del motor de cronogramas pueden mover la fecha efectiva. Si `declarations` guardara un único campo `due_date` mutable, se perdería la capacidad de responder "¿cuál era la fecha de vencimiento original, antes de cualquier recálculo?" — una pregunta con valor legal y de auditoría real (una prórroga posterior no debe borrar el rastro del vencimiento original).

## Decisión
`declarations` mantiene **3 campos de vencimiento sin ambigüedad de significado**, nunca uno solo:

- **`initial_effective_due_date`**: fijado una única vez en la creación de la declaración; **nunca cambia** después, sin importar cuántos recálculos ocurran.
- **`current_original_due_date`**: el vencimiento que resultaría de la regla de cronograma vigente, sin considerar prórrogas manuales.
- **`current_effective_due_date`**: el vencimiento real aplicable hoy, después de cualquier prórroga u override administrativo.

Cada vez que el vencimiento se recalcula (nueva versión de cronograma aprobada, corrección administrativa), se escribe una fila en `declaration_due_date_history` (`app/models/declaration.py`) con el **par completo** anterior/nuevo — nunca una sola columna compartida entre el estado "antes" y "después". Esto incluye tanto los campos de fecha como la referencia completa a la regla de cronograma que los produjo (`from_version_id`/`from_source_id`/`from_rule_type`/`from_entry_id`/`from_exception_id` y su equivalente `to_*`), de modo que el historial explica **por qué** cambió el vencimiento, no solo que cambió. La generación inicial de la declaración también escribe una fila en este historial, con el par `from_*` completo en `NULL` (no hay "antes" cuando se crea por primera vez).

`ck_declaration_due_date_history_rule_type` exige que el par `from_rule_type`/`from_entry_id`/`from_exception_id` esté completamente `NULL` o completamente coherente (una regla `ENTRY` con `entry_id` y sin `exception_id`, o viceversa) — igual que la coherencia exigida en la propia `declarations` (ver `ck_declarations_rule_type_coherence`).

## Alternativas consideradas
- **Un único campo `due_date` mutable**: rechazado — pierde la fecha de vencimiento original en cuanto ocurre el primer recálculo, imposibilitando auditar prórrogas.
- **Historial con una sola columna de fecha (`from_date`/`to_date`) sin la referencia completa a la regla de cronograma**: rechazado — no explica la causa del cambio (¿fue una prórroga manual? ¿una nueva versión aprobada del cronograma?), reduciendo el historial a una bitácora de fechas sin contexto.

## Consecuencias
- `declaration_service.create_declaration` escribe la primera fila de `declaration_due_date_history` como parte de las 5 tablas de historial inicial (junto a `DeclarationStatusHistory`, `DeclarationAssignmentHistory`, `FilingHistory`, `DeclarationReopenHistory`).
- Cualquier función de recálculo de vencimiento debe escribir el par completo anterior/nuevo en esta tabla antes de actualizar `current_original_due_date`/`current_effective_due_date` en la fila viva de `declarations` — nunca actualizar la fila viva sin dejar rastro histórico.
- La tabla comparte `DeclarationHistoryMixin` (`app/db/base.py`, ver ADR-0028) con el resto de historiales append-only de `declarations` y `tax_periods`.

## Compatibilidad
Sin cambios en Bloque 3.2. Las nuevas tablas de 3.2 (`tax_assessment_history`, `tax_payment_history`, `professional_fee_agreement_history`, etc.) siguen el mismo principio de historial append-only con el par completo anterior/nuevo, reutilizando el mismo mixin.

## Referencias
- Modelo: `app/models/declaration.py` (clase `DeclarationDueDateHistory`)
- Mixin compartido: `app/db/base.py` (`DeclarationHistoryMixin`, ver ADR-0028)
- Servicio: `app/services/declaration_service.py`, `app/services/declaration_snapshot_service.py`
- Migración: `backend/migrations/versions/0006_declarations_core.py`
- Pruebas: `backend/tests/integration/test_declaration_state_machine.py`, `backend/tests/unit/test_declaration_snapshot.py`
