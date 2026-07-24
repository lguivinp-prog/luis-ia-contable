# ADR-0054: Cadena lógica de versiones por archivo, sin restauración, `file_metadata` como legado descriptivo

## Estado
Aceptado

## Contexto
Un ítem documental necesita soportar tanto múltiples adjuntos independientes (varios comprobantes distintos
para un mismo requisito) como el reemplazo de un archivo ya cargado (una versión corregida del mismo
comprobante), sin perder el historial de ninguno de los dos casos ni confundirlos entre sí.

## Decisión

### `logical_file_id` identifica la cadena; `version` la posición dentro de ella
La tabla `declaration_document_files` (migración `0013`) usa `logical_file_id` (UUID) para agrupar todas las
versiones de un mismo archivo lógico: una carga inicial genera un `logical_file_id` nuevo (`version=1`,
`replaces_file_id=NULL`); un reemplazo conserva el `logical_file_id` de la cadena existente e incrementa
`version`. Varios `logical_file_id` distintos pueden coexistir `ACTIVE` en el mismo ítem — soporta el caso de
múltiples adjuntos independientes sin que se pisen entre sí.

### Solo una versión `ACTIVE` por cadena, impuesto estructuralmente
Índice único parcial: `UNIQUE(tenant_id, logical_file_id) WHERE status = 'ACTIVE'`. No es solo disciplina de
aplicación — PostgreSQL rechaza a nivel de base de datos cualquier intento de dejar dos versiones `ACTIVE`
simultáneas en la misma cadena, incluso ante un error de programación futuro. Por no ser diferible, el flujo
de reemplazo requiere un `flush()` explícito entre marcar la versión anterior `SUPERSEDED` e insertar la nueva
`ACTIVE` (ver ADR-0055).

### Integridad estructural de la cadena, con FK compuesta hacia la propia tabla
`CHECK ((version = 1 AND replaces_file_id IS NULL) OR (version > 1 AND replaces_file_id IS NOT NULL))` impide
una versión inicial que declare reemplazo o una versión posterior sin predecesora. Una FK compuesta adicional
sobre `(replaces_file_id, tenant_id, client_id, declaration_id, declaration_document_item_id, logical_file_id)`
acota `replaces_file_id` a la misma cadena exacta — un reemplazo no puede enlazar, ni por error de aplicación,
con un archivo de otro ítem, otra declaración o otro cliente del mismo tenant. 4 pruebas SQL directas
confirman cada caso de violación (`version=1` con predecesor, `version>1` sin predecesor, reemplazo cruzado de
ítem, reemplazo cruzado de cadena).

### Tres estados, transiciones sin ambigüedad
`ACTIVE` / `SUPERSEDED` / `DELETED`, cada uno con columnas de cierre mutuamente excluyentes forzadas por
`CHECK` (`superseded_at` solo en `SUPERSEDED`; `deleted_at`/`deleted_by`/`deletion_reason` solo en `DELETED`).
Solo una versión `ACTIVE` puede reemplazarse o eliminarse (409 en cualquier otro caso); `SUPERSEDED` es
historial inmutable; `DELETED` es la transición directa `ACTIVE → DELETED` (nunca pasa por `SUPERSEDED`) y es
terminal para esa cadena.

### Sin restauración — mismo precedente que anular un pago
No existe endpoint para reactivar una versión `DELETED` ni para revivir una cadena cerrada. Si se necesita el
documento de nuevo, se sube como una **cadena nueva** (`logical_file_id` nuevo) — mismo principio que anular
un pago (Bloque 3.2) o cerrar una determinación tributaria: el historial se conserva, no se revierte.

### `file_metadata` (legado) permanece como notas descriptivas, nunca como evidencia física
La columna `declaration_document_items.file_metadata` no cambia de esquema ni de contrato:
`DocumentFileMetadataV1` (`extra="forbid"`) sigue rechazando cualquier ruta/URL, exactamente como antes de este
bloque. Se trata como texto libre heredado (p. ej. "recibido por correo, 2 páginas"), nunca se migra a
`declaration_document_files` (no hay bytes reales detrás), sigue editable vía el mismo `PATCH
.../documents/{item_id}` existente, y el frontend nunca la usa para poblar la lista de archivos de un ítem —
esa lista proviene exclusivamente de la tabla nueva. Una prueba nueva reconfirma el rechazo de `{"path": ...}`
junto a la prueba existente, blindando el contrato contra una relajación accidental futura.

## Alternativas consideradas
- **Versionar `file_metadata` in-place**: rechazado — mezclaría metadata descriptiva heredada con evidencia
  física real bajo el mismo campo, perdiendo la distinción explícita que este ADR fija.
- **Un solo archivo activo por ítem (sin múltiples cadenas)**: rechazado — no cubre el caso real de varios
  comprobantes independientes para el mismo requisito documental.
- **Endpoint de restauración de una versión eliminada**: rechazado por el mismo principio que otras
  operaciones irreversibles del sistema (anulación de pagos, cierre de declaraciones): el historial se
  conserva, no se revierte; un documento nuevo es una cadena nueva.

## Consecuencias
- `declaration_document_file_repository.lock_exact_active_file` bloquea el `file_id` exacto solicitado (no
  "cualquier ACTIVE de la cadena"), evitando que dos peticiones sobre la misma versión original se encadenen
  silenciosamente sobre versiones distintas (ver ADR-0055 para el detalle de concurrencia).
- El frontend agrupa por `logical_file_id` (`groupIntoChains` en `DocumentItemFiles.tsx`), mostrando la
  versión activa y un historial colapsable de `SUPERSEDED`/`DELETED`.
- `docs/DATA_MODEL.md` documenta explícitamente: "`file_metadata` = notas heredadas, nunca el archivo real;
  `declaration_document_files` = evidencia física real".
