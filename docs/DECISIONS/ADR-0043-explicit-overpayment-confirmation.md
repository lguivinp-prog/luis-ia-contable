# ADR-0043: El sobrepago requiere confirmación explícita y motivo

## Estado
Aceptado

## Contexto
Un pago (tributario u honorario) puede exceder el saldo pendiente por error del cliente/depositante o de forma deliberada (adelanto). El sistema no debe inferir automáticamente que un exceso es "correcto" ni rechazarlo sin más — ambos casos son legítimos y deben quedar registrados de forma distinguible y auditable.

## Decisión
`tax_payment_service.create_payment`/`professional_fee_payment_service.create_payment` calculan el exceso (`total_pagado_previo + monto_nuevo - total_payable_o_amount`) **antes** de escribir nada:

- Si el exceso es `<= 0`: pago normal, columnas de sobrepago quedan en `NULL`/`false`.
- Si el exceso es `> 0`:
  - Si `overpayment_confirmed` no viene en `true` en el payload: `OverpaymentConfirmationRequiredError` (409) — nada se persiste, el frontend debe pedir confirmación explícita al usuario.
  - Si viene `true` pero sin `overpayment_reason` (o vacío): `MissingOverpaymentReasonError` (422).
  - Si ambos están presentes: se persiste `overpayment_confirmed=true`, `overpayment_amount=<exceso calculado>`, `overpayment_reason`, `confirmed_by=<usuario actual>`, `confirmed_at=<ahora>`.
- Los `CHECK` de `tax_payments`/`professional_fee_payments` exigen que estas cuatro columnas viajen juntas (`overpayment_confirmed=false` o las cuatro no-nulas) — la base de datos también rechaza un estado incoherente, no solo el servicio.
- Una corrección posterior que reduce el impuesto **nunca modifica pagos anteriores**; el exceso resultante queda visible como dato calculado (comparando el pago histórico contra la nueva determinación vigente), no se re-marca retroactivamente como sobrepago.

## Alternativas consideradas
- **Rechazar cualquier pago que exceda el saldo**: rechazado — un adelanto legítimo del cliente es un caso de uso real que debe poder registrarse.
- **Aceptar el exceso silenciosamente sin marca**: rechazado — perdería la trazabilidad de que hubo un sobrepago y por qué, dificultando conciliaciones futuras.

## Consecuencias
- `test_tax_payments.py::test_overpayment_requires_confirmation`/`test_fee_overpayment_requires_confirmation_and_reason` prueban las tres ramas (rechazo sin confirmar, rechazo sin motivo, éxito con ambos).
- El frontend (`TaxPaymentsPanel.tsx`, `clients/[id]/fees/page.tsx`) detecta el 409 específico de sobrepago y revela un checkbox + campo de motivo para reintentar con los mismos datos más la confirmación.
