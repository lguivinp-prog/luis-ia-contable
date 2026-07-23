# ADR-0046: Regla de importe agregado en recibos de honorarios

## Estado
Aceptado

## Contexto
Un recibo (`professional_receipts`) puede emitirse de forma parcial contra un cargo de honorarios (varios recibos pequeños en vez de uno solo por el monto total). Esta es una regla **agregada** (depende de la suma de varias filas existentes) que no puede expresarse como un `CHECK` de una sola fila en Postgres — requiere validación explícita en el servicio antes de insertar.

## Decisión
`professional_fee_receipt_service.issue_receipt` calcula la suma de recibos `ISSUED` ya existentes para el cargo (`professional_fee_repository.sum_issued_receipts_for_charge`) y valida que `suma_existente + nuevo_monto <= professional_fee_charges.amount` **antes** de insertar. Si se excede: `ReceiptExceedsChargeAmountError` (HTTP 422), nada se persiste. Un recibo anulado (`status='VOIDED'`) nunca se elimina — su número queda reservado permanentemente vía `UNIQUE(tenant_id, receipt_type, number)`, y no se puede reemitir el mismo número aunque el original esté anulado.

`professional_receipts` también exige `issued_by UUID NOT NULL` (FK compuesta hacia `users`) y un `CHECK` de coherencia: `status='ISSUED'` implica los tres campos de anulación en `NULL`; `status='VOIDED'` implica los tres en `NOT NULL`.

## Alternativas consideradas
- **Exigir que cada recibo cubra el monto completo del cargo**: rechazado — no refleja la práctica real de facturación parcial.
- **Permitir recibos sin límite agregado**: rechazado — permitiría sobre-emitir documentalmente más de lo que el cargo realmente vale, rompiendo la coherencia contable.

## Consecuencias
- `test_professional_fees.py::test_receipt_partial_allowed_sum_cannot_exceed_charge_amount` prueba ambos límites: un recibo parcial es válido, un segundo que excede el remanente es rechazado.
- `test_professional_fees.py::test_receipt_void_and_duplicate_number_rejected` prueba que un número de recibo anulado no puede reutilizarse.
