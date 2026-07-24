# ADR-0025: Integridad multitenant extendida retroactivamente a tablas de Bloque 1/2

## Estado
Aceptado

## Contexto
Las FKs compuestas de `declarations` (ver ADR-0024) necesitan que `clients`, `client_obligations`, `users` y las tablas de `tax_schedule_*` (`tax_schedule_sources`, `tax_schedule_versions`, `tax_schedule_entries`, `tax_schedule_exceptions`) expongan una restricción `UNIQUE` que incluya `tenant_id` (y, en el caso de `client_obligations`, también `client_id`) — no bastaba con su `UNIQUE(id)` original de Bloque 1/2, porque Postgres exige que el lado referenciado de una FK compuesta sea único exactamente sobre esas columnas.

## Decisión
La migración `0006_declarations_core.py` agrega, **antes** de crear `declarations`, las restricciones auxiliares necesarias sobre tablas ya existentes de Bloque 1/2:

- `uq_clients_id_tenant`: `UNIQUE(id, tenant_id)` sobre `clients`.
- `uq_client_obligations_id_tenant_client`: `UNIQUE(id, tenant_id, client_id)` sobre `client_obligations`.
- `uq_tax_schedule_sources_id_tenant`: `UNIQUE(id, tenant_id)` sobre `tax_schedule_sources`.
- `uq_tax_schedule_versions_id_tenant_source`: `UNIQUE(id, tenant_id, source_id)` sobre `tax_schedule_versions`.
- `uq_tax_schedule_entries_id_tenant_version` / `uq_tax_schedule_exceptions_id_tenant_version`: `UNIQUE(id, tenant_id, version_id)` sobre `tax_schedule_entries`/`tax_schedule_exceptions`.

`users` ya había recibido su `UNIQUE(id, tenant_id)` en la migración `0005_tax_periods.py`, porque `tax_period_status_history.actor_user_id` la necesitaba primero — la migración `0006` no la repite, solo agrega las 6 restantes.

Ninguna de estas restricciones auxiliares cambia el comportamiento de las tablas de Bloque 1/2 para su propio dominio: siguen funcionando exactamente igual para contribuyentes, obligaciones y cronogramas. Su único propósito es habilitar que `declarations` (y, desde Bloque 3.2, `declaration_document_items`, `professional_fee_agreements`, etc.) puedan anclar sus FKs compuestas de forma real, no solo declarativa.

## Alternativas consideradas
- **Rediseñar `clients`/`users`/`tax_schedule_*` para incluir `tenant_id` en su clave primaria** en vez de agregar un `UNIQUE` auxiliar: rechazado — habría requerido migrar todas las FKs existentes de Bloque 1/2 que ya referencian `id` solo, un cambio mucho más invasivo para un beneficio equivalente.
- **No agregar estas restricciones y usar FKs simples en `declarations`**: rechazado — ver ADR-0024, es exactamente el riesgo que las FKs compuestas existen para eliminar.

## Consecuencias
- `test_declarations_multitenant_integrity.py` verifica que estas restricciones auxiliares existen y que las FKs compuestas de `declarations` (ADR-0024) las usan correctamente para rechazar cruces de tenant.
- Cualquier tabla de Bloque 1/2 que en el futuro necesite ser referenciada por una FK compuesta desde una tabla nueva debe seguir el mismo patrón: agregar el `UNIQUE(id, tenant_id[, ...])` auxiliar en la migración que introduce la tabla que la referencia, nunca modificar la tabla antigua fuera de ese contexto.

## Compatibilidad
Bloque 3.2 sigue el mismo patrón para sus propias tablas nuevas cuando son referenciadas por otras tablas de 3.2 (por ejemplo, `uq_declarations_id_tenant_client` sobre `declarations`, migración `0008_document_control.py`, para que `declaration_document_items` pueda anclarse también por `client_id` — ver ADR-0036).

## Referencias
- Migraciones: `backend/migrations/versions/0005_tax_periods.py`, `backend/migrations/versions/0006_declarations_core.py`
- Modelos afectados: `app/models/client.py`, `app/models/obligation.py`, `app/models/tax_schedule.py`, `app/models/user.py`
- Pruebas: `backend/tests/integration/test_declarations_multitenant_integrity.py`
