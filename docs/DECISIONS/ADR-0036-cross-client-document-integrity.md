# ADR-0036: Integridad de mismo cliente entre declaración e ítem documental

## Estado
Aceptado

## Contexto
Un `declaration_document_item` referencia tanto una `declaration_id` como un `client_document_requirement_id`. Ambas FKs, si solo incluyeran `tenant_id`, dejarían una brecha real: nada impediría, a nivel de base de datos, insertar una fila cuya declaración pertenece al cliente A pero cuyo requisito pertenece al cliente B del **mismo tenant** — un cruce entre clientes que rompería la confidencialidad de datos entre contribuyentes de la misma firma contable.

## Decisión
`declaration_document_items` (`app/models/declaration_document.py`) tiene una columna `client_id` propia (no derivada), usada **literalmente igual** en dos FKs compuestas:

- `(declaration_id, tenant_id, client_id) → declarations(id, tenant_id, client_id)`
- `(client_document_requirement_id, tenant_id, client_id) → client_document_requirements(id, tenant_id, client_id)`

Como el mismo valor de `client_id` de la fila debe satisfacer **ambas** verificaciones simultáneamente, Postgres estructuralmente no puede aceptar una fila cuya declaración y cuyo requisito pertenezcan a clientes distintos, aunque ambos sean del mismo tenant. Esto requirió agregar una nueva auxiliar `UNIQUE(id, tenant_id, client_id)` sobre `declarations` (migración `0008`, `ALTER TABLE` sobre la tabla ya existente de 3.1) y sobre `client_document_requirements`.

## Alternativas consideradas
- **Validación solo en servicio** (comparar `declaration.client_id == requirement.client_id` en Python antes de insertar): rechazada como única defensa — es una defensa en profundidad válida pero no evita una inserción directa (migración manual, script administrativo, bug futuro) que la pase por alto. La restricción de base de datos es la garantía real.
- **FKs compuestas solo con `tenant_id`** (como en la mayoría de tablas de 3.1/3.2): insuficiente aquí porque `declaration_document_items` es la única tabla que referencia **dos** padres que a su vez pertenecen ambos a un cliente — el riesgo de cruce solo existe cuando hay dos referencias "hijas de un cliente" en la misma fila.

## Consecuencias
- Prueba SQL/ORM directa dedicada (`test_declaration_documents.py::test_declaration_document_item_rejects_cross_client_mix`): inserta directamente una fila con `declaration_id` del cliente A y `client_document_requirement_id` del cliente B, y confirma `IntegrityError`.
- Este patrón (columna "puente" + dos FKs compuestas idénticas en esa columna) es el que debe reutilizarse en cualquier tabla futura que referencie dos entidades hijas de un mismo cliente en la misma fila.
