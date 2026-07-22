# ADR-0011: Versionado de cronogramas tributarios

## Estado
Aceptado

## Contexto
El motor de cronogramas debe garantizar que ningún vencimiento se calcule desde datos no aprobados por un humano, que las correcciones nunca sobrescriban una versión ya aprobada, y que el reemplazo de una versión no afecte accidentalmente cronogramas de otros años o categorías.

## Decisión
Estados de `tax_schedule_versions`: `DRAFT → IMPORTED → PENDING_REVIEW → APPROVED → SUPERSEDED`, con salidas laterales a `REJECTED`/`CANCELLED`. Las transiciones válidas están codificadas explícitamente en `VALID_TRANSITIONS` (`app/services/tax_schedule_service.py`) y se validan en cada operación — cualquier transición fuera de esa tabla lanza `InvalidTransitionError` (409).

Una versión `APPROVED` es **inmutable**: cualquier corrección crea una versión nueva (nunca se edita in-place).

### Alcance exacto para "reemplazar"
Aprobar una versión nueva solo puede reemplazar (`SUPERSEDED`) la versión previa `APPROVED` con el **mismo scope exacto**: `(tenant_id, source_id, fiscal_year, schedule_type, scope_category_code)`. Un índice único parcial de Postgres (`ux_tax_schedule_versions_one_approved_per_scope`, con `COALESCE(scope_category_code, '')` para que `NULL` también quede cubierto) garantiza a nivel de base de datos que nunca existan dos versiones `APPROVED` del mismo scope simultáneamente — este es el mecanismo real de control de concurrencia, no una simple verificación en Python.

### Orden crítico en `approve()`
El servicio primero degrada la versión previa a `SUPERSEDED` y hace `flush()`, **antes** de promover la nueva versión a `APPROVED`. Si el orden fuera inverso, el propio `flush()` de la nueva versión chocaría con el índice único parcial (dos filas `APPROVED` del mismo scope coexistiendo momentáneamente dentro de la misma transacción).

### Traducción de errores de concurrencia
Una carrera real entre dos aprobaciones concurrentes del mismo scope se manifiesta como un `IntegrityError` de Postgres al hacer `flush()`; el servicio lo captura y lo traduce a `ConcurrentApprovalConflictError` (409), con rollback explícito.

## Consecuencias
- `test_tax_schedule_transitions.py` cubre las transiciones válidas/inválidas, la inmutabilidad de `APPROVED`, y que aprobar una versión nueva reemplaza automáticamente la anterior del mismo scope (nunca de otro año fiscal).
- `test_tax_schedule_concurrency.py` verifica el índice único a nivel de base de datos y la traducción de `IntegrityError` a `ConcurrentApprovalConflictError` simulando una lectura obsoleta (`monkeypatch` sobre `get_approved_version`).
