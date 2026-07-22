# ADR-0014: Últimos dígitos aplicables como arreglo normalizado

## Estado
Aceptado

## Contexto
Las reglas de cronograma de SUNAT suelen agruparse por el último dígito del RUC (individual o en grupos, p. ej. "0 y 1", "2 y 3"...). Representar esto como texto libre (`"0;1"`, `"0,1"`, `"0-1"`) habría hecho la comparación y validación frágiles.

## Decisión
`applicable_last_digits` es un `SMALLINT[]` de Postgres (`NULL` = aplica a todos los dígitos). La normalización (`normalize_applicable_last_digits`, `app/services/tax_schedule_digits.py`) vive en la capa de servicio: rechaza arreglos vacíos, valores fuera de 0-9, duplicados, y no-enteros; siempre devuelve la lista ordenada y sin duplicados.

### Restricción a nivel de base de datos sin subconsulta
Postgres no permite subconsultas dentro de un `CHECK CONSTRAINT`. La restricción de rango (0-9) se implementa con el operador de contención de arreglos: `applicable_last_digits <@ ARRAY[0,1,2,3,4,5,6,7,8,9]::smallint[]`, en vez de `NOT EXISTS (SELECT ... FROM unnest(...))` — este último falló en Postgres con `FeatureNotSupported: cannot use subquery in check constraint`. La ausencia de duplicados y de arreglos vacíos se sigue validando en Python, no en el `CHECK` (esas dos condiciones no son expresables con operadores de arreglo simples).

## Consecuencias
- El resolver (ver ADR-0015) puede calcular la especificidad de una regla contando `len(digits)` directamente, sin parsear texto.
- La importación de CSV (`_parse_digits_cell`) acepta `"0;1;2"` (separado por `;`) y delega toda la validación de contenido a `normalize_applicable_last_digits`.
