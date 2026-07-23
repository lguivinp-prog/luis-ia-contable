# ADR-0035: Sincronización documental idempotente (individual y masiva)

## Estado
Aceptado

## Contexto
`declaration_document_items` no puede materializarse en el momento en que se asigna un requisito a un cliente (la declaración concreta puede no existir todavía), ni tampoco vía un trigger de base de datos (dificulta el control de errores y la auditoría). Además, las declaraciones creadas en la subentrega 3.1 (antes de que existiera control documental) necesitan poder incorporar requisitos documentales después, sin una migración destructiva de backfill.

## Decisión
`declaration_document_service.sync_requirements_for_declaration` (`app/services/declaration_document_service.py`) es la única vía de materialización, y es **idempotente por diseño**:

1. Recupera el periodo tributario de la declaración y exige que esté abierto (`tax_period_service.ensure_period_open`).
2. Calcula una fecha de referencia (mismo criterio que `tax_schedule_resolver.resolve_reference_date`).
3. Consulta `client_document_requirements` vigentes para `(tenant_id, client_id)` cuyo rango de vigencia (`validity_range`, columna generada) contiene la fecha de referencia, y cuyo `client_obligation_id` es `NULL` o coincide con el de la declaración.
4. Para cada requisito: si ya existe una fila en `declaration_document_items` con `(tenant_id, declaration_id, client_document_requirement_id)` (protegido además por un `UNIQUE` real), se cuenta como "existente" y no se toca; si no existe, se crea con `status='NOT_REQUESTED'`.
5. **Nunca elimina ni desactiva** un ítem existente, aunque la asignación que lo originó luego cambie o se desactive — el ítem histórico se conserva intacto.
6. Recalcula `document_status` sobre el conjunto completo (nuevos + existentes) y registra auditoría (`DOCUMENT_REQUIREMENTS_SYNC`).

Se invoca en tres contextos:
- **Automáticamente** al crear una declaración (`declaration_service.create_declaration(..., sync_documents=True)`, activo por defecto), incluida la generación masiva (que ya envuelve cada declaración en un `SAVEPOINT`).
- **Individualmente** vía `POST /api/v1/declarations/{id}/documents/sync-requirements` (botón "Sincronizar requisitos" en el frontend).
- **Masivamente por periodo** vía `POST /api/v1/tax-periods/{id}/documents/sync-requirements` (`routers/declaration_documents.py`), que itera todas las declaraciones del periodo con un `SAVEPOINT` por declaración — pensado explícitamente para declaraciones preexistentes de 3.1 que nunca pasaron por control documental. Este es el procedimiento administrativo no destructivo documentado en `USER_MANUAL.md`.

## Alternativas consideradas
- **Backfill de migración**: rechazado — una migración no puede decidir de forma segura qué declaraciones existentes deberían recibir qué requisitos (la asignación pudo cambiar desde entonces), y mezclar lógica de negocio en una migración de esquema complica el rollback.
- **Trigger de base de datos**: rechazado — dificulta la auditoría explícita y el manejo de errores por fila, y esconde la lógica de negocio fuera de la capa de servicio.

## Consecuencias
- `test_declaration_documents.py` prueba: llamar la sincronización dos veces seguidas no duplica ítems; los ítems históricos se conservan tras desactivar una asignación; la sincronización masiva por periodo cubre declaraciones "preexistentes".
- Cualquier nuevo tipo de requisito documental debe seguir el mismo patrón de materialización explícita, nunca automática vía trigger.
