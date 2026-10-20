# ADR-0079 — Control, conciliación y evidencia mensual del SIRE (M4)

Estado: aceptada. Aceptación funcional del propietario completada (19/10/2026) en la interfaz real, con un contribuyente activo y septiembre de 2026. Migración 0025 (`sire_controls`, `sire_control_sources`, `sire_control_files`, `sire_control_reviews`; solo agrega tablas; el retroceso se BLOQUEA si existe algún expediente para no destruir evidencia).

## Contexto
M1–M3 permiten abrir el workspace mensual de un contribuyente y acceder con supervisión a SIRE (RVIE/RCE) en SUNAT. Falta lo que el contador necesita después: comparar lo que informa SUNAT con lo que existe en DARWIN/CONTASIS, decidir y conservar evidencia, sin duplicar CONTASIS/DARWIN ni presentar nada.

## Decisiones
1. **Expediente por `tenant + contribuyente + período + componente (RVIE|RCE)`** (`sire_controls`, único por esa llave). Nunca se crea al consultar: el primer registro de fuente lo crea (bloqueo `SELECT … FOR UPDATE` de la fila; `row_version` optimista).
2. **Fuentes inmutables y versionadas** (`sire_control_sources`): origen `SUNAT` | `DARWIN`, modo `MANUAL` | `IMPORT`, `replaces_source_id` (reemplazar crea una versión nueva; la anterior queda en el historial), `content_hash` e `idempotency_key` (índices únicos parciales). Cada dato (cantidad, base, IGV, total) puede ser **desconocido (NULL) ≠ 0**.
3. **Motor determinista** (`sire_control_logic`): `Decimal` (≤ 2 decimales, nunca `float`), diferencia = DARWIN − SUNAT, comparación exacta por defecto; la tolerancia es explícita (campos propios, auditada, visible) y jamás oculta la diferencia original. Estados: sin fuentes, parcial, cuadra, diferencias, no comparable (período/moneda distintos o ningún concepto común). Nunca corrige datos.
4. **Revisión humana versionada** (`sire_control_reviews`): verificado | observado | por regularizar; el verificado exige que el control cuadre; observado/regularizar exigen texto. Guarda `comparison_hash` + instantánea; si una fuente cambia, la revisión vigente queda como **«Requiere nueva revisión»** y se conserva en el historial.
5. **Importación CSV/XLSX con vista previa** (`sire_import_parser`): detección de codificación (UTF-8/CP1252) y delimitador, elección de hoja, mapeo de columnas sugerido pero confirmado por el usuario, estilo decimal (coma/punto) independiente del formato visual con guarda de ambigüedad (obliga a elegir), rechazo de encabezados ambiguos, filas de totales, importes no estrictamente numéricos, periodo o tipo (ventas/compras) incorrecto. XLSX con `openpyxl` en solo lectura, **sin ejecutar ni evaluar fórmulas**; se rechazan macros y partes OOXML prohibidas. Límites de tamaño (5 MB) y filas (20 000); errores acotados (50).
6. **Evidencia** (`sire_control_files`): almacenamiento fuera del repositorio con la infraestructura existente (staging → clasificación por contenido → promoción), SHA-256, duplicados por (control, origen, hash) sin duplicar, estados `STAGED/ACTIVE/SUPERSEDED`, verificación de integridad, descarga autorizada y auditada; la API no expone rutas físicas. Fallos parciales compensan los blobs huérfanos.
7. **RBAC y aislamiento**: permisos `sire_control:read` (administrador, contador, asistente) y `sire_control:write` (administrador, contador); CSRF en mutaciones; recursos de otro tenant → 404.
8. **Integración**: pestaña SIRE del workspace (dos secciones independientes, sin reemplazar el estado jurídico ni el acceso SUNAT de M3), columna **«Control SIRE»** en el cockpit (resumen por cliente; no sustituye los indicadores de presentación, riesgo ni vencimiento) y reporte compacto imprimible con advertencia expresa de que **no es una constancia ni una presentación oficial de SUNAT**.
9. **Sin automatización nueva**: M4 no importa módulos de bóveda, broker ni agente (hay una prueba que lo verifica) y no modifica estados de presentación, importes tributarios, pagos, vencimientos ni obligaciones (prueba de instantánea de tablas tributarias).

## Alcance aceptado y semántica de los datos
| Ámbito | Estado |
|---|---|
| M4 — alcance manual (fuentes por ingreso manual, comparación, diferencias, revisión, historial, reporte) | **Aceptado por el propietario** |
| Evidencias — carga y descarga de archivos (PDF/imagen/XLSX) | **Validadas por el propietario**; integridad SHA-256 comprobada en la auditoría de cierre sin modificar el original |
| CSV/XLSX estructurados — importación y mapeo de columnas | Pruebas sintéticas aprobadas; **NO VALIDADO CON FORMATOS REALES** (compatibilidad pendiente, no bloquea lo ya aprobado) |
| SUNAT | Sin presentaciones ni operaciones tributarias automatizadas |

La interfaz y el reporte distinguen: (1) datos ingresados manualmente y atribuidos a SUNAT, (2) ingresados manualmente y atribuidos a DARWIN/CONTASIS, (3) datos importados de un archivo conservado con su SHA-256, (4) cuadre numérico, (5) revisión profesional y (6) presentación efectiva ante SUNAT, que **este módulo no acredita nunca**. Un archivo adjunto como evidencia NO se interpreta ni se presume que respalde los importes registrados salvo que se haya importado estructuralmente. La advertencia «no es una constancia oficial de presentación» se muestra una vez en el contexto de los controles y una vez en el reporte imprimible.

La **fase mensual** («Operación», «Cierre») la deriva M1 exclusivamente del calendario (`monthly_phase`: para septiembre de 2026, desde el 15 de octubre corresponde «Cierre»); ninguna operación M4 la modifica (prueba dedicada).

Una revisión corregida es una **nueva versión**: la anterior pasa a `SUPERSEDED` y se conserva sin editar; el reporte usa la vigente y no cambia la decisión por sí mismo.

## Migración 0025 y retroceso
Solo agrega tablas. `downgrade` solo se ejecuta cuando **no existe ningún expediente** (`sire_controls` vacío); si existe alguno, aborta con `RuntimeError` para no perder fuentes, revisiones ni referencias a evidencia. No se ejecuta retroceso sobre la base de aceptación `m2live`.

## Límites declarados (NO inventar compatibilidad)
- **Los formatos reales de exportación de SUNAT y de DARWIN/CONTASIS NO están validados.** El importador es genérico y configurable (mapeo de columnas confirmado por el usuario) y solo se probó con **fixtures sintéticos**. Cualquier archivo real deberá probarse y, si hace falta, añadir un mapeo.
- PDF e imágenes se conservan como respaldo: no se interpretan automáticamente.
- No hay acceso a bases internas de CONTASIS ni ingeniería inversa de DARWIN.
- Un error de importación no persiste una fuente (solo se informa en la vista previa o al confirmar).

## Consecuencias
El contador cuenta con un expediente trazable y reproducible por mes y registro; añadir un formato real implica solo un nuevo mapeo y fixtures, no cambios al motor.
