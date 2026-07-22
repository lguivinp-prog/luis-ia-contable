# ADR-0015: Especificidad determinista de reglas de cronograma (sin suma de pesos)

## Estado
Aceptado

## Contexto
Cuando varias reglas (entradas o excepciones) de una misma versión aprobada aplican al mismo cliente/dígito/periodo, el sistema debe elegir una única regla de forma predecible y explicable — nunca mediante una fórmula de puntaje ponderado que un humano no pueda auditar a simple vista, y nunca inventando una prioridad que la fuente importada no definió.

## Decisión
La selección usa una **comparación estricta por tupla** (`_specificity_tuple`, `app/services/tax_schedule_resolver.py`), en este orden de criterios, sin combinarlos en una suma:

1. **Excepción vence a regla general** — son tablas separadas (`tax_schedule_exceptions` siempre se evalúa antes que `tax_schedule_entries`); si hay una excepción aplicable, la regla general ni se considera.
2. **Categoría específica vence a regla sin categoría** (`category_rank`: 0 si `required_category_code` no es `NULL`, 1 en caso contrario).
3. **Menos dígitos aplicables es más específico** (`digit_rank = len(digits)`; `NULL` — aplica a todos — usa un centinela alto (100), el menos específico posible).
4. **`priority` explícito, solo como último desempate**, y solo si la fuente importada lo definió (nunca inventado por el sistema); si no está definido, se usa un centinela muy alto (1 000 000) que nunca gana un desempate real.

Si tras aplicar los cuatro criterios sigue habiendo más de una regla empatada, se lanza `AmbiguousScheduleRuleError` (409) en vez de elegir arbitrariamente. Lo mismo aplica al elegir entre versiones `APPROVED` de distinto `scope_category_code` cuando el cliente cumple con más de una categoría igualmente específica (`select_applicable_version`).

## Alternativas consideradas
- **Suma ponderada de puntajes** (p. ej. `categoria*10 + (10-digitos) + prioridad`): rechazada explícitamente durante la revisión del plan por ser difícil de auditar y poder producir empates o resultados no intuitivos según cómo se elijan los pesos.

## Consecuencias
- `test_tax_schedule_resolver.py` cubre cada nivel de la tupla de forma aislada (categoría vs. general, dígitos específicos vs. `NULL`, excepción vs. entrada) y el caso de ambigüedad real (dos reglas con tupla idéntica).
- Cualquier regla nueva de desempate futura debe añadirse como un nivel adicional de la tupla, nunca como una ponderación combinada.
