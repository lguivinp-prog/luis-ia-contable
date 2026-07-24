# ADR-0027: Contenido determinista del snapshot de resolución de cronograma

## Estado
Aceptado

## Contexto
El checksum de staleness de la generación masiva (ver ADR-0021) necesita comparar "lo que el usuario vio en el preview" contra "el estado actual de los datos" de forma confiable. Si el contenido que se hashea incluyera metadatos de ejecución (la fecha en que se generó el preview, qué usuario lo pidió, a qué `run` pertenece), dos previews generados en momentos distintos sobre exactamente los mismos datos subyacentes producirían checksums distintos — el checksum dejaría de significar "los datos cambiaron" y pasaría a significar, incorrectamente, "el preview se pidió de nuevo".

## Decisión
`app/schemas/declaration_snapshot.py` separa explícitamente dos conceptos con dos modelos Pydantic distintos:

- **`DeclarationScheduleResolutionV1`**: el contenido **determinista y hasheable** de una resolución de cronograma (qué regla aplicó, qué fecha resultó, con qué categoría/último dígito). Nunca incluye metadatos de ejecución.
- **`DeclarationScheduleSnapshotV1`**: envuelve esa resolución con la procedencia real (quién, cuándo, desde qué `run`) — es lo que finalmente se guarda en `declarations.schedule_snapshot`.

El checksum de staleness de la generación masiva (`_compute_checksum` en `declaration_generation_service.py`) se calcula **exclusivamente** sobre `DeclarationScheduleResolutionV1` serializada de forma determinista (JSON con claves ordenadas), nunca sobre el snapshot completo con su envoltura de procedencia. `declaration_snapshot_service` es responsable de ensamblar ambos: primero resuelve el `ScheduleResolution` puro (motor de cronogramas), luego lo envuelve con la procedencia antes de persistirlo.

`DeclarationGenerationRunItem` (ver ADR-0021) persiste exactamente esta resolución determinista tal como el usuario la revisó — el checksum del run y la pantalla de revisión se calculan y leen **siempre** desde esas columnas persistidas, nunca re-resolviendo en silencio contra el estado actual (que podría haber cambiado).

## Alternativas consideradas
- **Un único modelo que mezcle resolución y procedencia**: rechazado — forzaría a excluir campos manualmente antes de cada cálculo de checksum, con el riesgo real de que alguien olvide excluir un campo de procedencia nuevo y rompa la detección de staleness silenciosamente.
- **Recalcular el checksum sobre el estado actual en el momento de confirmar, en vez de comparar contra lo persistido en el preview**: rechazado — ver ADR-0021, no permitiría distinguir con precisión qué cambió entre lo que el usuario aprobó y el estado real al confirmar.

## Consecuencias
- `test_declaration_snapshot.py` prueba que dos resoluciones con el mismo contenido mas distinta procedencia producen el mismo checksum, y que cualquier campo determinista distinto cambia el checksum.
- `declaration_snapshot_service` es el único punto de ensamblaje entre el motor de cronogramas puro y la persistencia en `declarations`/`declaration_generation_run_items` — ningún otro servicio construye un snapshot directamente.

## Compatibilidad
Sin cambios en Bloque 3.2. Las nuevas tablas de 3.2 no tienen un concepto de "snapshot determinista hasheable" equivalente; el patrón se mantiene específico al motor de cronogramas.

## Referencias
- Schemas: `app/schemas/declaration_snapshot.py`
- Servicio: `app/services/declaration_snapshot_service.py`, `app/services/declaration_generation_service.py` (`_compute_checksum`)
- Pruebas: `backend/tests/unit/test_declaration_snapshot.py`
