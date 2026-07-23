# ADR-0038: Determinación tributaria versionada (`sequence_number` + `is_current`)

## Estado
Aceptado

## Contexto
Una declaración puede determinarse una vez y luego corregirse cero o más veces (nunca se edita una determinación en el lugar, para preservar el historial completo de cuánto se determinó y cuándo). Se necesita una forma de saber, en todo momento, cuál es la determinación vigente sin ambigüedad, y de rechazar a nivel de base de datos cualquier duplicado de secuencia.

## Decisión
`tax_assessments` (`app/models/tax_assessment.py`, migración `0009_tax_assessments_payments.py`):

- `assessment_type VARCHAR(20) CHECK IN ('ORIGINAL', 'CORRECTION')`, `sequence_number SMALLINT CHECK (>= 1)`.
- `CHECK ((assessment_type = 'ORIGINAL' AND sequence_number = 1) OR (assessment_type = 'CORRECTION' AND sequence_number > 1))` — coherencia entre tipo y secuencia, verificada por la base, no solo por el servicio.
- `UNIQUE(tenant_id, declaration_id, sequence_number)` — rechaza duplicados de secuencia por declaración.
- `is_current BOOLEAN` + índice único parcial `ux_tax_assessments_one_current_per_declaration` sobre `(tenant_id, declaration_id) WHERE is_current` — garantiza que nunca haya más de una fila vigente por declaración, a nivel de base de datos.
- Los montos (`gross_tax_amount`, `credits_applied`, `total_payable`, `remaining_credit`) tienen un `CHECK` que fija la fórmula exacta: `total_payable = GREATEST(gross - credits, 0)` y `remaining_credit = GREATEST(credits - gross, 0)` — nunca aceptados directamente del frontend, siempre recalculados en `tax_assessment_service` con `Decimal`.
- `tax_assessment_history` conserva el par completo `previous_assessment_snapshot`/`new_assessment_snapshot` (JSONB), con `CHECK` de que ningún snapshot quede vacío cuando corresponde.

La determinación inicial (`tax_assessment_service.create_initial_assessment`) escribe, en una sola invocación dentro de la transacción del router: la fila de `tax_assessments`, su `tax_assessment_history` (`previous=NULL`), una fila de `declaration_status_history` (`NOT_DETERMINED → <derivado>`), y recalcula `declarations.tax_payment_status`.

## Alternativas consideradas
- **Editar la fila de determinación vigente en el lugar**: rechazado — pierde el historial de qué se determinó originalmente antes de cada corrección, dificultando auditorías.
- **Aceptar `total_payable`/`remaining_credit` desde el payload**: rechazado explícitamente — el cliente nunca debe poder inventar el saldo; siempre se deriva server-side.

## Consecuencias
- `test_tax_assessments.py` cubre: creación inicial (4 filas en una transacción), corrección que aumenta/reduce/genera exceso, rechazo de una segunda determinación inicial, y el `UNIQUE(sequence_number)` rechazando duplicados vía inserción directa.
- La fórmula de montos usando `Decimal` (nunca `float`) es obligatoria en cualquier cálculo tributario futuro para evitar errores de redondeo.
