# ADR-0013: Categorías tributarias del cliente desacopladas de `tax_regime`

## Estado
Aceptado (revisado tras varias rondas de corrección durante la aprobación del plan)

## Contexto
`clients.tax_regime` (NRUS/RER/RMT/GENERAL) es un concepto cerrado y estable del Bloque 1. El motor de cronogramas necesita, además, códigos de categoría abiertos y con vigencia histórica (p. ej. `BUEN_CONTRIBUYENTE`, `ENTIDAD_PUBLICA`, categorías especiales futuras) que no deben mezclarse con `tax_regime` ni forzar cambios de esquema cada vez que aparezca una categoría nueva.

## Decisión
Se añaden `tax_category_catalog` (catálogo abierto: código, nombre, descripción, activo) y `client_tax_categories` (asignación cliente↔categoría con vigencia `valid_from`/`valid_until`), en el mismo patrón que `obligation_catalog`/`client_obligations` del Bloque 1. `tax_regime` no se toca.

### Vigencia histórica sin bloquear renovaciones
Una restricción `UNIQUE(client_id, category_code)` habría impedido registrar una renovación (el cliente pierde y luego recupera la misma categoría en otra fecha). En su lugar se usa una columna generada `validity_range daterange` (`daterange(valid_from, valid_until, '[]')`, `GENERATED ALWAYS ... STORED`) y una restricción `EXCLUDE USING gist` (requiere la extensión `btree_gist`) sobre `(client_id, category_code, validity_range)` con operador `&&`: impide vigencias solapadas del mismo cliente+categoría, mientras permite cuantas renovaciones no solapadas sean necesarias.

### `clients.is_good_taxpayer` queda congelada (legacy)
La columna booleana `is_good_taxpayer` del Bloque 1 **nunca** se lee ni se escribe desde la lógica de negocio nueva. `ClientOut.is_good_taxpayer` se calcula **dinámicamente en cada lectura** desde `client_tax_categories` (código `BUEN_CONTRIBUYENTE`), evaluado contra la fecha de referencia "hoy" (`date.today()`), con una consulta batched (`compute_good_taxpayer_map`) para listados — sin N+1. `ClientCreate`/`ClientUpdate` usan `ConfigDict(extra="forbid")`, de modo que enviar `is_good_taxpayer` en el body produce un 422 explícito en vez de ser ignorado en silencio.

### Fecha de referencia para vigencia de categorías (evaluación de cronogramas)
`resolve_reference_date(tax_period)` (ver ADR-0011/ADR-0015) usa el **último día calendario** del periodo tributario como fecha de referencia (`"2026-01"` → `2026-01-31`). Esto es una **convención del sistema**, documentada como tal en `docs/TAX_RULES_MODEL.md` — no una regla oficial de SUNAT — y revisable si aparece una fuente oficial distinta.

## Consecuencias
- `test_client_tax_category_validity.py` cubre la restricción `EXCLUDE` (solapamiento rechazado con 409, renovación no solapada aceptada).
- `test_good_taxpayer_dynamic.py` cubre el cálculo dinámico (activo/expirado/por defecto), el rechazo 422 de `is_good_taxpayer` en creación/edición, y el cálculo batched en listados.
