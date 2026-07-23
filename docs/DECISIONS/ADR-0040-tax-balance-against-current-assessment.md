# ADR-0040: El saldo tributario siempre se calcula contra la determinación vigente

## Estado
Aceptado

## Contexto
`tax_payments.assessment_id` guarda qué determinación estaba vigente **en el momento en que se registró el pago** (una referencia histórica útil para mostrar "con qué determinación se pagó esto"). Existe el riesgo de diseñar por error el cálculo de saldo filtrando los pagos por ese `assessment_id` puntual — lo cual sería incorrecto tras una corrección, porque los pagos ya hechos siguen siendo válidos contra la nueva determinación vigente, no contra la que existía cuando se pagaron.

## Decisión
El saldo (`tax_payment_status`, y el saldo mostrado en el detalle de la declaración) se calcula **siempre** sumando **todos** los pagos válidos (no anulados) de la declaración —`tax_payment_repository.sum_valid_amount_for_declaration`, filtrado únicamente por `declaration_id`, nunca por `assessment_id`— y comparando ese total contra `total_payable`/`remaining_credit` de la determinación **actualmente vigente** (`is_current=true`). `assessment_id` en `tax_payments` es puramente informativo/histórico y nunca participa en el cálculo de saldo.

Esto aplica simétricamente en `tax_assessment_service.correct_assessment` (recalcula el saldo tras una corrección) y en `tax_payment_service.create_payment`/`void_payment` (recalcula tras cada pago/anulación).

## Alternativas consideradas
- **Filtrar pagos por `assessment_id` de la determinación vigente**: rechazado — dejaría "huérfanos" (no contabilizados) todos los pagos hechos contra determinaciones anteriores tras cualquier corrección, mostrando un saldo pendiente incorrecto aunque el cliente ya hubiera pagado.

## Consecuencias
- `test_tax_assessments.py::test_correction_generating_overpayment_excess_reflected_in_status` prueba explícitamente que un pago hecho contra la determinación original sigue contando tras una corrección que reduce el impuesto.
- Cualquier reporte o vista futura que muestre "pagos de esta declaración" debe agregar por `declaration_id`, nunca filtrar por un `assessment_id` específico al calcular totales/saldos.
