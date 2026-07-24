# ADR-0055: Consistencia BD-filesystem, concurrencia real de reemplazo/eliminación y reporte de integridad

## Estado
Aceptado

## Contexto
Con un backend de archivos desacoplado (ADR-0053) y una cadena lógica de versiones (ADR-0054), quedaba
resolver tres problemas relacionados: qué pasa cuando la escritura física y la fila en BD se desincronizan
ante un fallo a mitad de camino; cómo se comporta el sistema cuando dos peticiones concurrentes intentan
reemplazar o eliminar la misma versión; y cómo se detecta — sin necesitar confiar ciegamente en que nunca
ocurrió un fallo — cualquier discrepancia real entre lo que la base de datos cree y lo que el disco contiene.

## Decisión

### Orden estricto de escritura, con `flush()` doble alrededor del índice parcial no diferible
Toda carga/reemplazo sigue el orden: validar tamaño/contenido en streaming → escribir a `.part` → validar
contenido real (ADR-0056) → `promote()` (rename atómico) → insertar/actualizar filas → `flush()` → `commit()`
(exclusivo del router). Para el reemplazo específicamente, como el índice único parcial `WHERE status='ACTIVE'`
(ADR-0054) no es diferible, `replace_file` hace un `flush()` explícito justo después de marcar la versión
anterior `SUPERSEDED` y antes de insertar la nueva fila `ACTIVE` — sin ese `flush()` intermedio, PostgreSQL no
puede confirmar que la fila anterior dejó de contar como `ACTIVE` antes de aceptar la segunda.

### Compensación física ante fallo, nunca oculta el error original
`compensate_physical_write(storage, *, key)` es un helper best-effort (nunca lanza) que borra un blob huérfano
tras un fallo de `flush()`/`commit()` posterior a `promote()`. Se invoca envolviendo `db.add()`+`flush()` en el
servicio; si el `flush()` falla, el `rollback()` exterior (responsabilidad exclusiva del router, sin
excepción) revierte también cualquier `UPDATE` previo — por ejemplo, en un reemplazo fallido, la versión
marcada `SUPERSEDED` vuelve a `ACTIVE` automáticamente por el propio rollback, sin lógica manual de reversión.
Si la limpieza física en sí falla, el error se descarta silenciosamente (comentario explícito en el código) y
el blob huérfano queda para que el reporte de integridad lo detecte — la compensación es la primera barrera,
el reporte es la segunda, nunca el mecanismo ordinario.

### Bloqueo del `file_id` exacto, no "cualquier ACTIVE de la cadena"
`lock_exact_active_file(db, tenant_id, declaration_id, item_id, file_id)` hace `SELECT ... FOR UPDATE` sobre la
fila exacta solicitada. Esto garantiza que dos peticiones que apuntan a la **misma versión original** se
serialicen correctamente entre sí, en vez de que una encadene silenciosamente sobre una versión ya reemplazada
por la otra. El archivo nuevo se escribe y valida en streaming **antes** de tomar cualquier bloqueo de BD
(minimiza el tiempo bajo lock); solo después se bloquea, se confirma bajo el bloqueo que la fila sigue
`ACTIVE`, y si no lo es (carrera perdida) se limpia el temporal sin promoverlo nunca y se devuelve 409.

### `populate_existing=True`: el hallazgo central de este dominio
Una prueba de concurrencia real (reemplazo vs. eliminación simultáneos sobre el mismo `file_id`, dos conexiones
reales + `threading.Event`) encontró una violación real de `ck_declaration_document_files_status_coherence`:
ambas transacciones habían modificado la misma fila. La causa: una lectura sin bloqueo anterior en la misma
sesión (el probe de `replace_file` que obtiene `logical_file_id`/`version`) había poblado el identity map de
SQLAlchemy; el `SELECT ... FOR UPDATE` posterior adquiría el lock correctamente a nivel SQL y esperaba el
commit de la otra transacción, pero SQLAlchemy devolvía el objeto Python **cacheado**, con atributos
desactualizados, en vez de refrescarlos desde la fila recién confirmada. Sin `.execution_options(
populate_existing=True)` en la consulta bloqueada, `target.status` seguía leyendo el valor previo al bloqueo —
anulando por completo la garantía que el lock existe para dar. Corregido añadiendo esa opción; verificado
repitiendo la prueba 5 veces consecutivas sin fallos, más la suite completa.

### Reporte de integridad: 5 categorías exactas, nunca expone rutas físicas
`GET /api/v1/document-files/integrity-report` (permiso `document:manage`) recorre todas las filas del tenant y
el volumen físico bajo su prefijo (`iter_keys` aísla por tenant), clasificando cada hallazgo:
- **`missing_blob`**: fila `ACTIVE`/`SUPERSEDED` cuyo blob no existe — nunca debería ocurrir dado el orden de
  escritura; indica corrupción externa.
- **`hash_mismatch`**: blob presente, pero el SHA-256 recalculado difiere del almacenado.
- **`retained_deleted_blob`**: blob de una fila `DELETED` que aún existe físicamente — **intencional**
  (Sección "eliminación física diferida" de ADR-0054), nunca se cuenta como anomalía.
- **`orphan_blob`**: blob físico sin ninguna fila correspondiente.
- **`temporary_part`**: `.part` abandonado por un proceso interrumpido antes del rename atómico.

Las entradas con fila en BD exponen `file_id`/`logical_file_id`/`status`/hashes; las que no tienen fila
(`orphan_blob`/`temporary_part`) usan `blob_fingerprint = SHA256(clave_relativa)` en vez de la clave real —
ninguna respuesta de la API expone jamás `storage_key` ni ninguna ruta física, verificado explícitamente en
pruebas.

### Backup/restauración conjunta, con verificación del 100% de los blobs
`backup.ps1`/`restore.ps1` detienen el `backend` durante toda la captura (cero escrituras concurrentes),
capturan el dump de PostgreSQL y un archivo `.tar.gz` del volumen (vía
`backend/app/cli/document_storage_archive.py`, que usa únicamente `tarfile`+`hashlib` de la librería estándar
— nunca depende de que la imagen tenga el binario `tar` del sistema), y recalculan el SHA-256 del **100%** de
los blobs registrados (nunca una muestra) antes de escribir `manifest.json`. `restore.ps1` toma primero un
snapshot de seguridad del estado actual (vía `backup.ps1 -Internal`), y si la verificación post-restauración
falla, revierte automáticamente a ese snapshot; si incluso la reversión falla, deja el `backend` detenido con
un mensaje operativo explícito en vez de arrancarlo sobre un estado parcial. Limitación aceptada y
documentada: el dump y el archivo del volumen no se capturan dentro de una transacción distribuida real —
detener el backend elimina el riesgo de escritura concurrente, pero no la vuelve atómica a nivel de sistema de
archivos subyacente.

Durante la verificación manual de este flujo se encontró un error real en `_cmd_extract`: intentaba
`shutil.rmtree()` sobre la raíz de almacenamiento completa, pero cuando esa raíz es el punto de montaje de un
volumen Docker nombrado (el caso real), el sistema operativo rechaza eliminar el punto de montaje mismo
("Device or resource busy") aunque ya esté vacío. Corregido para vaciar solo el **contenido** de la raíz,
dejando el directorio/punto de montaje intacto; verificado tanto con una prueba de regresión (comparación de
inodo antes/después) como con una restauración real de extremo a extremo contra el entorno de desarrollo.

## Alternativas consideradas
- **Confiar en el orden de flush implícito de SQLAlchemy sin `flush()` explícito**: rechazado — el índice
  único parcial no diferible exige que el `UPDATE` (marcar `SUPERSEDED`) esté confirmado en el servidor antes
  de que el `INSERT` (nueva `ACTIVE`) se intente, y SQLAlchemy no garantiza ese orden sin un `flush()` manual.
- **Bloquear "cualquier fila ACTIVE de la cadena" en vez del `file_id` exacto**: rechazado — permitiría que dos
  peticiones sobre versiones distintas de la misma cadena se sirvan sin serializarse correctamente entre sí
  cuando ambas apuntan al mismo `file_id` original.
- **Purga física inmediata al eliminar lógicamente**: rechazado — la purga real queda para un proceso futuro
  fuera de alcance; el reporte de integridad ya distingue `retained_deleted_blob` (esperado) de una anomalía
  real.
- **Backup por muestreo de blobs en vez de verificación completa**: rechazado — para un volumen de un solo
  despacho local-first, verificar el 100% es factible y es la regla, no una excepción.

## Consecuencias
- `test_document_file_replace_concurrency.py` y `test_document_file_chain_integrity.py` cubren la
  concurrencia real y la integridad estructural con dos conexiones reales, nunca con mocks.
- `test_document_integrity_report_api.py` cubre las 5 categorías exactas contra un `LocalFileStorageBackend`
  real apuntando a `tmp_path`, incluida la verificación explícita de que ninguna entrada expone `storage_key`.
- `test_document_storage_archive_cli.py` cubre `archive`/`extract` de forma aislada (sin BD); el flujo
  conjunto de `backup.ps1`/`restore.ps1` se verifica manualmente, documentado en el informe final del bloque.
