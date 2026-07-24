# ADR-0053: Almacenamiento real de archivos documentales — sistema de archivos local + metadatos en PostgreSQL

## Estado
Aceptado

## Contexto
El control documental (Bloque 3.2, ADR-0033 a ADR-0036) modela por completo el ciclo de vida de un requisito
documental (catálogo híbrido, asignación por cliente, sincronización idempotente, transiciones de estado) pero
nunca guarda un archivo real: `declaration_document_items.file_metadata` (JSONB, validado por
`DocumentFileMetadataV1` con `extra="forbid"`) solo acepta `original_filename`/`mime_type`/`size_bytes`/`notes`
— cualquier ruta, URL o clave de almacenamiento (`path`, `url`, `drive_id`) es rechazada por el propio modelo
Pydantic, confirmado por `test_change_item_status_rejects_invalid_file_metadata`. Este bloque añade
almacenamiento físico real sin tocar ese guardia: se le da una vía nueva y explícita (una tabla y un servicio
propios), dejando `file_metadata` como metadata descriptiva heredada (ver ADR-0054).

## Decisión

### Sistema de archivos local + PostgreSQL para metadatos, detrás de una interfaz desacoplada
```python
class FileStorageBackend(Protocol):
    def write_staged(self, temporary_key: str, stream: BinaryIO, *, max_bytes: int) -> StagedBlob: ...
    def promote(self, temporary_key: str, final_key: str) -> None: ...
    def delete(self, key: str) -> None: ...
    def open(self, key: str) -> BinaryIO: ...
    def exists(self, key: str) -> bool: ...
    def iter_keys(self, prefix: str) -> Iterable[str]: ...
    def stat(self, key: str) -> BlobStat: ...
```
Ninguna capa fuera de `backend/app/core/file_storage.py` recibe jamás una ruta absoluta — los llamadores solo
conocen claves lógicas (`storage_key`, `temporary_key`). `LocalFileStorageBackend` es la única implementación
de este bloque; una futura migración a otro backend (Drive, S3) implementaría el mismo protocolo sin tocar el
servicio de dominio ni el modelo de datos.

### Ruta de almacenamiento fuera del árbol de código
El volumen Docker **nombrado** `document_storage` se monta únicamente en el servicio `backend`, en
`/var/lib/luis-ia-contable/documents` — deliberadamente fuera de `/app` (que es un **bind mount** del código
fuente, `./backend:/app`, con recarga en vivo). Confundir ambos habría filtrado archivos reales de clientes al
árbol de git. `LocalFileStorageBackend._resolve(key)` añade una segunda defensa: verifica
`Path.resolve().is_relative_to(root)` antes de cualquier operación, aunque `storage_key` nunca se construye a
partir de datos externos (ver ADR-0056).

### `write_staged`/`promote`: nunca se escribe directamente sobre el nombre definitivo
Toda carga escribe primero a una clave temporal (`.part`) en streaming, calculando SHA-256 incrementalmente y
aplicando el límite de tamaño mientras se lee (nunca confiando solo en `Content-Length`). Solo tras pasar toda
la validación de contenido (ADR-0056) se hace `promote()` — un `os.replace()` atómico de temporal a
definitivo. Un `.part` fallido nunca llega a ser el archivo "oficial" de una fila en BD.

## Alternativas consideradas
- **BYTEA en PostgreSQL**: rechazado — infla la base de datos y sus backups sin aportar nada de cara a una
  eventual migración a almacenamiento en la nube.
- **Almacenamiento de objetos tipo S3/MinIO**: rechazado — añadiría un servicio nuevo a Docker Compose para un
  solo despacho local-first, la misma razón por la que ADR-0012 ya había rechazado Google Drive para la
  evidencia de importación de cronogramas. `ADR-0012` no se reabre: ese caso es texto plano que cabe en una
  columna `TEXT`; los documentos de este bloque son binarios de tamaño variable.
- **Bind mount del host para el volumen documental**: rechazado — un volumen Docker nombrado aísla el
  contenido real de clientes del árbol de código versionado, evitando cualquier filtración accidental a git.

## Consecuencias
- Requiere recrear el contenedor `backend` una vez (nuevo volumen en `docker-compose.yml`).
- Toda operación de archivo pasa por `FileStorageBackend`, nunca por `open()`/`Path` directos en el servicio o
  el router — verificado por revisión de código y por que las pruebas inyectan un
  `LocalFileStorageBackend(tmp_path)` vía `get_file_storage` (nunca la ruta real de producción).
- El reporte de integridad (ADR-0055) y el respaldo/restauración (mismo ADR) reutilizan la misma interfaz
  (`iter_keys`/`stat`) sin conocer nunca una ruta física real.
