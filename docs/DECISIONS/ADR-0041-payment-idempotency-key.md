# ADR-0041: Idempotencia de pagos mediante `idempotency_key`

## Estado
Aceptado

## Contexto
Un reenvío de red (doble clic, timeout con reintento automático, doble pestaña) podría generar dos filas de pago idénticas para la misma operación real, inflando el saldo pagado. El bloqueo `SELECT ... FOR UPDATE` protege contra **carreras de concurrencia** (dos usuarios pagando al mismo tiempo), pero no contra un **reenvío deliberado o accidental de la misma solicitud** — ambos escenarios pasarían el bloqueo secuencialmente sin conflicto aparente.

## Decisión
Tanto `tax_payments` como `professional_fee_payments` tienen una columna `idempotency_key UUID NOT NULL` generada por el **frontend** (`crypto.randomUUID()`) al abrir el formulario de pago, con `UNIQUE(tenant_id, idempotency_key)`:

- El frontend genera la clave una sola vez por intento de pago y la reutiliza en reintentos de la misma solicitud (nunca la regenera hasta que el pago se confirma exitosamente o el usuario decide explícitamente empezar un pago nuevo).
- El servicio (`tax_payment_service.create_payment` / `professional_fee_payment_service.create_payment`) busca primero si ya existe un pago con esa `(tenant_id, idempotency_key)`:
  - Si existe y el payload relevante coincide (`declaration_id`/`charge_id`, `amount`, `paid_at`, `payment_method`, `assessment_id` según el dominio): devuelve el pago existente sin duplicar (HTTP 201, mismo resultado).
  - Si existe pero el payload difiere: `IdempotencyKeyConflictError` (HTTP 409) — indica una colisión de clave con datos distintos, nunca se sobrescribe silenciosamente.
- Esta verificación es **independiente** del bloqueo `FOR UPDATE`: el bloqueo evita que dos pagos concurrentes lean el mismo saldo obsoleto; la clave de idempotencia evita que el mismo pago se registre dos veces.

## Alternativas consideradas
- **Deduplicar por `(declaration_id, amount, paid_at)`**: rechazado — dos pagos legítimos y distintos podrían coincidir accidentalmente en esos tres campos (p. ej. dos cuotas iguales el mismo día).
- **Confiar solo en el bloqueo `FOR UPDATE`**: insuficiente — un reenvío secuencial (no concurrente) del mismo formulario pasaría el bloqueo dos veces sin conflicto.

## Consecuencias
- `test_tax_payments.py`/`test_professional_fees.py` prueban: reenvío idéntico devuelve el mismo pago sin duplicar; mismo `idempotency_key` con payload distinto devuelve conflicto.
- El frontend (`TaxPaymentsPanel.tsx`, `clients/[id]/fees/page.tsx`) genera la clave con `crypto.randomUUID()` al construir el formulario y solo la renueva tras un envío exitoso.
