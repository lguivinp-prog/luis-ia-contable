# ADR-0034: `client_obligation_id` reemplaza `obligation_id` en requisitos documentales

## Estado
Aceptado

## Contexto
Un requisito documental asignado a un cliente puede ser general (aplica siempre) o estar atado a una obligación tributaria concreta que el cliente tiene asignada. Una versión temprana del diseño enlazaba el requisito directamente al catálogo global de obligaciones (`obligation_catalog.id`), lo cual no distingue "esta obligación en particular que este cliente tiene asignada" de "el tipo de obligación en general" — dos clientes con la misma obligación de catálogo pero vigencias distintas no podrían tener requisitos independientes correctamente vinculados.

## Decisión
`client_document_requirements.client_obligation_id` (`app/models/declaration_document.py`) referencia `client_obligations.id` (la asignación real y concreta), no `obligation_catalog.id`:

- FK compuesta `(client_obligation_id, tenant_id, client_id) → client_obligations(id, tenant_id, client_id)` — reutiliza la unicidad auxiliar ya existente de `client_obligations`, garantizando que la obligación enlazada pertenece al **mismo cliente**, no solo al mismo tenant.
- `client_obligation_id IS NULL` significa **requisito general del cliente**, no atado a ninguna obligación asignada específica.
- La sincronización (`declaration_document_service.sync_requirements_for_declaration`) materializa un requisito si `client_obligation_id IS NULL` (general) **o** si coincide exactamente con el `client_obligation_id` de la declaración.
- El `EXCLUDE` de solapamiento de vigencias (`ex_client_document_requirements_no_overlap`) incluye `COALESCE(client_obligation_id::text, '')` como parte de la clave de comparación, para que un requisito general y uno específico de la misma obligación no colisionen entre sí incorrectamente.

## Alternativas consideradas
- **Enlazar a `obligation_catalog.id` (catálogo global)**: rechazada explícitamente en la revisión del plan — no permite distinguir la obligación concreta asignada al cliente, y no protege contra que el requisito quede "huérfano" si la obligación de catálogo aplica a un cliente distinto.

## Consecuencias
- `test_declaration_documents.py::test_sync_requirements_matches_general_and_obligation_specific` prueba ambos casos (general y vinculado) sincronizando correctamente.
- Cualquier requisito futuro que necesite vincularse a una entidad más específica que una obligación (p. ej. una categoría tributaria) debe seguir el mismo patrón: FK compuesta hacia la fila concreta del cliente, nunca hacia un catálogo global.
