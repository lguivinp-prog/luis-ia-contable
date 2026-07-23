# ADR-0033: Catálogo de requisitos documentales híbrido (sistema/tenant)

## Estado
Aceptado

## Contexto
El control documental (Bloque 3.2) necesita un catálogo de tipos de documento (constancias, comprobantes, planillas, etc.) reutilizable entre clientes de un mismo tenant, más un conjunto reducido de tipos "de sistema" compartidos por todos los tenants (p. ej. documentos genéricos que cualquier contador pediría). No existía antes de 3.2.

## Decisión
`document_requirement_catalog` (`app/models/declaration_document.py`, migración `0008_document_control.py`) es una tabla híbrida:

- `is_system BOOLEAN NOT NULL DEFAULT false` + `tenant_id UUID NULL` con `CHECK ((is_system = true AND tenant_id IS NULL) OR (is_system = false AND tenant_id IS NOT NULL))`.
- `tenant_id` tiene una **FK real** hacia `tenants.id` (no compuesta — Postgres exime automáticamente las filas con `tenant_id NULL` de la verificación de FK, que es exactamente el comportamiento deseado para filas de sistema).
- Unicidad de `code` separada por alcance: `UNIQUE(code) WHERE tenant_id IS NULL` (sistema) y `UNIQUE(tenant_id, code) WHERE tenant_id IS NOT NULL` (por tenant), vía índices únicos parciales.
- Las filas `is_system=true` **nunca** se crean desde un endpoint normal de tenant — solo por semilla o una acción administrativa fuera de este flujo (`document_requirement_catalog_service.create_custom_catalog_entry` siempre fuerza `is_system=False`).
- Como la FK hacia el catálogo desde `client_document_requirements.requirement_catalog_id` es **simple** (no compuesta, por la limitación real de Postgres con filas `tenant_id NULL`), la validación de que el catálogo usado pertenece al tenant correcto o es de sistema se hace en `document_requirement_catalog_service.validate_catalog_scope` — si falla, `ForeignDocumentCatalogEntryError` (403), sin persistir nada.

## Alternativas consideradas
- **FK compuesta `(id, tenant_id)`**: rechazada porque una fila de sistema con `tenant_id NULL` haría que la FK compuesta nunca matchee ninguna fila real hija con un `tenant_id` concreto.
- **Duplicar cada tipo de sistema por tenant**: rechazada por ser redundante y complicar la actualización de un tipo de sistema (habría que propagarla a N tenants).

## Consecuencias
- `test_professional_fees.py`/`test_declaration_documents.py` cubren la creación de catálogo personalizado y el rechazo cross-tenant.
- Cualquier acción administrativa futura para poblar el catálogo de sistema debe hacerse fuera de los routers normales (semilla o script), nunca vía `POST /api/v1/document-requirement-catalog`.
