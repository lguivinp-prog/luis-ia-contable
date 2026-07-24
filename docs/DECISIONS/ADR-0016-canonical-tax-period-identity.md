# ADR-0016: Identidad canónica de periodos tributarios

## Estado
Aceptado

## Contexto
Un periodo tributario (`tax_periods`) debe tener una identidad inequívoca por tenant: no puede existir más de un periodo mensual para el mismo año fiscal y mes, ni más de un periodo anual para el mismo año fiscal, ni dos periodos especiales con el mismo `canonical_code`. Tampoco puede existir un periodo internamente incoherente (por ejemplo, uno mensual cuyo `start_date`/`end_date` no correspondan realmente a ese mes, o uno anual con un `month` no nulo). Validar esto solo en la capa de servicio es insuficiente: una inserción SQL directa (una migración de datos, un script administrativo, un error humano con acceso a la base) podría crear un periodo canónico duplicado o mal formado sin pasar por `tax_period_service`.

## Decisión
La canonicidad de `tax_periods` se garantiza a nivel de base de datos, no solo en Python (`app/models/tax_period.py`):

- **`ck_tax_periods_canonicity`**: un único `CHECK` que exige, según `period_type`, que `canonical_code`, `schedule_tax_period_code`, `start_date` y `end_date` se deriven exactamente de `fiscal_year`/`month` mediante funciones inmutables de Postgres (`make_date`, concatenación, aritmética de intervalos) — nunca texto libre. Para `MONTHLY`: `canonical_code = fiscal_year || '-' || LPAD(month, 2, '0')`, `start_date = make_date(fiscal_year, month, 1)`, `end_date` es el último día de ese mes. Para `ANNUAL`: `month IS NULL`, `canonical_code = fiscal_year`, el rango cubre el año calendario completo. Para `SPECIAL`: `canonical_code` no vacío y `end_date >= start_date`.
- **`ck_tax_periods_schedule_code_format`**: `CHECK` sobre `schedule_tax_period_code` con una expresión regular estricta (`^[0-9]{4}(-(0[1-9]|1[0-2]))?$`) que acepta únicamente años de 4 dígitos con un mes opcional `01`–`12`, rechazando `00`, `13`–`99` o formatos sin ceros a la izquierda directamente en la base.
- **Tres índices únicos parciales, uno por tipo** (`ux_tax_periods_monthly_identity`, `ux_tax_periods_annual_identity`, `ux_tax_periods_special_identity`), cada uno con su propio `postgresql_where` sobre `period_type`. Un `UNIQUE` único sin partición por tipo permitiría, por ejemplo, que un periodo especial con `fiscal_year` casualmente igual al de uno mensual bloqueara la inserción de este último sin relación real entre ambos.
- **`tax_period_service.create_period`** valida lo mismo en Python antes de insertar, para devolver un error de dominio legible (`DuplicatePeriodError`) en vez de dejar que el `IntegrityError` crudo llegue al router — pero la garantía real, la que protege contra un `INSERT` directo que se salte el servicio, vive en el `CHECK` y los índices parciales, no en Python.

## Alternativas consideradas
- **Un único `UNIQUE(tenant_id, fiscal_year, month)` sin partición por `period_type`**: rechazado — no distingue un periodo mensual de uno especial que coincida numéricamente, y no cubre el caso anual (`month IS NULL` en todas las filas anuales colisionaría entre sí bajo un índice no parcial que ignore el tipo).
- **Validar la canonicidad solo en el servicio**: rechazado explícitamente — no protege contra una inserción SQL/ORM directa que omita `tax_period_service`, que es exactamente el escenario que el `CHECK` y los índices existen para bloquear.

## Consecuencias
- `test_tax_periods_canonicity.py` prueba que una inserción SQL directa con un mes fuera de rango, un periodo anual con `month` no nulo, un `canonical_code` que no coincide con `fiscal_year`/`month`, o un duplicado exacto por tipo, son rechazados por la base con `IntegrityError` — no solo por el servicio.
- Cualquier tabla futura que necesite "una fila canónica por combinación de campos, mutuamente excluyente según un discriminador" debe seguir el mismo patrón: índice único **parcial** por rama del discriminador, nunca un `UNIQUE` plano que mezcle las ramas.

## Compatibilidad
Sin cambios de comportamiento en Bloque 3.2: `tax_periods` no se modifica en 3.2 más allá de ser referenciado por las nuevas tablas (`declarations` ya lo referenciaba desde 3.1; `professional_fee_charges` lo referencia como periodo de facturación).

## Referencias
- Modelo: `app/models/tax_period.py`
- Servicio: `app/services/tax_period_service.py`
- Migración: `backend/migrations/versions/0005_tax_periods.py`
- Pruebas: `backend/tests/integration/test_tax_periods_canonicity.py`
