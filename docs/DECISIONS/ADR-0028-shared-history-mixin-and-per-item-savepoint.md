# ADR-0028: Mixin común de historial append-only y clasificación persistida por ítem en generación masiva

## Estado
Aceptado

## Contexto
`declarations` y `tax_periods` acumulan varias tablas de historial append-only (estado de flujo, vencimiento, asignación, presentación, reapertura, estado de periodo) que comparten exactamente las mismas columnas de auditoría: quién hizo el cambio, cuándo, por qué, con qué origen, con qué metadata adicional, correlacionado con qué operación. Duplicar estas columnas en cada tabla de historial sería repetitivo y una fuente de inconsistencias si una tabla nueva olvidara alguna. Por separado, la generación masiva (ver ADR-0021) necesita persistir la clasificación completa de cada ítem analizado (se crearía / ya existe / error) para que el checksum de staleness y la pantalla de revisión trabajen siempre sobre datos reales, nunca re-resueltos en silencio.

## Decisión
**Mixin común**: `DeclarationHistoryMixin` (`app/db/base.py`) centraliza las columnas comunes a toda tabla de historial append-only: `tenant_id`, `actor_user_id` (opcional), `created_at`, `reason` (opcional), `origin` (`MANUAL`/`GENERATION`/`RECALCULATION`/`SYSTEM`), `history_metadata` (JSONB), `audit_correlation_id`. Cada tabla concreta que lo usa (`DeclarationStatusHistory`, `DeclarationDueDateHistory`, `DeclarationAssignmentHistory`, `FilingHistory`, `DeclarationReopenHistory`, `TaxPeriodStatusHistory`, y desde Bloque 3.2 todas las nuevas tablas `*_history`) debe declarar su propio `__table_args__` con las FKs compuestas específicas de esa tabla (`actor_user_id, tenant_id) → users(id, tenant_id)`, etc.) — SQLAlchemy no combina el `__table_args__` de un mixin con el de la subclase, la subclase lo sobreescribe completamente, así que cada tabla concreta repite esa FK explícitamente en su propio `__table_args__`.

**Clasificación persistida por ítem con `SAVEPOINT`**: `DeclarationGenerationRunItem` (ver ADR-0021) persiste la clasificación completa de cada combinación cliente/obligación analizada durante el preview. Durante `confirm()`, cada ítem se procesa dentro de su propio `db.begin_nested()` (`SAVEPOINT`): si `create_declaration` falla para ese ítem puntual, el `SAVEPOINT` se revierte (`savepoint.rollback()`) sin afectar los ítems ya confirmados ni los pendientes — el flujo completo es preview → clasificación persistida por ítem → confirmación con prevalidación global de staleness → `SAVEPOINT` de inserción por ítem.

## Alternativas consideradas
- **Duplicar las columnas de auditoría en cada tabla de historial**: rechazado — riesgo real de que una tabla nueva olvide un campo (por ejemplo, `audit_correlation_id`), rompiendo la correlación de auditoría de forma silenciosa.
- **Una única transacción para todo el lote de generación, sin `SAVEPOINT` por ítem**: rechazado — ver ADR-0021, un ítem con error abortaría el lote completo.

## Consecuencias
- Toda tabla de historial nueva (Bloque 3.2: `tax_assessment_history`, `tax_payment_history`, `document_item_status_history`, `professional_fee_agreement_history`, `professional_fee_charge_status_history`, `professional_fee_payment_history`) hereda de `DeclarationHistoryMixin` y declara su propio `__table_args__` con las FKs compuestas específicas — nunca omite este paso confiando en que el mixin lo resuelva automáticamente.
- `test_declaration_generation.py` cubre que un ítem con error durante `confirm()` no revierte los demás ítems del mismo run.

## Compatibilidad
Bloque 3.2 reutiliza `DeclarationHistoryMixin` sin modificarlo. La sincronización administrativa masiva de requisitos documentales por periodo (`POST /api/v1/tax-periods/{id}/documents/sync-requirements`) reutiliza el mismo patrón de `SAVEPOINT` por declaración que la generación masiva, por la misma razón de aislamiento de fallos.

## Referencias
- Mixin: `app/db/base.py` (`DeclarationHistoryMixin`)
- Modelo: `app/models/declaration_generation.py` (`DeclarationGenerationRunItem`)
- Servicio: `app/services/declaration_generation_service.py` (`confirm`, uso de `db.begin_nested()`)
- Pruebas: `backend/tests/integration/test_declaration_generation.py`
