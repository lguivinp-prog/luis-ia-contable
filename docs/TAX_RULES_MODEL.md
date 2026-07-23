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

Existe el **catálogo de obligaciones** (`obligation_catalog`, Bloque 1), el **motor de cronogramas tributarios versionado** completo (importación desde CSV, revisión, aprobación humana explícita, resolución de vencimientos), la **generación real de declaraciones** (individual y masiva, Bloque 3.1) y, desde Bloque 3.2, la **determinación y pago de impuestos** y los **honorarios profesionales** de cada declaración/cliente. **No hay integración real con SUNAT/SOL ni eventos de Google Calendar** — todo dato tributario sigue siendo ficticio y cargado manualmente.

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

## Determinación tributaria (Bloque 3.2, ADR-0038 a ADR-0043)

Cada declaración puede tener una **determinación inicial** (`ORIGINAL`, `sequence_number=1`) y cero o más **correcciones** (`CORRECTION`, `sequence_number` incremental) — nunca se edita una determinación en el lugar. Fórmula fija, calculada siempre server-side con `Decimal` (nunca aceptada del frontend):

```
total_payable    = max(gross_tax_amount - credits_applied, 0)
remaining_credit = max(credits_applied - gross_tax_amount, 0)
```

`tax_payment_status` se deriva de la suma de **todos** los pagos válidos de la declaración contra la determinación **vigente actual** (nunca contra la determinación que estaba vigente cuando cada pago se registró — ver ADR-0040):

| Condición | Estado |
|---|---|
| sin determinación vigente | `NOT_DETERMINED` |
| `remaining_credit > 0` | `CREDIT_BALANCE` (prioridad sobre lo demás) |
| `total_payable == 0` | `NO_TAX_DUE` |
| `total_pagado <= 0` | `PAYMENT_PENDING` |
| `0 < total_pagado < total_payable` | `PARTIALLY_PAID` |
| `total_pagado >= total_payable` | `PAID` |

Una corrección solo escribe una fila de historial de estado si el valor derivado realmente cambia (ADR-0039) — nunca "cambios" con `from_value == to_value`.

## Pagos tributarios: parcialidad, idempotencia, sobrepago, anulación (ADR-0041, ADR-0042, ADR-0043)

- **Pagos parciales o totales**: cualquier monto `> 0` es válido; el estado se recalcula tras cada pago/anulación.
- **Idempotencia**: cada pago lleva un `idempotency_key UUID` generado por el frontend; un reenvío con la misma clave y el mismo payload devuelve el pago ya existente (nunca lo duplica); la misma clave con un payload distinto devuelve HTTP 409 (ver ADR-0041).
- **Sobrepago**: si el pago excede el saldo pendiente, se exige `overpayment_confirmed=true` + `overpayment_reason` explícito; sin confirmación explícita, HTTP 409; con confirmación pero sin motivo, HTTP 422 (ver ADR-0043). Una corrección posterior que reduce el impuesto nunca modifica pagos ya registrados.
- **Anulación**: exige motivo explícito; nunca elimina la fila, marca `is_voided=true` + `voided_at`/`voided_by`/`void_reason`, y recalcula el estado derivado.
- **Concurrencia**: cada operación (determinar, corregir, pagar, anular) bloquea la fila de `declarations` con `SELECT ... FOR UPDATE` dentro de la transacción del router — dos operaciones concurrentes sobre la misma declaración se serializan, nunca pierden una escritura (ver ADR-0042, prueba con dos conexiones reales en `test_tax_payment_concurrency.py`).

## Honorarios profesionales (Bloque 3.2, ADR-0044 a ADR-0046)

Ciclo de vida completamente independiente del ciclo tributario (ninguno de los dos módulos importa al otro para escritura; los honorarios bloquean `professional_fee_charges`, nunca `declarations`):

1. **Acuerdo** (`professional_fee_agreements`): tarifa por tipo de servicio, con vigencia real. `supersede`/`cancel` cierran `valid_until` de verdad (nunca solo cambian `status`), liberando el rango para un acuerdo posterior no solapado (ADR-0044).
2. **Cargo** (`professional_fee_charges`): generado por acuerdo + periodo tributario, idempotente (`UNIQUE(tenant_id, agreement_id, tax_period_id)`). Estado persistido sin `OVERDUE` (ADR-0045); el riesgo de cobranza vencido se calcula en vivo (`compute_fee_collection_risk`), nunca se persiste.
3. **Pago**: mismas reglas de idempotencia/sobrepago/anulación que los pagos tributarios, pero contra el saldo del cargo, no de la declaración.
4. **Recibo** (`professional_receipts`): puede ser parcial, pero la suma de recibos `ISSUED` de un cargo nunca puede exceder su monto (ADR-0046).

## Datos semilla

El catálogo de obligaciones (`backend/app/seeds/obligation_catalog_data.py`) sigue incluyendo solo nombre, código y periodicidad — sin fechas de vencimiento. Cualquier cronograma con fechas cargado en desarrollo proviene exclusivamente de un CSV subido manualmente por un humano a través del flujo descrito arriba, y debe estar claramente etiquetado como ficticio.
