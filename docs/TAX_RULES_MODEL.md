# Modelo de reglas tributarias — Luis IA Contable

## Principio rector

**Nunca se codifican permanentemente en la lógica del sistema fechas, tributos, obligaciones, formularios o multas.** Toda regla tributaria (especialmente los cronogramas de vencimientos de SUNAT) debe:

- Tener una fuente identificable (norma, resolución, portal oficial).
- Tener año de vigencia y versión.
- Tener fecha de incorporación al sistema.
- Poder modificarse sin cambiar código (dato, no código).
- Conservar el historial de versiones anteriores.
- Requerir aprobación humana explícita antes de usarse para generar vencimientos reales.

## Alcance de esta entrega

Existe el **catálogo de obligaciones** (`obligation_catalog`, Bloque 1) y, desde este bloque, el **motor de cronogramas tributarios versionado** completo: importación desde CSV, revisión, aprobación humana explícita, y resolución de vencimientos para vista previa. **No se generan declaraciones ni eventos de calendario todavía** — eso corresponde a bloques posteriores de Fase 1.

**Ningún dato de este módulo es real.** Todo cronograma cargado en desarrollo es explícitamente ficticio/demostrativo; la interfaz y las respuestas de la API lo recuerdan (`demo_disclaimer` en la vista previa de vencimiento, aviso en la UI).

## Máquina de estados de una versión de cronograma (ADR-0011)

`DRAFT → IMPORTED → PENDING_REVIEW → APPROVED → SUPERSEDED`, con salidas laterales a `REJECTED` o `CANCELLED` desde los estados mutables. Transiciones válidas:

| Desde | Hacia |
|---|---|
| `DRAFT` | `PENDING_REVIEW`, `CANCELLED` |
| `IMPORTED` | `PENDING_REVIEW`, `CANCELLED` |
| `PENDING_REVIEW` | `APPROVED`, `REJECTED`, `CANCELLED` |
| `APPROVED` | `SUPERSEDED` (automático, al aprobarse una versión nueva del mismo alcance exacto) |
| `SUPERSEDED`, `REJECTED`, `CANCELLED` | (terminales) |

Una versión `APPROVED` es **inmutable**: cualquier corrección crea una versión nueva. Aprobar/rechazar/cancelar requieren un comentario o motivo no vacío (`MissingCommentError` si se omite).

## Flujo de importación de CSV

1. **Subir** el archivo (`POST /tax-schedules/sources/{id}/import-runs`, con `fiscal_year`, `schedule_type`, `scope_category_code` opcional) — se valida tamaño máximo (`TAX_SCHEDULE_CSV_MAX_BYTES`), extensión `.csv`, tipo de contenido, codificación UTF-8 estricta.
2. **Validar estructura**: encabezados requeridos (`tax_period`, `applicable_last_digits`, `required_category_code`, `priority`, `due_date_original`, `due_date_effective`, `due_date_type`, `notes`), tipos, formato de fecha (`YYYY-MM-DD`), formato de periodo (`YYYY` o `YYYY-MM`), límite de filas (`TAX_SCHEDULE_CSV_MAX_ROWS`), duplicados internos (misma combinación periodo/dígitos/categoría en dos filas).
3. **Comparar contra la versión `APPROVED` previa del mismo alcance exacto** (fuente, año fiscal, tipo de cronograma, categoría): se calculan `added`/`removed`/`modified_dates`, sin rechazar el CSV — es solo informativo (`diff_summary`).
4. **Vista previa**: nada se persiste como cronograma definitivo todavía; el `import_run` queda en `VALIDATED` (o `REJECTED` si hubo errores estructurales).
5. **Confirmar o rechazar**: confirmar crea la versión (`IMPORTED`) y sus entradas; rechazar marca el `import_run` como `REJECTED` sin crear versión. El CSV crudo se conserva siempre como evidencia (ver ADR-0012), incluso si se rechaza.
6. **Enviar a revisión y aprobar**: requiere acción humana explícita en cada paso; aprobar una versión reemplaza automáticamente (`SUPERSEDED`) la versión `APPROVED` previa del **mismo alcance exacto**, nunca de otro año o categoría (ver ADR-0011).

**Regla dura**: ningún vencimiento se resuelve desde una versión que no esté en estado `APPROVED`.

## Reglas de asociación cliente↔cronograma

- **Por últimos dígitos del RUC**, individuales o en grupo (`applicable_last_digits SMALLINT[]`, `NULL` = todos — ver ADR-0014).
- **Por "buen contribuyente" u otras categorías**, calculadas dinámicamente desde `client_tax_categories` a la fecha de referencia (ver ADR-0013), nunca desde `clients.is_good_taxpayer` (columna congelada del Bloque 1).
- **Excepciones** (`tax_schedule_exceptions`) siempre tienen prioridad sobre las entradas generales de la misma versión.
- **Cronogramas por obligación**: cada `tax_schedule_source` pertenece a una única obligación — nunca se asume que todas las obligaciones comparten el mismo cronograma.
- **Especificidad determinista, no ponderada** (ver ADR-0015): excepción > categoría específica > menos dígitos aplicables > `priority` explícito (solo si la fuente lo definió) como último desempate. Un empate real tras los cuatro criterios lanza un error explícito de ambigüedad (`AmbiguousScheduleRuleError`, HTTP 409) — el sistema nunca elige arbitrariamente entre dos reglas igualmente aplicables.

### Fecha de referencia para evaluar vigencia de categorías

`resolve_reference_date(tax_period)` usa el **último día calendario** del periodo tributario (`"2026-01"` → `2026-01-31`; `"2026"` → `2026-12-31`). Esta es una **convención del sistema**, no una regla oficial de SUNAT — se revisará si aparece una fuente oficial distinta (ver ADR-0013).

## Vista previa de vencimiento

`GET /tax-schedules/preview-due-date?source_id=&client_id=&tax_period=` resuelve, para un cliente y periodo concretos: la regla aplicada (excepción o entrada), el último dígito del RUC, la categoría exigida (si aplica), la fecha resultante, si hubo prórroga respecto a la fecha original, la fuente y versión utilizadas, y advertencias. Cada consulta se audita (`TAX_SCHEDULE_PREVIEW_QUERY`). **No genera declaraciones ni eventos de Google Calendar** — eso queda para un bloque posterior.

## Auditoría del motor de cronogramas

Se audita: creación de fuente, creación/confirmación/rechazo de una importación, envío a revisión, aprobación, rechazo, cancelación, y cada consulta de vista previa. Los errores de asociación (`AmbiguousScheduleRuleError`, `NoApplicableScheduleRuleError`, `NoApprovedVersionError`) se devuelven como respuestas HTTP explícitas (409/404), nunca se resuelven en silencio.

## Datos semilla

El catálogo de obligaciones (`backend/app/seeds/obligation_catalog_data.py`) sigue incluyendo solo nombre, código y periodicidad — sin fechas de vencimiento. Cualquier cronograma con fechas cargado en desarrollo proviene exclusivamente de un CSV subido manualmente por un humano a través del flujo descrito arriba, y debe estar claramente etiquetado como ficticio.
