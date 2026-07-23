# ADR-0045: Modelo de estado de honorarios (estado persistido + historiales separados)

## Estado
Aceptado

## Contexto
Un cargo de honorarios (`professional_fee_charges`) necesita reflejar tanto su ciclo de vida documental (emitido o no) como su situación de cobro (pendiente, parcial, pagado). Un diseño temprano incluía `OVERDUE` como valor persistido de `status`, lo cual dependía del paso del tiempo — sin un job programado que lo recalculara periódicamente, esa columna quedaría desactualizada indefinidamente (un cargo vencido ayer seguiría mostrando el estado de "pago pendiente" hasta la próxima escritura). Además, se necesita distinguir el historial del **cargo como documento** del historial de **cada pago individual** contra ese cargo — son dos ciclos de vida distintos.

## Decisión
`professional_fee_charges.status` (`app/models/professional_fee.py`) tiene exactamente 6 valores, **sin `OVERDUE`**: `PENDING_ISSUANCE → ISSUED|PAYMENT_PENDING → PARTIALLY_PAID → PAID`, más `CANCELLED` como terminal. `issue_charge` decide entre `ISSUED` (sin `due_date`, sin seguimiento de cobranza exigible) y `PAYMENT_PENDING` (con `due_date`, entra de inmediato al seguimiento); los pagos/anulaciones posteriores recalculan entre `PAYMENT_PENDING`/`PARTIALLY_PAID`/`PAID` vía la función pura `professional_fee_charge_status.compute_charge_payment_status`.

El **riesgo de cobranza vencido** (`OVERDUE`/`DUE_TODAY`/`ON_TRACK`) se calcula en vivo, nunca persistido: `professional_fee_risk.compute_fee_collection_risk(due_date, status, balance, today)`, con su expresión SQL equivalente (`fee_collection_risk_sql_expression`) usada en el tablero para evitar N+1. Una prueba de paridad (`test_professional_fee_risk_sql_parity.py`) confirma que ambas formas coinciden en los 5 valores posibles.

Además, se mantienen **dos tablas de historial separadas**: `professional_fee_charge_status_history` (ciclo de vida del cargo como documento: `PENDING_ISSUANCE → ISSUED → ...`) y `professional_fee_payment_history` (nacimiento/anulación de cada pago individual, `NULL → ACTIVE → VOIDED`) — no se colapsan en una sola tabla porque describen conceptos distintos (un cargo puede tener varios pagos, cada uno con su propio ciclo de vida de anulación).

## Alternativas consideradas
- **Persistir `OVERDUE` en `status`**: rechazado explícitamente — requeriría un job periódico para mantenerlo actualizado, algo fuera del alcance de esta fase y una fuente de inconsistencia si el job falla o no corre.
- **Una sola tabla de historial para cargo y pagos**: rechazado — mezclaría dos ciclos de vida independientes (el cargo puede seguir `ISSUED` mientras un pago individual se anula y otro se mantiene activo).

## Consecuencias
- `test_professional_fee_risk_sql_parity.py::test_fee_collection_risk_changes_with_time_without_altering_persisted_status` prueba, con `frozen_clock`, que el riesgo calculado cambia al avanzar el reloj mientras el `status` persistido permanece igual.
- Cualquier vista futura que necesite "cargos vencidos" debe usar `compute_fee_collection_risk`/su expresión SQL, nunca añadir un valor `OVERDUE` a la columna persistida.
