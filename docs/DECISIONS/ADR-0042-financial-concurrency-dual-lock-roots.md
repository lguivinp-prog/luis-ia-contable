# ADR-0042: Concurrencia financiera con `FOR UPDATE` y dos raíces de bloqueo independientes

## Estado
Aceptado

## Contexto
Determinar/corregir/pagar impuesto y pagar/anular honorarios son operaciones financieras que leen un saldo, lo validan y escriben — un patrón clásico de carrera si dos solicitudes concurrentes leen el mismo saldo antes de que la primera escriba. La subentrega 3.1 usaba únicamente bloqueo optimista (`row_version`) para el ciclo de vida general de la declaración, insuficiente para proteger sumas de dinero bajo concurrencia real.

## Decisión
Las operaciones financieras de 3.2 usan bloqueo **pesimista** real (`SELECT ... FOR UPDATE`), con **dos raíces de bloqueo completamente independientes**:

- **Impuestos**: `declaration_repository.lock_for_update` bloquea la fila de `declarations` — usado por `tax_assessment_service`/`tax_payment_service`.
- **Honorarios**: `professional_fee_repository.lock_charge_for_update` bloquea la fila de `professional_fee_charges` — usado por `professional_fee_payment_service`. **Nunca** bloquea `declarations`.

La secuencia exacta dentro de cada operación: (1) `FOR UPDATE` sobre la raíz correspondiente (esto participa en la transacción ya abierta por el router); (2) releer el estado vigente bajo el bloqueo; (3) validar (incluida idempotencia y sobrepago); (4) escribir la fila principal + historial + auditoría (`flush`); (5) recalcular el estado derivado; (6) el bloqueo se libera recién con el `commit()` único del router al final del endpoint.

## Alternativas consideradas
- **Bloqueo optimista (`row_version`) también para pagos**: rechazado para las escrituras financieras — bajo una carrera real, el segundo intento fallaría con un conflicto de versión y requeriría reintento manual del usuario, en vez de serializarse transparentemente como hace `FOR UPDATE`.
- **Una única raíz de bloqueo compartida entre impuestos y honorarios**: rechazado explícitamente — mezclar las dos raíces obligaría a bloquear `declarations` para operaciones de honorarios que no tienen relación necesaria con una declaración concreta, y rompería la independencia de ambos dominios (ver `docs/DATA_MODEL.md`, sección de independencia impuestos/honorarios).

## Consecuencias
- `test_tax_payment_concurrency.py::test_two_real_connections_serialize_on_for_update_lock` es una prueba de concurrencia **real** (dos conexiones/hilos independientes, sincronizados con `threading.Event`, nunca solo `sleep`) que confirma que el segundo pago espera realmente a que el primero libere el bloqueo, y que la suma final refleja ambos pagos sin pérdida.
- `professional_fee_payment_service` no importa `declaration_service` ni el modelo `Declaration` — verificado explícitamente por `test_professional_fees.py::test_fee_payment_service_never_imports_declaration_service`.
