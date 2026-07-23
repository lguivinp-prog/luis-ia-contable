# ADR-0039: La corrección solo escribe historial de estado si el valor derivado cambió

## Estado
Aceptado

## Contexto
Una corrección de determinación tributaria (`tax_assessment_service.correct_assessment`) recalcula `tax_payment_status` a partir de los nuevos montos. Si esa corrección no cambia el estado derivado (p. ej. sigue `PAYMENT_PENDING` antes y después), escribir de todas formas una fila de `declaration_status_history` con `from_value == to_value` ensuciaría el historial con "cambios" que no ocurrieron realmente, dificultando su lectura.

## Decisión
`tax_assessment_service._recalculate_tax_payment_status` (y su equivalente en `tax_payment_service`) solo escribe una fila de `declaration_status_history` cuando `new_status != from_status`. Si el estado derivado no cambia, la función retorna sin escribir nada — la corrección en sí (fila de `tax_assessments` + `tax_assessment_history`) siempre se registra, pero el historial de `tax_payment_status` solo refleja cambios reales.

## Alternativas consideradas
- **Registrar siempre una fila, aunque `from_value == to_value`**: rechazado explícitamente en la revisión del plan — genera ruido en el historial que dificulta distinguir "esto realmente cambió" de "se recalculó y dio lo mismo".

## Consecuencias
- `test_tax_assessments.py::test_correction_that_does_not_change_derived_status_writes_no_extra_history` prueba explícitamente que el conteo de filas de `declaration_status_history` para `status_field='TAX_PAYMENT'` no aumenta cuando la corrección no cambia el estado derivado.
- Cualquier recálculo de estado derivado futuro (impuestos, honorarios, documentos) debe seguir el mismo patrón: comparar antes de escribir, nunca escribir incondicionalmente.
