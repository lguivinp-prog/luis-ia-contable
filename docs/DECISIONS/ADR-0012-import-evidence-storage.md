# ADR-0012: Almacenamiento de la evidencia de importación de CSV

## Estado
Aceptado

## Contexto
El flujo de importación de cronogramas (ver ADR-0011 y `docs/TAX_RULES_MODEL.md`) exige conservar el archivo fuente como evidencia, incluso si la importación termina siendo rechazada.

## Decisión
El contenido crudo del CSV (ya decodificado como texto UTF-8) se guarda en `tax_schedule_import_runs.raw_content` (columna `TEXT`), junto con `file_checksum` (SHA-256 del archivo original en bytes), `original_filename`, y el resultado íntegro del parseo (`parsed_rows`, `validation_errors`, `diff_summary`, todos `JSONB`). No se usa almacenamiento en sistema de archivos ni en un servicio externo (Google Drive) en esta entrega — eso se evalúa para Fase 2, cuando exista integración con Drive.

## Alternativas consideradas
- **Sistema de archivos local**: descartado por ahora para evitar gestionar rutas, permisos y limpieza de archivos huérfanos antes de que exista una necesidad real de servir el archivo binario original (el CSV es texto, cabe cómodamente en una columna `TEXT` dentro de los límites de tamaño configurados — `TAX_SCHEDULE_CSV_MAX_BYTES`).
- **Google Drive**: descartado explícitamente para este bloque; el prompt maestro prohíbe implementar integraciones de Google en esta fase.

## Consecuencias
- Cada `import_run` es auto-contenido: se puede auditar o reprocesar sin depender de un archivo externo que pudiera haberse movido o borrado.
- Si en Fase 2 se decide migrar a Drive, la migración es aditiva (nueva columna de referencia externa), sin romper el historial existente.
