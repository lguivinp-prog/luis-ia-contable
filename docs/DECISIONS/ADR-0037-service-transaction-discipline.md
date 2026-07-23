# ADR-0037: Disciplina transaccional de servicios de dominio (nunca commit/rollback)

## Estado
Aceptado

## Contexto
En la subentrega 3.1, `declaration_service.create_declaration` originalmente ejecutaba `db.rollback()` internamente ante ciertos errores, lo cual causaba que el router perdiera el control real del límite transaccional de su propio endpoint (un rollback interno del servicio puede deshacer cambios que el router asumía como ya asentados en la misma transacción, y complica componer varias llamadas de servicio en una sola operación atómica). Bloque 3.2 generaliza la corrección a **todos** los servicios de dominio nuevos.

## Decisión
Ningún servicio de dominio (`declaration_document_service`, `document_requirement_catalog_service`, `tax_assessment_service`, `tax_payment_service`, `professional_fee_service`, `professional_fee_payment_service`, `professional_fee_receipt_service`) ejecuta `db.commit()` ni `db.rollback()`:

- Usan `db.flush()` cuando necesitan un ID generado o cuando quieren que una violación de restricción (`IntegrityError`) se manifieste de inmediato, para poder traducirla a una excepción de dominio propia (p. ej. `AgreementOverlapError`, `ChargeAlreadyExistsError`).
- El **router** (o la función de aplicación que lo invoca) es quien controla el límite transaccional real: hace `db.commit()` una sola vez si todo salió bien, o `db.rollback()` en el bloque `except` correspondiente — exactamente el mismo patrón ya usado por todos los routers de 3.1 (`tax_periods.py`, `declarations.py`).
- Para operaciones financieras, esto significa que el `SELECT ... FOR UPDATE` inicial, todas las escrituras (determinación/pago/historial/auditoría) y el recálculo de estado derivado ocurren dentro de la **misma** transacción abierta por el router, que solo se cierra (liberando el bloqueo) con el `commit()` final del endpoint.

## Alternativas consideradas
- **Que cada servicio controle su propia transacción** (como estaba antes de la corrección de 3.1): rechazado explícitamente — impide componer varias escrituras de distintos servicios en una sola operación atómica, y un rollback interno de un servicio puede revertir datos que el llamador no esperaba perder.

## Consecuencias
- Prueba de atomicidad dedicada por dominio financiero: forzar (vía `monkeypatch`) un fallo al escribir el historial o la auditoría **después** de haber hecho `db.add()`/`db.flush()` del pago — el rollback del router debe dejar cero filas nuevas persistidas (`test_tax_payments.py::test_atomic_rollback_if_history_write_fails_after_payment_insert`).
- Cualquier servicio de dominio nuevo debe seguir esta misma regla; se verifica por inspección de código y por la prueba de rollback atómico correspondiente.
