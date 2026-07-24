# ADR-0018: Enums de dominio de `Declaration` y riesgo calculado en vivo, nunca persistido

## Estado
Aceptado

## Contexto
Una declaración tiene varios ejes de estado independientes (progreso operativo interno, presentación ante la autoridad, situación tributaria, situación documental) que no deben confundirse en una sola columna, y un "nivel de riesgo" (crítico, próximo a vencer, en curso, etc.) que depende del tiempo transcurrido hasta el vencimiento — un valor que cambiaría solo si alguien ejecutara un job programado para mantenerlo actualizado, algo que este proyecto no tiene ni necesita.

## Decisión
`app/models/declaration.py` define 4 enums de dominio **independientes entre sí**, cada uno con su propio campo de estado:

- `WorkflowStatus`: progreso operativo interno (`PENDING` → ... → `CLOSED`, con salidas a `NOT_APPLICABLE`/`CANCELLED`).
- `FilingStatus`: presentación ante la autoridad (`NOT_FILED`/`READY`/`FILED`/`FAILED`/`NOT_REQUIRED`).
- `TaxPaymentStatus`: situación tributaria (`NOT_DETERMINED`/`NO_TAX_DUE`/`CREDIT_BALANCE`/`PAYMENT_PENDING`/`PARTIALLY_PAID`/`PAID`/`NOT_APPLICABLE`).
- `DocumentStatus`: situación documental (`NOT_CONFIGURED`/`PENDING`/`PARTIAL`/`OBSERVED`/`COMPLETE`/`NOT_APPLICABLE`).

En la subentrega 3.1, `tax_payment_status` y `document_status` no tenían máquina de transiciones propia: sus tablas de origen (`tax_assessments`, `declaration_document_items`) no existían todavía, así que toda declaración aplicable nacía y permanecía en `NOT_DETERMINED`/`NOT_CONFIGURED` hasta que Bloque 3.2 los activara.

**`computed_risk_status` nunca es una columna de `declarations`**: es una función pura (`app/services/declaration_risk.py::compute_risk_status`) que recibe el vencimiento vigente y los 4 estados como parámetros y devuelve el riesgo en el momento de la consulta. Esto evita que el riesgo quede desactualizado por el simple paso del tiempo sin que nadie lo recalculara explícitamente. La misma lógica existe como expresión SQL equivalente (para el tablero, evitando N+1 al consultar muchas declaraciones), y una prueba de paridad (`test_declaration_risk_python_matches_sql`) garantiza que ambas formas coinciden siempre.

En Bloque 3.2 (Dominio E), la función se extiende con los parámetros `document_status`/`tax_payment_status`/`closed_with_pending` — ahora que esos campos pueden alcanzar valores reales gracias a los Dominios A–C — sin cambiar el principio: sigue siendo una función pura, sin acceso a base de datos, nunca persistida.

## Alternativas consideradas
- **Un único campo de "estado general"** que combine progreso, presentación, impuesto y documentos: rechazado — impediría, por ejemplo, cerrar por presentación completa mientras el impuesto sigue pendiente, o razonar sobre cada eje independientemente en el tablero.
- **Persistir `computed_risk_status` como columna, actualizada por un trigger o job**: rechazado — añade una fuente de desincronización (el job no corrió, el trigger falló) para un valor que es trivial de calcular al vuelo; el patrón elegido (`tax_payment_status.py`, `professional_fee_charge_status.py` siguen el mismo principio) es deliberadamente consistente en todo el proyecto.

## Consecuencias
- `test_declaration_risk.py` (antes `test_declaration_risk_python_matches_sql`) cubre la paridad Python/SQL y, desde 3.2, la interacción de `document_status`/`tax_payment_status`/`closed_with_pending` con el riesgo.
- `test_declaration_state_machine.py` cubre las transiciones de `WorkflowStatus`/`FilingStatus` (ver ADR-0022 para la mecánica de la máquina de estados en sí).
- Cualquier estado derivado nuevo (riesgo de cobranza de honorarios, `tax_payment_status` derivado) debe seguir el mismo patrón: función pura, sin acceso a base de datos, replicada en SQL solo cuando el tablero lo necesite para evitar N+1.

## Compatibilidad
Bloque 3.2 activa `tax_payment_status`/`document_status` (antes solo alcanzables en sus valores iniciales) y extiende `compute_risk_status` de forma aditiva — ninguna declaración de 3.1 cambia de comportamiento retroactivamente; simplemente ahora sus campos pueden tomar valores adicionales conforme se determina impuesto o se sincronizan documentos.

## Referencias
- Modelo: `app/models/declaration.py` (enums `WorkflowStatus`, `FilingStatus`, `TaxPaymentStatus`, `DocumentStatus`)
- Servicio: `app/services/declaration_risk.py`
- Pruebas: `backend/tests/integration/test_declaration_risk.py`, `backend/tests/integration/test_declaration_state_machine.py`
