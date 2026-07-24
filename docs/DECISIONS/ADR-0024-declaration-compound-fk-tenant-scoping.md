# ADR-0024: FKs compuestas de `declarations` hacia sus propias referencias, ancladas por tenant

## Estado
Aceptado

## Contexto
`declarations` referencia varias entidades (`clients`, `client_obligations`, `tax_periods`, `users`, `tax_schedule_versions`/`entries`/`exceptions`). Una FK simple por `id` (por ejemplo, `client_id → clients.id`) no impide que, mediante una inserción SQL/ORM directa que rodee la capa de servicio, una declaración del tenant A termine apuntando a un cliente del tenant B — Postgres no tiene forma de saberlo con una FK de una sola columna.

## Decisión
Todas las FKs de `declarations` hacia otras tablas propias del dominio son **compuestas**, incluyendo siempre `tenant_id` como parte de la clave referenciada:

- `fk_declarations_client_tenant`: `(client_id, tenant_id) → clients(id, tenant_id)`
- `fk_declarations_obligation_tenant_client`: `(client_obligation_id, tenant_id, client_id) → client_obligations(id, tenant_id, client_id)` — anclada además por `client_id`, para que la obligación asignada realmente pertenezca al mismo cliente de la declaración, no solo al mismo tenant.
- `fk_declarations_period_tenant`: `(tax_period_id, tenant_id) → tax_periods(id, tenant_id)`
- `fk_declarations_assigned_user_tenant` / `fk_declarations_closed_by_tenant`: `(assigned_user_id, tenant_id)` / `(closed_by, tenant_id) → users(id, tenant_id)`
- `fk_declarations_schedule_version_tenant_source`, `fk_declarations_schedule_entry_tenant_version`, `fk_declarations_schedule_exception_tenant_version`: cada una ancla la referencia al cronograma tanto por `tenant_id` como por su padre inmediato (`source_id`/`version_id`), para que una declaración no pueda referenciar una entrada o excepción de una versión de cronograma distinta a la que declara usar.

Cada una de estas FKs compuestas depende de que la tabla referenciada tenga una restricción `UNIQUE` que incluya exactamente esas mismas columnas (`(id, tenant_id)` o `(id, tenant_id, client_id)`) — Postgres exige que el lado referenciado de una FK compuesta sea único. Como `clients`, `client_obligations`, `users` y las tablas de `tax_schedule_*` son tablas de Bloque 1/2 que originalmente solo tenían `UNIQUE(id)`, esas restricciones auxiliares tuvieron que agregarse retroactivamente (ver ADR-0025).

## Alternativas consideradas
- **FKs simples por `id`, con la validación de tenant solo en el servicio**: rechazado — no protege contra una inserción SQL/ORM directa, exactamente el escenario que la subentrega 3.1 identificó como riesgo real tras la segunda ronda de corrección del plan.
- **Un único `CHECK` de aplicación (trigger) en vez de FKs compuestas**: rechazado — un trigger es una capa adicional a mantener y más difícil de razonar que una FK declarativa estándar de Postgres, que además documenta la relación directamente en el esquema.

## Consecuencias
- `test_declarations_multitenant_integrity.py` cubre 10 casos de inserción SQL/ORM directa que intentan mezclar tenants o clientes distintos a través de cada una de estas FKs compuestas, confirmando que todas son rechazadas con `IntegrityError`.
- El mismo patrón (FK compuesta anclada por `tenant_id`, y por `client_id` cuando corresponde) se reutiliza en todas las tablas nuevas de Bloque 3.2: `declaration_document_items`, `tax_assessments`, `tax_payments`, `professional_fee_charges`, etc.

## Compatibilidad
Bloque 3.2 extiende el mismo principio con una FK compuesta adicional sobre `declarations` (`uq_declarations_id_tenant_client`, migración `0008_document_control.py`) para que `declaration_document_items` pueda anclarse también por `client_id` — ver `ADR-0036-cross-client-document-integrity.md`.

## Referencias
- Modelo: `app/models/declaration.py` (`__table_args__`, todas las `ForeignKeyConstraint`)
- Migración: `backend/migrations/versions/0006_declarations_core.py`
- Pruebas: `backend/tests/integration/test_declarations_multitenant_integrity.py`
