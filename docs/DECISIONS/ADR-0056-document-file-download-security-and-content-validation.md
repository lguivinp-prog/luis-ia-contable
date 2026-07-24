# ADR-0056: Validación de contenido real, saneamiento de nombre y descarga segura para archivos documentales

## Estado
Aceptado

## Contexto
Aceptar archivos subidos por el usuario y servirlos de vuelta introduce una superficie de ataque conocida:
contenido que no corresponde a su extensión declarada (incluidos ZIP-bombs disfrazados de DOCX/XLSX), nombres
de archivo que intentan path traversal o inyección de cabeceras, y la necesidad de un permiso separado para
las operaciones más sensibles (reemplazar/eliminar) frente a las de solo lectura o carga inicial.

## Decisión

### La clase del archivo se deriva del contenido real, nunca de la extensión ni del `Content-Type` declarado
`classify_file_content(stream: BinaryIO)` opera siempre sobre un stream obtenido de `storage.open()` (nunca
una ruta), nunca confía en el nombre ni el MIME que el cliente envía:
- **PDF/JPEG/PNG**: detección por firma real de los primeros bytes vía la librería `filetype` (pura Python,
  sin dependencia de `libmagic`).
- **DOCX/XLSX (OOXML)**: verificación estructural real, no solo "es un ZIP válido". Antes de leer cualquier
  contenido se validan los límites del directorio central (máx. 2000 entradas, máx. 200 MB descomprimidos en
  total, máx. 100 MB por entrada, ratio de compresión máx. 100:1) — defensa contra ZIP-bombs, aplicada sobre
  metadatos del ZIP antes de descomprimir nada. Se rechazan nombres de entrada con componentes de traversal.
  La clasificación DOCX vs. XLSX es por prefijo **exclusivo** (`word/` sin `xl/`, o viceversa; ambos presentes
  o ninguno se rechaza). Se rechaza cualquier variante macro-habilitada (`vbaProject.bin`, detectado con
  `endswith` sobre el nombre completo de la entrada — un `in`/igualdad exacta habría fallado en dejar pasar
  macros reales, ya que la entrada real es `word/vbaProject.bin`, no `vbaProject.bin` a secas; error encontrado
  y corregido durante el desarrollo antes de escribir las pruebas formales). Nunca se extrae ningún archivo del
  ZIP al disco en ningún paso.
- **Allowlist cerrada**: PDF, JPEG, PNG, XLSX, DOCX. Cualquier otro contenido detectado se rechaza con 422,
  incluidos ejecutables, scripts, SVG y archivos comprimidos genéricos.

### Nombre original saneado antes de insertar, nunca usado para construir la clave física
`sanitize_original_filename(raw)` quita cualquier componente de ruta (unix y windows), rechaza caracteres de
control y `CR`/`LF`, trunca a 255 caracteres, y usa `"documento"` como último recurso si el resultado queda
vacío. El valor guardado es puramente de presentación — `storage_key` siempre se genera del lado del servidor
(`{tenant_id}/{item_id}/{logical_file_id}/v{version}-{uuid4().hex}{extensión_derivada_del_contenido}`), nunca a
partir del nombre que el cliente envió.

### `Content-Disposition` seguro, con soporte RFC 6266 para nombres no ASCII
`build_content_disposition(filename)` construye siempre `filename="..."` (fallback ASCII-safe) **y**
`filename*=UTF-8''<percent-encoded>` cuando el nombre saneado contiene caracteres no ASCII — nunca
interpolación directa sin codificar. Toda respuesta de descarga incluye además `X-Content-Type-Options:
nosniff`.

### Ningún campo de la API expone jamás una ruta física
`DeclarationDocumentFileOut` omite deliberadamente `storage_key` y cualquier variante de ruta — verificado
explícitamente en pruebas de integración (`assert "storage_key" not in body`). El reporte de integridad
(ADR-0055) sigue la misma regla mediante `blob_fingerprint`.

### Permiso `document:file_manage`, separado de lectura y de carga inicial
Nuevo permiso, nombrado deliberadamente para cubrir **ambas** operaciones sensibles (reemplazar y eliminar) en
vez de llamarse "delete" y resultar engañoso para el reemplazo. Añadido a `_CONTADOR_ONLY_ADDITIONS`:
`admin`/`contador` lo tienen, `asistente` no. La carga inicial reutiliza `document:item_write` (asistente
incluido, ya que subir un comprobante es una tarea operativa habitual); la descarga reutiliza `document:read`
(los 3 roles); el reporte de integridad usa `document:manage` (ya existente, mismo grupo que gestionar el
catálogo documental).

## Alternativas consideradas
- **Confiar en el `Content-Type` que envía el cliente**: rechazado — es trivialmente falsificable; toda la
  validación se basa en contenido real, nunca en metadata declarada por el cliente.
- **Aceptar cualquier ZIP como DOCX/XLSX sin verificar estructura interna**: rechazado — permitiría subir un
  ZIP arbitrario (incluido uno diseñado como ZIP-bomb) simplemente renombrándolo; la verificación estructural
  con límites de directorio central cierra esa vía.
- **Un solo permiso `document:file_delete`**: rechazado — habría sido engañoso para la operación de
  reemplazo, que también es sensible y no es una eliminación.
- **Servir descargas desde una ruta estática pública**: rechazado — toda descarga pasa por
  `require_permission(DOCUMENT_READ)` y revalida `tenant_id`, nunca hay una URL directa al blob.

## Consecuencias
- `test_document_file_content_validation.py` (14 pruebas) cubre firma real PDF/JPEG/PNG, estructura OOXML
  válida e inválida (ZIP genérico renombrado, ambigüedad word+xl, macro-habilitado, ZIP-bomb, traversal de
  nombre de entrada).
- `test_document_file_naming.py` (9 pruebas) cubre saneamiento de ruta/control/CRLF/truncado y codificación
  RFC 6266 de `Content-Disposition`.
- `test_permission_matrix_api.py` incluye el caso 200/403 de `document:file_manage`; el asistente puede subir
  pero recibe 403 al intentar reemplazar o eliminar, confirmado también manualmente en navegador con los 3
  roles reales.
- `docs/SECURITY_MODEL.md` documenta la distinción de permisos y la cadena de validación de contenido.
